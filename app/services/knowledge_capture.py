"""Knowledge capture — persist useful AI interactions into the library.

Every completed chat turn, agent reply, and research run appends a
compact entry to a rolling per-day markdown document inside the
owner's ``AI Captures`` collection. Entries become normal
``DocumentChunk`` rows, so they are immediately searchable via
``/v1/knowledge/search`` and the agent document-search tool — no extra
ingest pass required.

Small talk is filtered out by :func:`worth_capturing`; exact duplicate
turns (e.g. a replayed answer) are deduplicated per document.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select

from app.models import Collection, Document, DocumentChunk
from app.services.documents import _embed, chunk_text

log = logging.getLogger(__name__)

COLLECTION_NAME = "AI Captures"
MIN_ANSWER_CHARS = 240
MAX_ENTRY_CHARS = 24_000
MAX_QUESTION_CHARS = 4_000
MAX_DOC_BYTES = 1_048_576  # stop appending past ~1MB; next entry opens a new doc


def worth_capturing(question: str, answer: str) -> bool:
    """True when an exchange carries reusable knowledge.

    Small talk and near-empty replies are excluded; anything with a code
    block, a citation link, or a substantive answer is kept."""
    a = (answer or "").strip()
    if not a or not (question or "").strip():
        return False
    if len(a) < MIN_ANSWER_CHARS and "```" not in a and "http" not in a:
        return False
    return True


def _entry(kind: str, title: str, question: str, answer: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%H:%M")
    head = title or kind.replace("_", " ").title()
    q = question.strip()[:MAX_QUESTION_CHARS]
    a = answer.strip()[:MAX_ENTRY_CHARS]
    return f"\n\n## {stamp} UTC · {head}\n\n**Q:** {q}\n\n{a}\n"


async def _collection(session: Any, owner_id: Optional[int]) -> Collection:
    q = select(Collection).where(Collection.name == COLLECTION_NAME)
    if owner_id is None:
        q = q.where(Collection.owner_id.is_(None))
    else:
        q = q.where(Collection.owner_id == owner_id)
    col = (await session.execute(q)).scalars().first()
    if col is None:
        col = Collection(
            name=COLLECTION_NAME, owner_id=owner_id,
            description="Automatically captured knowledge from AI interactions")
        session.add(col)
        await session.flush()
    return col


async def _document(session: Any, col: Collection, owner_id: Optional[int],
                    name: str, kind: str) -> Document:
    q = (select(Document)
         .where(Document.name == name,
                Document.collection_id == col.id,
                Document.size < MAX_DOC_BYTES))
    if owner_id is None:
        q = q.where(Document.owner_id.is_(None))
    else:
        q = q.where(Document.owner_id == owner_id)
    doc = (await session.execute(q.order_by(Document.id.desc()))).scalars().first()
    if doc is not None:
        return doc
    doc = Document(name=name, mime="md", size=0, owner_id=owner_id,
                   collection_id=col.id, status="ready",
                   meta={"capture": kind, "chunks": 0, "chars": 0,
                         "embedded": False})
    session.add(doc)
    await session.flush()
    return doc


async def capture(owner_id: Optional[int], kind: str, question: str,
                  answer: str, *, title: str = "") -> Optional[int]:
    """Append one interaction to today's capture document.

    Returns the document id, or None when the exchange was filtered or
    deduplicated. Never raises for ordinary failures — a capture miss
    must not break a chat."""
    if not worth_capturing(question, answer):
        return None
    from app.db import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        # Plan gate: library captures are a basic+ entitlement. Anonymous
        # owners (no-auth local mode) are allowed through.
        if owner_id is not None:
            from app.models import User
            from app.services import entitlements
            owner = await session.get(User, owner_id)
            if owner is not None and not entitlements.has_cap(
                    owner.plan, "library_captures", owner.id,
                    is_admin=owner.role == "admin"):
                return None
        col = await _collection(session, owner_id)
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        doc = await _document(session, col, owner_id,
                              f"captures-{kind}-{day}.md", kind)
        entry = _entry(kind, title, question, answer)
        sha = hashlib.sha256(entry.encode("utf-8")).hexdigest()
        if doc.meta.get("last_sha") == sha:
            return doc.id
        chunks = chunk_text(entry.strip()) or [entry.strip()]
        embeds = await asyncio.to_thread(_embed, chunks)
        base = (await session.execute(
            select(func.coalesce(func.max(DocumentChunk.ord), -1))
            .where(DocumentChunk.document_id == doc.id))).scalar() or 0
        for i, (text, vec) in enumerate(zip(chunks, embeds)):
            session.add(DocumentChunk(
                document_id=doc.id, ord=base + 1 + i, text=text,
                embedding=vec, tokens=len(text) // 4))
        doc.size += len(entry.encode("utf-8"))
        doc.meta = {**doc.meta,
                    "chunks": int(doc.meta.get("chunks", 0)) + len(chunks),
                    "chars": int(doc.meta.get("chars", 0)) + len(entry),
                    "embedded": bool(embeds and embeds[0] is not None),
                    "last_sha": sha}
        await session.commit()
        return doc.id


def schedule(owner_id: Optional[int], kind: str, question: str,
             answer: str, *, title: str = "") -> None:
    """Fire-and-forget capture — never blocks or fails the caller."""
    if not worth_capturing(question, answer):
        return

    async def _bg() -> None:
        try:
            await capture(owner_id, kind, question, answer, title=title)
        except Exception as e:  # pragma: no cover - defensive
            log.warning("knowledge capture failed (%s): %s", kind, e)

    try:
        asyncio.create_task(_bg())
    except RuntimeError:
        pass  # no running loop — nothing to schedule on
