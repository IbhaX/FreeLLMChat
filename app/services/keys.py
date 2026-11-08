"""Key persistence helpers shared by admin routes and seeding."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crypto import encrypt_key, key_hint
from app.models import ApiKey, Provider


async def add_key(
    session: AsyncSession,
    provider: Provider,
    raw_key: str,
    *,
    label: str = "",
    quota_group: str | None = None,
    priority: int = 100,
    weight: int = 1,
    notes: str | None = None,
) -> ApiKey | None:
    """Insert a key; returns None if the same hint already exists for the provider."""
    hint = key_hint(raw_key)
    dup = (
        await session.execute(
            select(ApiKey).where(
                ApiKey.provider_id == provider.id, ApiKey.key_hint == hint
            )
        )
    ).scalars().first()
    if dup is not None:
        return None
    key = ApiKey(
        provider_id=provider.id,
        label=label or f"key-{hint}",
        encrypted_key=encrypt_key(raw_key),
        key_hint=hint,
        quota_group=quota_group,
        priority=priority,
        weight=weight,
        notes=notes,
    )
    session.add(key)
    await session.flush()
    return key
