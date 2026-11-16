"""Roleplay engine: character personas and multi-character room turns.

Each room keeps a shared transcript; on every user message each character
replies in turn with a persona-locked system prompt, so voices stay
distinct instead of collapsing into one generic assistant.

The Stage adds message kinds (dialogue/narration/ooc/whisper), per-message
ids and reply variants, streaming turn events, scene mood/director notes,
and transcript compaction via ``room.summary``.
"""

from __future__ import annotations

import json
import re
import secrets
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import select

from app.models import Character, RoleplayRoom

MAX_TURNS = 60          # transcript cap sent to the model
MAX_ROOM_CHARS = 6      # characters per room
MAX_STORED = 400        # persisted transcript cap
SUMMARY_EVERY = 40      # re-summarize after this many new messages


def _mid() -> str:
    return secrets.token_hex(4)


def _sse(ev: dict[str, Any]) -> bytes:
    return f"data: {json.dumps({'freellm': ev}, ensure_ascii=False)}\n\n".encode()


def normalize_room(room: RoleplayRoom) -> None:
    """Backfill ids/kinds on legacy messages so edit/undo/swipe work."""
    for m in room.messages or []:
        m.setdefault("id", _mid())
        if "kind" not in m:
            m["kind"] = "user" if m.get("role") == "user" else "dialogue"
        m.setdefault("variants", [])


def persona_prompt(c: Character, scenario: str = "",
                   others: list[Character] | None = None,
                   *, mood: str = "", director_notes: str = "",
                   summary: str = "", tools: bool = False) -> str:
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
    if summary:
        lines.append(f"Earlier in the story (recap): {summary}")
    if mood:
        lines.append(f"Scene mood: {mood} — let it color your tone.")
    if others:
        names = ", ".join(f"{o.name} ({o.tagline or o.category})" for o in others if o.id != c.id)
        if names:
            lines.append(
                f"Others present: {names}. React to what they actually say — "
                "agree, disagree, question, or build on it as your character "
                "would. Do not speak for them.")
    if director_notes:
        lines.append(
            "Stage direction from the player (treat as private guidance, "
            f"never quote it): {director_notes}")
    if tools:
        lines.append(
            "You may use the web_search and http_get tools to look up "
            "real-world facts the scene calls for — dates, places, lore, "
            "current events. Search when it genuinely helps; weave what "
            "you find into your in-character speech, and never mention "
            "tools, searches, or sources.")
    lines.append(
        "Write only this character's next turn — 1-3 short paragraphs or a "
        "few lines of dialogue. No stage directions in brackets unless your "
        "style calls for them, and never prefix with your own name. "
        "Lines in ((double parentheses)) are out-of-character direction — "
        "obey them but never speak them.")
    return "\n".join(lines)


def transcript_messages(room: RoleplayRoom, *, upto: int | None = None,
                        for_char: int | None = None) -> list[dict[str, str]]:
    """Convert stored room messages into chat-completion history.

    ``for_char`` hides whispers that were not addressed to that character.
    """
    msgs = (room.messages or [])[:upto]
    out: list[dict[str, str]] = []
    for m in msgs[-MAX_TURNS:]:
        kind = m.get("kind") or "dialogue"
        speaker = m.get("speaker") or ""
        text = m.get("text") or ""
        if kind == "whisper":
            if for_char is not None and m.get("whisper_to") != for_char:
                continue
            out.append({"role": "user",
                        "content": f"(whispered only to you) {text}"})
        elif kind == "ooc":
            out.append({"role": "user",
                        "content": f"((out of character direction: {text}))"})
        elif kind == "narration":
            out.append({"role": "assistant",
                        "content": f"[Narrator]: {text}"})
        elif m.get("role") == "user":
            out.append({"role": "user", "content": text})
        else:
            out.append({"role": "assistant",
                        "content": f"[{speaker}]: {text}"})
    return out


async def room_characters(session: Any, room: RoleplayRoom) -> list[Character]:
    ids = [int(i) for i in (room.character_ids or [])][:MAX_ROOM_CHARS]
    if not ids:
        return []
    rows = (await session.execute(
        select(Character).where(Character.id.in_(ids)))).scalars().all()
    by_id = {c.id: c for c in rows}
    return [by_id[i] for i in ids if i in by_id]


