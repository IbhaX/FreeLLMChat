"""/v1/roleplay — persistent multi-character rooms."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import optional_user, require_user, subject_for
from app.db import get_session
from app.models import RoleplayRoom, User
from app.services import chat_jobs
from app.services import roleplay as rp
from app.services import usage
from app.services import worlds as wd
from app.services.auth import plan_limits

router = APIRouter(prefix="/v1/roleplay", tags=["roleplay"],
                   dependencies=[Depends(require_user)])


class RoomIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    scenario: str = ""
    character_ids: list[int] = Field(default_factory=list, max_length=6)
    model: str = "auto"
    tools_enabled: bool = False
    world: dict[str, Any] | None = None


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    speaker: int | None = None  # character id; None = everyone replies


class TurnIn(BaseModel):
    """One beat on the stage: talk / narrate / ooc / whisper."""
    kind: str = "talk"
    text: str = Field(default="", max_length=8000)
    whisper_to: int | None = None
    advance: bool = True          # False = record the beat, no replies


class RetryIn(BaseModel):
    message_id: str


class EditIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


class RoomPatch(BaseModel):
    name: str | None = None
    scenario: str | None = None
    mood: str | None = None
    paused_ids: list[int] | None = None
    director_notes: str | None = None
    character_ids: list[int] | None = None
    model: str | None = None
    tools_enabled: bool | None = None
    world: dict[str, Any] | None = None


def _room_out(r: RoleplayRoom) -> dict[str, Any]:
    return {"id": r.id, "name": r.name, "scenario": r.scenario,
            "character_ids": r.character_ids, "model": r.model,
            "summary": r.summary or "", "mood": r.mood or "",
            "paused_ids": r.paused_ids or [],
            "director_notes": r.director_notes or "",
            "tools_enabled": bool(r.tools_enabled),
            "world": wd.normalize_world(r),
            "messages": r.messages, "updated_at": r.updated_at.isoformat()}


def _gateway(request: Request) -> Any:
    return request.app.state.gateway


@router.get("/rooms")
async def list_rooms(session: AsyncSession = Depends(get_session),
                     user: User | None = Depends(optional_user)):
    q = select(RoleplayRoom).order_by(RoleplayRoom.updated_at.desc())
    if user:
        q = q.where(or_(RoleplayRoom.owner_id == user.id,
                        RoleplayRoom.owner_id.is_(None)))
    rows = (await session.execute(q)).scalars().all()
    return {"rooms": [_room_out(r) for r in rows]}


@router.post("/rooms", status_code=201)
async def create_room(body: RoomIn,
                      session: AsyncSession = Depends(get_session),
                      user: User | None = Depends(optional_user)):
    if user is not None:
        lim = plan_limits(user.plan).get("rooms", 0)
        if lim > 0:
            n = (await session.execute(
                select(func.count(RoleplayRoom.id))
                .where(RoleplayRoom.owner_id == user.id))).scalar() or 0
            if n >= lim:
                raise HTTPException(
                    429, f"room limit reached for the {user.plan} plan "
                         f"({n}/{lim})")
    room = RoleplayRoom(name=body.name.strip(), scenario=body.scenario,
                        character_ids=body.character_ids, model=body.model,
                        tools_enabled=body.tools_enabled,
                        owner_id=user.id if user else None, messages=[],
                        world=body.world or wd.default_world(body.name.strip()))
    session.add(room)
    await session.commit()
    return _room_out(room)


async def _owned_room(session: AsyncSession, rid: int,
                      user: User | None) -> RoleplayRoom:
    """Fetch a room AND enforce ownership. A room owned by another user is
    indistinguishable from a missing one — 404, no existence leak. Rooms
    with no owner (pre-tenancy legacy) stay readable by anyone."""
    room = await session.get(RoleplayRoom, rid)
    if room is None or (room.owner_id is not None
                        and (user is None or room.owner_id != user.id)):
        raise HTTPException(404, "room not found")
    return room


@router.get("/rooms/{rid}")
async def get_room(rid: int, session: AsyncSession = Depends(get_session),
                   user: User | None = Depends(optional_user)):
    room = await _owned_room(session, rid, user)
    return _room_out(room)


@router.delete("/rooms/{rid}")
async def delete_room(rid: int, session: AsyncSession = Depends(get_session),
                      user: User | None = Depends(optional_user)):
    room = await _owned_room(session, rid, user)
    await session.delete(room)
    await session.commit()
    return {"ok": True}


@router.post("/rooms/{rid}/message")
async def send_message(rid: int, body: MessageIn, request: Request,
                       session: AsyncSession = Depends(get_session),
                       user: User | None = Depends(optional_user)):
    room = await _owned_room(session, rid, user)
    if user is not None:
        ok, reason = await usage.check_quota(
            session, subject_for(user), "chat", user.plan)
        if not ok:
            raise HTTPException(429, reason)
    from app.services import artifacts as _artifacts

    _artifacts.set_context(
        source="roleplay", owner_id=user.id if user else None)
    replies = await rp.room_turn(_gateway(request), session, room,
                                 body.text, speaker_filter=body.speaker)
    if user is not None:
        await usage.record(session, subject_for(user), "chat",
                           model=room.model or "")
    await session.commit()
    return {"replies": replies, "messages": room.messages}


# ---------- The Stage: streaming turns + director controls ----------


def _job_key(rid: int) -> str:
    return f"rp-{rid}"


def _stream_job(rid: int, agen: Any) -> StreamingResponse:
    job = chat_jobs.start(_job_key(rid), agen)
    return StreamingResponse(
        job.stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/rooms/{rid}/turn")
async def run_turn(rid: int, body: TurnIn, request: Request,
                   session: AsyncSession = Depends(get_session),
                   user: User | None = Depends(optional_user)):
    """Append a beat and stream each character's reply (SSE nova frames)."""
    room = await _owned_room(session, rid, user)
    _usage_ev_id = None
    if user is not None:
        ok, reason = await usage.check_quota(
            session, subject_for(user), "chat", user.plan)
        if not ok:
            raise HTTPException(429, reason)
        _uev = await usage.record(session, subject_for(user), "chat",
                                  model=room.model or "")
        _usage_ev_id = _uev.id if _uev is not None else None
    from app.services import artifacts as _artifacts

    _artifacts.set_context(
        source="roleplay", owner_id=user.id if user else None)
    kind = body.kind if body.kind in ("talk", "narrate", "ooc", "whisper") else "talk"
    if not body.text.strip():
        raise HTTPException(400, "text required")
    from app.db import get_session_factory
    factory = get_session_factory()
    agen = rp.turn_stream(_gateway(request), factory, rid, kind=kind,
                          text=body.text.strip(), whisper_to=body.whisper_to)
    if not body.advance:
        # record-only: consume just the 'beat' frame synchronously
        agen = rp.beat_only(factory, rid, kind=kind, text=body.text.strip(),
                            whisper_to=body.whisper_to)

    async def _metered(src: Any) -> Any:
        """Settle real billed tokens onto the UsageEvent row by scanning
        usage events embedded in the stream's nova frames."""
        tin = tout = 0
        try:
            async for chunk in src:
                try:
                    for part in chunk.split(b"data:"):
                        part = part.strip()
                        if not part or part == b"[DONE]":
                            continue
                        ev = (json.loads(part) or {}).get("nova") or {}
                        if ev.get("event") == "usage":
                            tin += int(ev.get("tokens_in") or 0)
                            tout += int(ev.get("tokens_out") or 0)
                except Exception:
                    pass
                yield chunk
        finally:
            await usage.settle_tokens(_usage_ev_id, tin, tout)

    return _stream_job(rid, _metered(agen))


