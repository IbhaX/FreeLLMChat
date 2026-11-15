"""/v1/roleplay — persistent multi-character rooms."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import optional_user, require_gateway_key
from app.db import get_session
from app.models import RoleplayRoom, User
from app.services import roleplay as rp

router = APIRouter(prefix="/v1/roleplay", tags=["roleplay"],
                   dependencies=[Depends(require_gateway_key)])


class RoomIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    scenario: str = ""
    character_ids: list[int] = Field(default_factory=list, max_length=6)
    model: str = "auto"


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    speaker: Optional[int] = None  # character id; None = everyone replies


def _room_out(r: RoleplayRoom) -> dict[str, Any]:
    return {"id": r.id, "name": r.name, "scenario": r.scenario,
            "character_ids": r.character_ids, "model": r.model,
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