def clean_turn_text(text: str, name: str) -> str:
    """Strip common persona breaks from a character turn."""
    t = text.strip()
    # "[Name]: ..." or "Name: ..." self-prefix
    t = re.sub(rf"^\[?{re.escape(name)}\]?\s*:\s*", "", t)
    # leading "As an AI …" disclaimer line
    t = re.sub(r"^(As an AI\b[^\n]*\n+)", "", t, flags=re.I)
    return t.strip()


async def _reply(gateway: Any, session: Any, model: str, system: str,
                 history: list[dict[str, str]], *,
                 tools: bool = False) -> str:
    """One character turn via the model policy's general-role candidates."""
    if tools:
        from app.services.tools import run_tool_loop
        payload: dict[str, Any] = {
            "model": model or "auto",
            "messages": [{"role": "system", "content": system}] + history,
            "temperature": 0.8,
            "stream": False,
        }
        try:
            result = await run_tool_loop(gateway, session, payload,
                                         ROLEPLAY_TOOLS, max_rounds=3,
                                         call_timeout=120.0)
            msg = ((result.get("choices") or [{}])[0]).get("message", {})
            content = msg.get("content")
            return content.strip() if isinstance(content, str) else ""
        except Exception:
            return ""
    from app.services.model_policy import internal_chat
    payload = {
        "messages": [{"role": "system", "content": system}] + history,
        "temperature": 0.8,
    }
    res = await internal_chat(gateway, session, payload,
                              requested=model, role="general", retries=1)
    return (res.text or "").strip() if res.ok else ""


def _find_msg(room: RoleplayRoom, mid: str) -> dict[str, Any] | None:
    return next((m for m in room.messages or [] if m.get("id") == mid), None)


MAX_COMMENTERS = 3   # characters that may add a brief aside after the reply


def _is_pass(text: str) -> bool:
    return text.strip().upper().startswith("PASS")


async def _pick_responder(
    gateway: Any, session: Any, room: RoleplayRoom,
    live: list[Character], text: str, emit: Any = None,
) -> Character:
    """Choose the single character the player's beat is addressed to.

    Order: explicit name mention → only live character → a tiny router
    call (best effort) → least-recently-spoke rotation, so every cast
    member gets the stage over time.
    """
    for c in live:
        first = (c.name or "").split()[0] if c.name else ""
        if first and re.search(rf"\b{re.escape(first)}\b", text, re.I):
            return c
    if len(live) == 1:
        return live[0]
    try:
        cast = "\n".join(
            f"{c.id}: {c.name} — {c.tagline or c.category or ''}"
            for c in live)
        payload = {"messages": [
            {"role": "system", "content":
             "You direct a roleplay scene. Given the cast and the player's "
             "latest line, reply with ONLY the id of the single character "
             "who should respond first.\nCast:\n" + cast},
            {"role": "user", "content": text}],
            "temperature": 0}
        from app.services.model_policy import internal_chat
        res = await internal_chat(gateway, session, payload,
                                  requested=room.model or "auto",
                                  role="general", retries=0)
        if res.ok and res.text:
            m = re.search(r"\d+", res.text)
            pick = next((c for c in live if m and c.id == int(m.group())),
                        None)
            if pick:
                return pick
    except Exception:
        pass
    last_spoke: dict[int, int] = {}
    for i, m in enumerate(room.messages or []):
        if m.get("role") == "character":
            last_spoke[int(m.get("character_id") or 0)] = i
    return min(live, key=lambda c: last_spoke.get(c.id, -1))


ROLEPLAY_TOOLS = ["web_search", "http_get"]


