"""/v1/skills/manage — custom (user-defined) skills CRUD."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import optional_user, require_gateway_key, require_user
from app.db import get_session
from app.models import CustomSkill, User
from app.services.skills import load_custom_skills

router = APIRouter(prefix="/v1/skills/manage", tags=["skills"],
                   dependencies=[Depends(require_gateway_key), Depends(require_user)])


class SkillIn(BaseModel):
    name: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9_]+$")
    description: str = Field(min_length=1, max_length=500)
    instructions: str = Field(min_length=1)
    tools: list[str] = Field(default_factory=list)
    enabled: bool = True


def _out(s: CustomSkill) -> dict[str, Any]:
    return {"id": s.id, "name": s.name, "description": s.description,
            "instructions": s.instructions, "tools": s.tools,
            "version": s.version, "enabled": s.enabled,
            "owner_id": s.owner_id, "custom": True,
            "updated_at": s.updated_at.isoformat()}


@router.get("")
async def list_custom(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(
        select(CustomSkill).order_by(CustomSkill.name))).scalars().all()
    return {"skills": [_out(s) for s in rows]}


@router.post("", status_code=201)
async def create_skill(body: SkillIn,
                       session: AsyncSession = Depends(get_session),
                       user: User | None = Depends(optional_user)):
    existing = (await session.execute(
        select(CustomSkill).where(CustomSkill.name == body.name))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(409, "a custom skill with that name exists")
    s = CustomSkill(name=body.name, description=body.description,
                    instructions=body.instructions, tools=body.tools,
                    enabled=body.enabled,
                    owner_id=user.id if user else None)
    session.add(s)
    await session.commit()
    await load_custom_skills(session)
    return _out(s)


@router.patch("/{sid}")
async def update_skill(sid: int, body: SkillIn,
                       session: AsyncSession = Depends(get_session),
                       user: User | None = Depends(optional_user)):
    s = await session.get(CustomSkill, sid)
    if not s:
        raise HTTPException(404, "skill not found")
    if s.owner_id is not None and (not user or s.owner_id != user.id):
        raise HTTPException(403, "not your skill")
    s.name = body.name
    s.description = body.description
    s.instructions = body.instructions
    s.tools = body.tools
    s.enabled = body.enabled
    s.version += 1
    await session.commit()
    await load_custom_skills(session)
    return _out(s)


@router.delete("/{sid}")
async def delete_skill(sid: int, session: AsyncSession = Depends(get_session),
                       user: User | None = Depends(optional_user)):
    s = await session.get(CustomSkill, sid)
    if not s:
        raise HTTPException(404, "skill not found")
    if s.owner_id is not None and (not user or s.owner_id != user.id):
        raise HTTPException(403, "not your skill")
    await session.delete(s)
    await session.commit()
    await load_custom_skills(session)
    return {"ok": True}
