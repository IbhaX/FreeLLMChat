"""/v1/roleplay — persistent multi-character rooms."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import optional_user, require_gateway_key
from app.db import get_session
from app.models import RoleplayRoom, User
from app.services import chat_jobs
from app.services import roleplay as rp

router = APIRouter(prefix="/v1/roleplay", tags=["roleplay"],
                   dependencies=[Depends(require_gateway_key)])


class RoomIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    scenario: str = ""
    character_ids: list[int] = Field(default_factory=list, max_length=6)
    model: str = "auto"
    tools_enabled: bool = False


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


def _room_out(r: RoleplayRoom) -> dict[str, Any]:
    return {"id": r.id, "name": r.name, "scenario": r.scenario,
            "character_ids": r.character_ids, "model": r.model,
            "summary": r.summary or "", "mood": r.mood or "",
            "paused_ids": r.paused_ids or [],
            "director_notes": r.director_notes or "",
            "tools_enabled": bool(r.tools_enabled),
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
    room = RoleplayRoom(name=body.name.strip(), scenario=body.scenario,
                        character_ids=body.character_ids, model=body.model,
                        tools_enabled=body.tools_enabled,
                        owner_id=user.id if user else None, messages=[])
    session.add(room)
    await session.commit()
    return _room_out(room)


@router.get("/rooms/{rid}")
async def get_room(rid: int, session: AsyncSession = Depends(get_session)):
    room = await session.get(RoleplayRoom, rid)
    if not room:
        raise HTTPException(404, "room not found")
    return _room_out(room)


@router.delete("/rooms/{rid}")
async def delete_room(rid: int, session: AsyncSession = Depends(get_session),
                      user: User | None = Depends(optional_user)):
    room = await session.get(RoleplayRoom, rid)
    if not room:
        raise HTTPException(404, "room not found")
    if room.owner_id is not None and (not user or room.owner_id != user.id):
        raise HTTPException(403, "not your room")
    await session.delete(room)
    await session.commit()
    return {"ok": True}


@router.post("/rooms/{rid}/message")
async def send_message(rid: int, body: MessageIn, request: Request,
                       session: AsyncSession = Depends(get_session)):
    room = await session.get(RoleplayRoom, rid)
    if not room:
        raise HTTPException(404, "room not found")
    replies = await rp.room_turn(_gateway(request), session, room,
                                 body.text, speaker_filter=body.speaker)
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
                   session: AsyncSession = Depends(get_session)):
    """Append a beat and stream each character's reply (SSE freellm frames)."""
    room = await session.get(RoleplayRoom, rid)
    if not room:
        raise HTTPException(404, "room not found")
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
    return _stream_job(rid, agen)


@router.get("/rooms/{rid}/live")
async def turn_live(rid: int):
    job = chat_jobs.get(_job_key(rid))
    if not job:
        return {"running": False}
    return {"running": not job.done, "done": job.done,
            "error": job.error}


@router.get("/rooms/{rid}/live/stream")
async def turn_live_stream(rid: int):
    job = chat_jobs.get(_job_key(rid))
    if not job:
        raise HTTPException(404, "no turn in flight")
    return StreamingResponse(
        job.stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/rooms/{rid}/retry")
async def retry_turn(rid: int, body: RetryIn, request: Request,
                     session: AsyncSession = Depends(get_session)):
    """Regenerate one character turn — appends a swipe variant (SSE)."""
    room = await session.get(RoleplayRoom, rid)
    if not room:
        raise HTTPException(404, "room not found")
    from app.db import get_session_factory
    agen = rp.retry_stream(_gateway(request), get_session_factory(), rid,
                           message_id=body.message_id)
    return _stream_job(rid, agen)


@router.patch("/rooms/{rid}/messages/{mid}")
async def edit_message(rid: int, mid: str, body: EditIn,
                       session: AsyncSession = Depends(get_session)):
    room = await session.get(RoleplayRoom, rid)
    if not room:
        raise HTTPException(404, "room not found")
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
                         session: AsyncSession = Depends(get_session)):
    room = await session.get(RoleplayRoom, rid)
    if not room:
        raise HTTPException(404, "room not found")
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
                     session: AsyncSession = Depends(get_session)):
    room = await session.get(RoleplayRoom, rid)
    if not room:
        raise HTTPException(404, "room not found")
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(room, k, v)
    session.add(room)
    await session.commit()
    return _room_out(room)


@router.post("/rooms/{rid}/summary")
async def summarize(rid: int, request: Request,
                    session: AsyncSession = Depends(get_session)):
    room = await session.get(RoleplayRoom, rid)
    if not room:
        raise HTTPException(404, "room not found")
    from app.db import get_session_factory
    agen = rp.summarize_stream(_gateway(request), get_session_factory(), rid)
    job = chat_jobs.start(f"rp-sum-{rid}", agen)
    return StreamingResponse(
        job.stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