@router.get("/rooms/{rid}/live")
async def turn_live(rid: int, session: AsyncSession = Depends(get_session),
                    user: User | None = Depends(optional_user)):
    await _owned_room(session, rid, user)
    job = chat_jobs.get(_job_key(rid))
    if not job:
        return {"running": False}
    return {"running": not job.done, "done": job.done,
            "error": job.error}


@router.delete("/rooms/{rid}/live")
async def turn_live_cancel(rid: int,
                           session: AsyncSession = Depends(get_session),
                           user: User | None = Depends(optional_user)):
    """Cancel the in-flight turn job (completed messages are preserved;
    the visible partial is finalized as interrupted server-side)."""
    await _owned_room(session, rid, user)
    job = chat_jobs.get(_job_key(rid))
    if job is None or job.done:
        return {"stopped": False}
    job.cancel()
    return {"stopped": True}


@router.get("/rooms/{rid}/live/stream")
async def turn_live_stream(rid: int,
                           session: AsyncSession = Depends(get_session),
                           user: User | None = Depends(optional_user)):
    await _owned_room(session, rid, user)
    job = chat_jobs.get(_job_key(rid))
    if not job:
        raise HTTPException(404, "no turn in flight")
    return StreamingResponse(
        job.stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/rooms/{rid}/retry")
async def retry_turn(rid: int, body: RetryIn, request: Request,
                     session: AsyncSession = Depends(get_session),
                     user: User | None = Depends(optional_user)):
    """Regenerate one character turn — appends a swipe variant (SSE)."""
    room = await _owned_room(session, rid, user)
    from app.db import get_session_factory
    agen = rp.retry_stream(_gateway(request), get_session_factory(), rid,
                           message_id=body.message_id)
    return _stream_job(rid, agen)


@router.patch("/rooms/{rid}/messages/{mid}")
async def edit_message(rid: int, mid: str, body: EditIn,
                       session: AsyncSession = Depends(get_session),
                       user: User | None = Depends(optional_user)):
    room = await _owned_room(session, rid, user)
    rp.normalize_room(room)
    msg = next((m for m in room.messages or [] if m.get("id") == mid), None)
    if not msg:
        raise HTTPException(404, "message not found")
    room.messages = [
        {**m, "text": body.text.strip()} if m.get("id") == mid else m
        for m in room.messages or []
    ]
    session.add(room)
    await session.commit()
    msg = next(m for m in room.messages if m.get("id") == mid)
    return {"ok": True, "message": msg}


@router.delete("/rooms/{rid}/messages/{mid}")
async def delete_message(rid: int, mid: str,
                         session: AsyncSession = Depends(get_session),
                         user: User | None = Depends(optional_user)):
    room = await _owned_room(session, rid, user)
    rp.normalize_room(room)
    before = len(room.messages or [])
    room.messages = [m for m in room.messages or [] if m.get("id") != mid]
    if len(room.messages) == before:
        raise HTTPException(404, "message not found")
    session.add(room)
    await session.commit()
    return {"ok": True, "messages": room.messages}


@router.patch("/rooms/{rid}")
async def patch_room(rid: int, body: RoomPatch,
                     session: AsyncSession = Depends(get_session),
                     user: User | None = Depends(optional_user)):
    room = await _owned_room(session, rid, user)
    data = body.model_dump(exclude_unset=True)
    world_patch = data.pop("world", None)
    if world_patch is not None:
        merged = wd.normalize_world(room)
        for k, v in world_patch.items():
            if k in ("events", "relationships", "rules") and not isinstance(v, list):
                continue
            merged[k] = v
        room.world = merged
    for k, v in data.items():
        setattr(room, k, v)
    session.add(room)
    await session.commit()
    return _room_out(room)


@router.post("/rooms/{rid}/summary")
async def summarize(rid: int, request: Request,
                    session: AsyncSession = Depends(get_session),
                    user: User | None = Depends(optional_user)):
    room = await _owned_room(session, rid, user)
    from app.db import get_session_factory
    agen = rp.summarize_stream(_gateway(request), get_session_factory(), rid)
    job = chat_jobs.start(f"rp-sum-{rid}", agen)
    return StreamingResponse(
        job.stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------- Worlds: catalogue, entry, choices, blueprint ----------


class BlueprintIn(BaseModel):
    concept: str = Field(default="", max_length=1200)
    genre: str = Field(default="", max_length=60)
    mood: str = Field(default="", max_length=60)
    cast_size: int = Field(default=3, ge=1, le=5)


class WorldEventIn(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    kind: str = Field(default="note", max_length=24)


class WorldEnterIn(BaseModel):
    blueprint: dict[str, Any] | None = None   # studio preview → custom world


@router.get("/worlds")
async def list_worlds():
    """Featured world catalogue for the Worlds landing page."""
    return {"worlds": wd.catalog()}


@router.post("/worlds/blueprint")
async def world_blueprint(body: BlueprintIn, request: Request):
    """AI-assisted world creation — returns a previewable blueprint."""
    bp = await wd.generate_blueprint(
        _gateway(request), body.concept, body.genre, body.mood,
        cast_size=body.cast_size)
    return {"blueprint": bp}


@router.post("/worlds/{wid}/enter", status_code=201)
async def enter_world(wid: str, body: WorldEnterIn,
                      session: AsyncSession = Depends(get_session),
                      user: User | None = Depends(optional_user)):
    """Enter a catalogue world — or materialize a studio blueprint."""
    uid = user.id if user else None
    if wid == "custom":
        bp = body.blueprint or {}
        seeds = (bp.get("characters") or [])[:6]
        char_ids = await wd._ensure_characters(session, seeds, uid)
        world = wd.default_world(str(bp.get("title") or "New World")[:90])
        for k in ("premise", "location", "world_time", "rules", "title"):
            if bp.get(k):
                world[k] = bp[k]
        wd.log_event(world,
                     f"You arrive — {world['location'] or world['title']}",
                     kind="arrival")
        room = RoleplayRoom(
            name=world["title"], scenario=str(bp.get("scenario") or ""),
            character_ids=char_ids, model="auto", owner_id=uid,
            messages=[], world=world)
        session.add(room)
    else:
        room = await wd.enter_world(session, wid, uid)
        if room is None:
            raise HTTPException(404, "unknown world")
    await session.commit()
    return _room_out(room)


@router.post("/rooms/{rid}/choices")
async def room_choices(rid: int, request: Request,
                       session: AsyncSession = Depends(get_session),
                       user: User | None = Depends(optional_user)):
    """Contextual 'what do you do' options for the current beat."""
    room = await _owned_room(session, rid, user)
    cast = await rp.room_characters(session, room)
    choices = await wd.suggest_choices(_gateway(request), room, cast)
    return {"choices": choices}


@router.post("/rooms/{rid}/world/events")
async def world_log_event(rid: int, body: WorldEventIn,
                          session: AsyncSession = Depends(get_session),
                          user: User | None = Depends(optional_user)):
    """Director logs a story event into the world's curated event feed."""
    room = await _owned_room(session, rid, user)
    world = wd.normalize_world(room)
    wd.log_event(world, body.text, kind=body.kind[:24])
    room.world = world
    session.add(room)
    await session.commit()
    return {"ok": True, "world": world}
