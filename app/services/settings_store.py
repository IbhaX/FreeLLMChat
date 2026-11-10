"""Shared accessor for the user-defined custom instructions setting."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

CUSTOM_INSTRUCTIONS_KEY = "custom_instructions"


async def get_custom_instructions(session: AsyncSession) -> str:
    """Return the user's custom instructions text ('' when unset). Never raises."""
    try:
        from app.models import Setting

        row = (
            await session.execute(
                select(Setting).where(Setting.key == CUSTOM_INSTRUCTIONS_KEY)
            )
        ).scalar_one_or_none()
        return (row.value if row else "").strip()
    except Exception:
        return ""


def custom_instructions_block(text: str) -> str:
    """Format custom instructions as a system-prompt section."""
    return f"## USER CUSTOM INSTRUCTIONS\n{text.strip()}"