async def _char_reply_stream(
    gateway: Any, session: Any, model: str, system: str,
    history: list[dict[str, str]], emit: Any = None,
    tools: bool = False,
) -> AsyncIterator[str]:
    """Yield streamed delta text for one character turn; falls back to a
    single buffered reply when streaming fails before any token.

    With ``tools`` on, the turn goes through the non-streaming tool loop
    (web_search/http_get) instead — empty heartbeat yields keep the
    caller draining queued tool-activity events while the loop runs,
    and the finished reply arrives as one final delta.
    """
    if tools:
        import asyncio

        from app.services.tools import run_tool_loop
        payload: dict[str, Any] = {
            "model": model or "auto",
            "messages": [{"role": "system", "content": system}] + history,
            "temperature": 0.8,
            "stream": False,
        }
        task = asyncio.ensure_future(run_tool_loop(
            gateway, session, payload, ROLEPLAY_TOOLS, emit=emit,
            max_rounds=3, call_timeout=120.0))
        while not task.done():
            await asyncio.sleep(0.3)
            yield ""
        try:
            result = task.result()
        except Exception:
            result = {}
        msg = ((result.get("choices") or [{}])[0]).get("message", {})
        content = msg.get("content")
        text = content if isinstance(content, str) else ""
        if text.strip():
            yield text.strip()
        return
    payload: dict[str, Any] = {
        "model": model or "auto",
        "messages": [{"role": "system", "content": system}] + history,
        "temperature": 0.8,
        "stream": True,
    }
    got_any = False
    try:
        stream, _p, _k, _m = await gateway.chat_stream(
            session, payload, on_event=emit)
        async for chunk in stream:
            for line in chunk.decode("utf-8", "ignore").splitlines():
                line = line.strip()
                if not line.startswith("data:") or line == "data: [DONE]":
                    continue
                try:
                    j = json.loads(line[5:].strip())
                except Exception:
                    continue
                delta = ((j.get("choices") or [{}])[0]
                         .get("delta") or {}).get("content") or ""
                if delta:
                    got_any = True
                    yield delta
    except Exception:
        if got_any:
            return
    if not got_any:
        text = await _reply(gateway, session, model, system, history)
        if text:
            yield text


async def turn_stream(
    gateway: Any, session_factory: Any, room_id: int, *,
    kind: str, text: str, whisper_to: int | None = None,
    emit: Any = None,
) -> AsyncIterator[bytes]:
    """Append the player's beat, then stream each character's reply.

    ``kind``: talk | narrate | ooc | whisper. ``emit`` receives gateway
    activity dicts (usage etc.) forwarded as freellm frames. Owns its DB
    session so it can run detached as a chat_jobs job.
    """
    import asyncio

    from app.services.agents import _TOOL_BLOB_FALLBACK, _looks_like_tool_blob
    evq: asyncio.Queue = asyncio.Queue()
    emit = emit or evq.put_nowait
    async with session_factory() as session:
        room = await session.get(RoleplayRoom, room_id)
        if not room:
            yield _sse({"event": "error", "text": "room not found"})
            return
        normalize_room(room)
        chars = await room_characters(session, room)
        paused = set(room.paused_ids or [])
        live = [c for c in chars if c.id not in paused]
        if kind == "whisper" and whisper_to is not None:
            live = [c for c in live if c.id == whisper_to]

        # the player's beat enters canon (except pure ooc, which only steers)
        role = {"narrate": "narrator", "ooc": "user"}.get(kind, "user")
        entry: dict[str, Any] = {
            "id": _mid(), "role": role, "kind": kind,
            "speaker": {"narrate": "Narrator",
                        "ooc": "You (OOC)"}.get(kind, "You"),
            "text": text, "variants": [],
        }
        if kind == "whisper":
            entry["whisper_to"] = whisper_to
        room.messages = list(room.messages or []) + [entry]
        session.add(room)
        await session.commit()
        yield _sse({"event": "beat", "message": entry})

        if not live or not chars:
            yield _sse({"event": "turn_done", "messages": room.messages})
            yield b"data: [DONE]\n\n"
            return

        # one responder takes the stage; the rest may add short asides
        responder = await _pick_responder(gateway, session, room, live,
                                          text)
        order = [responder] + [c for c in live
                               if c.id != responder.id][:MAX_COMMENTERS]
        yield _sse({"event": "note",
                    "text": f"🎭 {responder.name} responds"})
        yield _sse({"event": "turn_queue", "order": [c.id for c in order],
                    "cast": [{"id": c.id, "name": c.name, "emoji": c.emoji}
                             for c in order]})
        others = await room_characters(session, room)
        empty_scene = not any(
            m.get("role") == "character" for m in room.messages[:-1])
        for idx, c in enumerate(order):
            reacting = idx > 0
            mid = _mid()
            yield _sse({"event": "char_start", "id": mid,
                        "character_id": c.id, "name": c.name,
                        "emoji": c.emoji, "aside": reacting})
            # greeting autoplay: first-ever turn opens with the set greeting
            if empty_scene and c.greeting and not reacting:
                text = c.greeting.strip()
                yield _sse({"event": "char_delta", "id": mid, "text": text})
            else:
                history = transcript_messages(room, for_char=c.id)
                system = persona_prompt(
                    c, room.scenario, others, mood=room.mood or "",
                    director_notes=room.director_notes or "",
                    summary=room.summary or "",
                    tools=bool(room.tools_enabled) and not reacting)
                if reacting:
                    system += (
                        f"\n\n{responder.name} just spoke. Give only a "
                        "SHORT in-character aside reacting to it — one "
                        "line of dialogue or a brief *action*, at most "
                        "~25 words. If your character would stay silent "
                        "here, reply with exactly: PASS")
                acc = ""
                try:
                    async for piece in _char_reply_stream(
                            gateway, session, room.model or "auto",
                            system, history, emit=emit,
                            tools=bool(room.tools_enabled) and not reacting):
                        acc += piece
                        while not evq.empty():
                            yield _sse(evq.get_nowait())
                        if piece:
                            yield _sse({"event": "char_delta", "id": mid,
                                        "text": piece})
                except Exception:
                    acc = ""
                while not evq.empty():
                    yield _sse(evq.get_nowait())
                text = clean_turn_text(acc, c.name)
            if reacting and (not text or _is_pass(text)):
                # no aside from this character — remove the placeholder
                yield _sse({"event": "char_skip", "id": mid,
                            "character_id": c.id})
                continue
            if not text or _looks_like_tool_blob(text):
                text = f"*{c.name} remains quiet, considering.*" \
                    if not text else _TOOL_BLOB_FALLBACK
            msg = {"id": mid, "role": "character", "kind": "dialogue",
                   "speaker": c.name, "character_id": c.id,
                   "emoji": c.emoji, "text": text, "variants": []}
            if reacting:
                msg["aside"] = True
            room.messages = (list(room.messages or []) + [msg])[-MAX_STORED:]
            session.add(room)
            await session.commit()
            yield _sse({"event": "char_end", "id": mid, "text": text})
        yield _sse({"event": "turn_done", "messages": room.messages})
        yield b"data: [DONE]\n\n"

    # background re-summarize when the transcript outgrows its window
    if len(room.messages or []) - getattr(room, "_summarized_at", 0) \
            >= SUMMARY_EVERY or (
            len(room.messages or []) > MAX_TURNS and not room.summary):
        import asyncio
        asyncio.ensure_future(summarize_room(gateway, session_factory, room_id))


