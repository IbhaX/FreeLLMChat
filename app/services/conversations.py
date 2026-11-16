"""Conversation persistence — shared CRUD for /v1 and /admin routes.

Conversations are the user's chat transcripts; keeping one code path
means the user-facing sync and the admin views can never drift apart.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation


async def list_summaries(session: AsyncSession,
                         limit: int = 200) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(Conversation).order_by(Conversation.updated_at.desc())
            .limit(limit))
    ).scalars().all()
    return [
        {"client_id": c.client_id, "name": c.name,
         "updated_at": c.updated_at.isoformat() if c.updated_at else None}
        for c in rows
    ]


async def upsert(session: AsyncSession, client_id: str, name: str,
                 messages: list[dict[str, Any]]) -> Conversation:
    conv = (
        await session.execute(
            select(Conversation).where(Conversation.client_id == client_id))
    ).scalar_one_or_none()
    if conv is None:
        conv = Conversation(client_id=client_id[:32])
        session.add(conv)
    conv.name = (name or "")[:200]
    conv.messages = messages
    conv.updated_at = datetime.now(UTC)
    await session.commit()
    return conv


async def get(session: AsyncSession,
              client_id: str) -> Conversation | None:
    return (
        await session.execute(
            select(Conversation).where(Conversation.client_id == client_id))
    ).scalar_one_or_none()


async def remove(session: AsyncSession, client_id: str) -> bool:
    conv = await get(session, client_id)
    if conv is None:
        return False
    await session.delete(conv)
    await session.commit()
    return True
