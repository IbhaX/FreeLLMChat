"""Astra DB vector memory: short-term conversation context + long-term facts.

Enabled when ASTRA_DB_APPLICATION_TOKEN and ASTRA_DB_API_ENDPOINT are set.
Uses Astra's built-in `$vectorize` (NVIDIA NV-Embed, free) so no embedding
model runs here. Every call degrades silently — if Astra is unreachable the
gateway simply behaves as if memory were empty.

Collections (auto-created on first use):
- `mem_short` — one doc per chat turn, filtered by conversation id; recalled
  by similarity to the latest message.
- `mem_long`  — durable facts/preferences extracted from chats by the
  extractor, recalled globally by similarity.
"""

from __future__ import annotations

import time
import uuid
from functools import lru_cache
from typing import Any

from app.config import get_settings

SHORT = "mem_short"
LONG = "mem_long"
_VECTORIZE = {"provider": "nvidia", "modelName": "NV-Embed-QA"}


@lru_cache
def _db():
    s = get_settings()
    if not (s.astra_db_application_token and s.astra_db_api_endpoint):
        return None
    try:
        from astrapy import DataAPIClient

        client = DataAPIClient(s.astra_db_application_token)
        return client.get_database(
            s.astra_db_api_endpoint, keyspace=s.astra_db_keyspace or None
        )
    except Exception:
        return None


def enabled() -> bool:
    return _db() is not None


def _collection(name: str):
    db = _db()
    if db is None:
        return None
    try:
        return db.get_collection(name)
    except Exception:
        return None


def ensure_collections() -> None:
    """Create both collections with built-in vectorize. Idempotent."""
    db = _db()
    if db is None:
        return
    try:
        existing = set(db.list_collection_names())
        for name in (SHORT, LONG):
            if name not in existing:
                try:
                    db.create_collection(name, service=_VECTORIZE)
                except Exception:
                    db.create_collection(name)  # no vectorize → plain store
    except Exception:
        pass


def _insert(col_name: str, doc: dict[str, Any]) -> None:
    col = _collection(col_name)
    if col is None:
        return
    try:
        col.insert_one(doc)
    except Exception:
        pass


def store_short(conversation_id: str, role: str, text: str) -> None:
    text = (text or "").strip()
    if not text or _db() is None:
        return
    _insert(SHORT, {
        "_id": uuid.uuid4().hex,
        "$vectorize": text[:4000],
        "conversation_id": conversation_id,
        "role": role,
        "text": text[:4000],
        "ts": time.time(),
    })


def store_long(fact: str, source: str = "auto") -> None:
    fact = (fact or "").strip()
    if not fact or _db() is None:
        return
    # Skip near-duplicates: if the closest long-term hit is (almost) identical,
    # don't store it again.
    try:
        col = _collection(LONG)
        hit = col.find_one(sort={"$vectorize": fact[:4000]})
        if hit and hit.get("text", "").strip().lower() == fact.lower():
            return
    except Exception:
        pass
    _insert(LONG, {
        "_id": uuid.uuid4().hex,
        "$vectorize": fact[:2000],
        "text": fact[:2000],
        "source": source,
        "ts": time.time(),
    })


def _recall(col_name: str, query: str, limit: int, flt: dict | None = None) -> list[str]:
    col = _collection(col_name)
    if col is None or not query:
        return []
    try:
        docs = col.find(
            filter=flt,
            sort={"$vectorize": query[:4000]},
            limit=limit,
            projection={"text": 1, "role": 1},
        )
        return [d.get("text", "") for d in docs if d.get("text")]
    except Exception:
        # Collection without vectorize or transient error — fall back to
        # most-recent docs so something still works.
        try:
            docs = list(col.find(filter=flt, limit=limit, projection={"text": 1}))
            return [d.get("text", "") for d in docs if d.get("text")]
        except Exception:
            return []


def recall_short(query: str, conversation_id: str, limit: int = 6) -> list[str]:
    return _recall(SHORT, query, limit, {"conversation_id": conversation_id})


def recall_long(query: str, limit: int = 6) -> list[str]:
    return _recall(LONG, query, limit)


def recall_text(query: str, conversation_id: str | None = None) -> str:
    """Format recalled memories as a system-prompt block."""
    if _db() is None:
        return ""
    parts: list[str] = []
    s = get_settings()
    long_hits = recall_long(query, s.memory_long_limit)
    if long_hits:
        parts.append("### Long-term memory\n" + "\n".join(f"- {t}" for t in long_hits))
    if conversation_id:
        short_hits = recall_short(query, conversation_id, s.memory_short_limit)
        if short_hits:
            parts.append("### Relevant earlier context\n"
                         + "\n".join(f"- {t}" for t in short_hits))
    if not parts:
        return ""
    return "## Memory (Astra)\n" + "\n\n".join(parts)


async def extract_and_store(gateway, user_text: str, assistant_text: str) -> None:
    """Ask the LLM for durable facts from this turn and store them long-term.

    Fire-and-forget — never raises.
    """
    if _db() is None or not user_text:
        return
    prompt = (
        "From this exchange, extract 0-5 durable facts worth remembering "
        "about the user long-term (preferences, names, projects, decisions). "
        "Reply with a JSON array of short strings only — [] if none.\n\n"
        f"USER: {user_text[:2000]}\nASSISTANT: {assistant_text[:2000]}"
    )
    try:
        import json

        from app.db import get_session_factory

        async with get_session_factory()() as s:
            result, _p, _k, _m = await gateway.chat(s, {
                "model": "auto",
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
            })
        content = ""
        for c in result.get("choices", []):
            content += (c.get("message") or {}).get("content") or ""
        start, end = content.find("["), content.rfind("]")
        if start < 0 or end <= start:
            return
        facts = json.loads(content[start:end + 1])
        for f in facts[:5]:
            if isinstance(f, str):
                store_long(f)
    except Exception:
        pass