async def beat_only(session_factory: Any, room_id: int, *,
                    kind: str, text: str,
                    whisper_to: int | None = None) -> AsyncIterator[bytes]:
    """Record a narration/OOC beat with no character replies."""
    async with session_factory() as session:
        room = await session.get(RoleplayRoom, room_id)
        if not room:
            yield _sse({"event": "error", "text": "room not found"})
            return
        normalize_room(room)
        entry: dict[str, Any] = {
            "id": _mid(), "role": "narrator" if kind == "narrate" else "user",
            "kind": kind,
            "speaker": "Narrator" if kind == "narrate" else "You (OOC)",
            "text": text, "variants": [],
        }
        room.messages = list(room.messages or []) + [entry]
        session.add(room)
        await session.commit()
        yield _sse({"event": "beat", "message": entry})
        yield _sse({"event": "turn_done", "messages": room.messages})
        yield b"data: [DONE]\n\n"


async def summarize_room(gateway: Any, session_factory: Any,
                         room_id: int) -> None:
    """Compact the transcript into ``room.summary`` (best effort)."""
    from app.services.model_policy import internal_chat
    try:
        async with session_factory() as session:
            room = await session.get(RoleplayRoom, room_id)
            if not room or not room.messages:
                return
            lines = transcript_messages(room)
            raw = "\n".join(m["content"] for m in lines)[-8000:]
            payload = {"messages": [
                {"role": "system", "content":
                 "Summarize this roleplay transcript in <=200 words: "
                 "setting, key events, relationships, open threads. "
                 "Third person, present tense."},
                {"role": "user", "content": raw}],
                "temperature": 0.3}
            res = await internal_chat(gateway, session, payload,
                                      requested=room.model or "auto",
                                      role="general", retries=1)
            if res.ok and res.text:
                room.summary = res.text.strip()[:2000]
                room._summarized_at = len(room.messages or [])
                session.add(room)
                await session.commit()
    except Exception:
        return


