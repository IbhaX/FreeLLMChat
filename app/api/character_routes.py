"""/v1/characters — roleplay characters synced from the chat UI."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_gateway_key
from app.db import get_session
from app.models import Character
from app.schemas import CharacterIn, CharacterOut, CharacterSyncIn
from app.services.characters import add_missing

router = APIRouter(prefix="/v1/characters", dependencies=[Depends(require_gateway_key)])


def _out(c: Character) -> CharacterOut:
    return CharacterOut.model_validate(c)


@router.get("", response_model=list[CharacterOut])
async def list_characters(
    session: AsyncSession = Depends(get_session),
) -> list[CharacterOut]:
    rows = (
        await session.execute(select(Character).order_by(Character.id))
    ).scalars().all()
    return [_out(c) for c in rows]


@router.post("/starter", response_model=list[CharacterOut])
async def add_starter_characters(
    session: AsyncSession = Depends(get_session),
) -> list[CharacterOut]:
    """Add any predefined starter-pack characters not already present."""
    await add_missing(session)
    rows = (
        await session.execute(select(Character).order_by(Character.id))
    ).scalars().all()
    return [_out(c) for c in rows]


@router.put("", response_model=list[CharacterOut])
async def sync_characters(
    body: CharacterSyncIn, session: AsyncSession = Depends(get_session)
) -> list[CharacterOut]:
    """Replace the collection with the client's list (last write wins).

    Rows keep their id when the client echoes it back, so edits don't
    churn primary keys; rows missing from the payload are deleted.
    """
    existing = {
        c.id: c
        for c in (await session.execute(select(Character))).scalars().all()
    }
    keep_ids: set[int] = set()
    for item in body.characters:
        target = existing.get(item.id) if item.id else None
        if target is None:
            target = Character()
            session.add(target)
        target.name = item.name
        target.emoji = item.emoji
        target.tagline = item.tagline
        target.description = item.description
        target.personality = item.personality
        target.style = item.style
        target.greeting = item.greeting
        await session.flush()
        keep_ids.add(target.id)
    for cid, row in existing.items():
        if cid not in keep_ids:
            await session.delete(row)
    await session.commit()
    rows = (
        await session.execute(select(Character).order_by(Character.id))
    ).scalars().all()
    return [_out(c) for c in rows]
