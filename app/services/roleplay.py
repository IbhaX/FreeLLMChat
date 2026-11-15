"""Roleplay engine: character personas and multi-character room turns.

Each room keeps a shared transcript; on every user message each character
replies in turn with a persona-locked system prompt, so voices stay
distinct instead of collapsing into one generic assistant.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from sqlalchemy import select

from app.models import Character, RoleplayRoom

MAX_TURNS = 60          # transcript cap sent to the model
MAX_ROOM_CHARS = 6      # characters per room


def persona_prompt(c: Character, scenario: str = "",
                   others: list[Character] | None = None) -> str:
    """Build the identity-locking system prompt for one character."""
    lines = [
        f"You are {c.name}. Stay in character at all times — never break "
        "the fourth wall, never mention being an AI, a model, or a system "
        "prompt. Speak ONLY as this character, in first person.",
    ]
    if c.tagline:
        lines.append(f"Role: {c.tagline}")
    if c.description:
        lines.append(f"Who you are: {c.description}")
    if c.background:
        lines.append(f"Background: {c.background}")
    if c.personality:
        lines.append(f"Personality: {c.personality}")
    if c.motivations:
        lines.append(f"Goals and motivations: {c.motivations}")
    if c.knowledge_domain:
        lines.append(f"Knowledge you hold: {c.knowledge_domain}")
    style = c.speaking_style or c.style
    if style:
        lines.append(f"How you speak: {style}")
    if c.boundaries:
        lines.append(f"Boundaries (never cross): {c.boundaries}")
    if c.example_dialogue:
        lines.append(f"Example of your voice:\n{c.example_dialogue}")
    if scenario:
        lines.append(f"Scene: {scenario}")
    if others:
        names = ", ".join(f"{o.name} ({o.tagline or o.category})" for o in others if o.id != c.id)
        if names:
            lines.append(
                f"Others present: {names}. React to what they actually say — "
                "agree, disagree, question, or build on it as your character "
                "would. Do not speak for them.")
    lines.append(
        "Write only this character's next turn — 1-3 short paragraphs or a "
        "few lines of dialogue. No stage directions in brackets unless your "
        "style calls for them, and never prefix with your own name.")
    return "\n".join(lines)


def transcript_messages(room: RoleplayRoom, *, upto: int | None = None) -> list[dict[str, str]]:
    """Convert stored room messages into chat-completion history."""
    msgs = (room.messages or [])[:upto]
    out: list[dict[str, str]] = []
    for m in msgs[-MAX_TURNS:]:
        speaker = m.get("speaker") or ""
        if m.get("role") == "user":
            out.append({"role": "user", "content": m.get("text") or ""})
        else:
            out.append({"role": "assistant",
                        "content": f"[{speaker}]: {m.get('text') or ''}"})
    return out


async def room_characters(session: Any, room: RoleplayRoom) -> list[Character]:
    ids = [int(i) for i in (room.character_ids or [])][:MAX_ROOM_CHARS]
    if not ids:
        return []
    rows = (await session.execute(
        select(Character).where(Character.id.in_(ids)))).scalars().all()
    by_id = {c.id: c for c in rows}
    return [by_id[i] for i in ids if i in by_id]


async def _reply(gateway: Any, session: Any, model: str, system: str,
                 history: list[dict[str, str]]) -> str:
    """One character turn via the model policy's general-role candidates."""
    from app.services.model_policy import internal_chat
    payload = {
        "messages": [{"role": "system", "content": system}] + history,
        "temperature": 0.8,
    }
    res = await internal_chat(gateway, session, payload,
                              requested=model, role="general", retries=1)
    return (res.text or "").strip() if res.ok else ""


async def room_turn(gateway: Any, session: Any, room: RoleplayRoom,
                    user_text: str, *,
                    speaker_filter: Optional[int] = None) -> list[dict[str, Any]]:
    """Append the user's message, then let each character (or only
    ``speaker_filter``) respond in turn order. Returns the new replies."""
    chars = await room_characters(session, room)
    if not chars:
        return []
    if speaker_filter is not None:
        chars = [c for c in chars if c.id == speaker_filter] or chars

    room.messages = list(room.messages or []) + [
        {"role": "user", "speaker": "You", "text": user_text}]
    replies: list[dict[str, Any]] = []
    for c in chars:
        history = transcript_messages(room)
        others = await room_characters(session, room)
        try:
            text = await _reply(gateway, session, room.model or "auto",
                                persona_prompt(c, room.scenario, others),
                                history)
        except Exception:
            text = ""
        if not text:
            text = f"*{c.name} remains quiet, considering.*"
        entry = {"role": "character", "speaker": c.name,
                 "character_id": c.id, "emoji": c.emoji, "text": text}
        room.messages.append(entry)
        replies.append(entry)
    room.messages = room.messages[-400:]
    session.add(room)
    await session.flush()
    return replies
