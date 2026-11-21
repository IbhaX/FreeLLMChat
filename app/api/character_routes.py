"""/v1/characters — roleplay characters synced from the chat UI."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import optional_user, require_user
from app.db import get_session
from app.models import Character, User
from app.schemas import CharacterIn, CharacterOut, CharacterSyncIn
from app.services.characters import add_missing

router = APIRouter(
    prefix="/v1/characters",
    dependencies=[Depends(require_user)])


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


_EXT_FIELDS = ("background", "knowledge_domain", "motivations", "boundaries",
               "example_dialogue", "speaking_style", "category", "tags",
               "visibility", "avatar_url")


def _apply(item: CharacterIn, target: Character) -> None:
    target.name = item.name
    target.emoji = item.emoji
    target.tagline = item.tagline
    target.description = item.description
    target.personality = item.personality
    target.style = item.style
    target.greeting = item.greeting
    for f in _EXT_FIELDS:
        setattr(target, f, getattr(item, f, getattr(target, f)))


@router.post("", response_model=CharacterOut, status_code=201)
async def create_character(
    body: CharacterIn,
    session: AsyncSession = Depends(get_session),
    user: "User | None" = Depends(optional_user),
) -> CharacterOut:
    """Create one character (REST). The bulk PUT below stays for UI sync."""
    c = Character(creator_id=user.id if user else None)
    _apply(body, c)
    session.add(c)
    await session.commit()
    return _out(c)


@router.get("/{cid}", response_model=CharacterOut)
async def get_character(cid: int, session: AsyncSession = Depends(get_session)) -> CharacterOut:
    c = await session.get(Character, cid)
    if not c:
        raise HTTPException(404, "character not found")
    return _out(c)


@router.patch("/{cid}", response_model=CharacterOut)
async def update_character(
    cid: int, body: CharacterIn,
    session: AsyncSession = Depends(get_session),
    user: "User | None" = Depends(optional_user),
) -> CharacterOut:
    c = await session.get(Character, cid)
    if not c:
        raise HTTPException(404, "character not found")
    if c.creator_id is not None and (not user or c.creator_id != user.id):
        raise HTTPException(403, "not your character")
    _apply(body, c)
    await session.commit()
    return _out(c)


@router.delete("/{cid}")
async def delete_character(
    cid: int,
    session: AsyncSession = Depends(get_session),
    user: "User | None" = Depends(optional_user),
) -> dict[str, bool]:
    c = await session.get(Character, cid)
    if not c:
        raise HTTPException(404, "character not found")
    if c.creator_id is not None and (not user or c.creator_id != user.id):
        raise HTTPException(403, "not your character")
    await session.delete(c)
    await session.commit()
    return {"ok": True}


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
        _apply(item, target)
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