async def retry_stream(
    gateway: Any, session_factory: Any, room_id: int, *,
    message_id: str, emit: Any = None,
) -> AsyncIterator[bytes]:
    """Regenerate one character turn as a new swipe variant."""
    import asyncio

    from app.services.agents import _TOOL_BLOB_FALLBACK, _looks_like_tool_blob
    evq2: asyncio.Queue = asyncio.Queue()
    emit = emit or evq2.put_nowait
    async with session_factory() as session:
        room = await session.get(RoleplayRoom, room_id)
        if not room:
            yield _sse({"event": "error", "text": "room not found"})
            return
        normalize_room(room)
        msg = _find_msg(room, message_id)
        if not msg or msg.get("role") != "character":
            yield _sse({"event": "error", "text": "no such character turn"})
            return
        idx = room.messages.index(msg)
        cid = msg.get("character_id")
        chars = await room_characters(session, room)
        c = next((x for x in chars if x.id == cid), None)
        if not c:
            yield _sse({"event": "error", "text": "character left the cast"})
            return
        others = await room_characters(session, room)
        history = transcript_messages(room, upto=idx, for_char=c.id)
        system = persona_prompt(
            c, room.scenario, others, mood=room.mood or "",
            director_notes=room.director_notes or "",
            summary=room.summary or "",
            tools=bool(room.tools_enabled))
        yield _sse({"event": "char_start", "id": message_id,
                    "character_id": c.id, "name": c.name,
                    "emoji": c.emoji, "regen": True})
        acc = ""
        try:
            async for piece in _char_reply_stream(
                    gateway, session, room.model or "auto", system, history,
                    emit=emit, tools=bool(room.tools_enabled)):
                acc += piece
                while not evq2.empty():
                    yield _sse(evq2.get_nowait())
                yield _sse({"event": "char_delta", "id": message_id,
                            "text": piece, "regen": True})
            while not evq2.empty():
                yield _sse(evq2.get_nowait())
        except Exception:
            acc = ""
        text = clean_turn_text(acc, c.name)
        if not text or _looks_like_tool_blob(text):
            text = f"*{c.name} remains quiet, considering.*" \
                if not text else _TOOL_BLOB_FALLBACK
        variants = list(msg.get("variants") or [])
        if msg.get("text"):
            variants.append(msg["text"])
        new_msg = {**msg, "variants": variants, "text": text}
        room.messages = [
            new_msg if m.get("id") == msg.get("id") else m
            for m in room.messages or []
        ]
        msg = new_msg
        session.add(room)
        await session.commit()
        yield _sse({"event": "char_end", "id": message_id, "text": text,
                    "variants": variants})
        yield _sse({"event": "turn_done", "messages": room.messages})
        yield b"data: [DONE]\n\n"


async def room_turn(gateway: Any, session: Any, room: RoleplayRoom,
                    user_text: str, *,
                    speaker_filter: int | None = None) -> list[dict[str, Any]]:
    """Append the user's message, then let each character (or only
    ``speaker_filter``) respond in turn order. Returns the new replies.

    Legacy non-streaming path kept for ``POST /rooms/{id}/message``.
    """
    normalize_room(room)
    chars = await room_characters(session, room)
    if not chars:
        return []
    if speaker_filter is not None:
        chars = [c for c in chars if c.id == speaker_filter] or chars
    else:
        live = [c for c in chars if c.id not in set(room.paused_ids or [])]
        if live:
            chars = [await _pick_responder(gateway, session, room, live,
                                           user_text)]

    room.messages = list(room.messages or []) + [
        {"id": _mid(), "role": "user", "kind": "talk",
         "speaker": "You", "text": user_text, "variants": []}]
    replies: list[dict[str, Any]] = []
    for c in chars:
        history = transcript_messages(room, for_char=c.id)
        others = await room_characters(session, room)
        try:
            text = await _reply(
                gateway, session, room.model or "auto",
                persona_prompt(c, room.scenario, others,
                               mood=room.mood or "",
                               director_notes=room.director_notes or "",
                               summary=room.summary or "",
                               tools=bool(room.tools_enabled)),
                history, tools=bool(room.tools_enabled))
        except Exception:
            text = ""
        text = clean_turn_text(text, c.name)
        if not text:
            text = f"*{c.name} remains quiet, considering.*"
        entry = {"id": _mid(), "role": "character", "kind": "dialogue",
                 "speaker": c.name, "character_id": c.id, "emoji": c.emoji,
                 "text": text, "variants": []}
        room.messages.append(entry)
        replies.append(entry)
    room.messages = room.messages[-MAX_STORED:]
    session.add(room)
    await session.flush()
    return replies
