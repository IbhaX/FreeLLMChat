"""Astra DB vector memory: short-term conversation context + long-term facts.

Enabled when ASTRA_DB_APPLICATION_TOKEN and ASTRA_DB_API_ENDPOINT are set.
Uses Astra's built-in ``$vectorize`` (NVIDIA NV-Embed, free) so no embedding
model runs here. Every call degrades silently — if Astra is unreachable the
gateway simply behaves as if memory were empty.

Multi-tenant isolation
----------------------
Every document carries ``user_id`` — the immutable tenant key produced by
:func:`tenant_key` (``u<account id>`` for authenticated users, ``"anon"``
for unauthenticated single-tenant mode). Every read, search, update and
delete applies ``{"user_id": tenant}`` **inside** the Astra query — results
are never fetched globally and filtered afterwards. Callers pass the tenant
they resolved from verified auth; this module never accepts one from a
request body. A missing tenant fails closed (empty results / no write).

This is application-enforced isolation on a shared collection, not
database-enforced row-level security.

Collections (auto-created on first use):
- ``mem_short`` — one doc per chat turn, filtered by user + conversation id.
- ``mem_long``  — durable facts extracted from chats, recalled per-user.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from functools import lru_cache
from typing import Any

from app.config import get_settings

log = logging.getLogger("nova.memory")

SHORT = "mem_short"
LONG = "mem_long"
_VECTORIZE = {"provider": "nvidia", "modelName": "NV-Embed-QA"}
# NV-Embed-QA rejects inputs over 512 tokens with a 400 — and 1800 chars
# already measured 645 tokens (~2.8 chars/token worst case). Cap well
# under ~1430 chars while retaining the full text in the document body.
_VECTORIZE_MAX_CHARS = 1200

# Unauthenticated single-tenant context. Distinct from every real account
# tenant ("u<id>") and from legacy quarantined docs ("__legacy__").
ANON_TENANT = "anon"
# Marker the migration stamps on pre-tenancy docs whose owner could not be
# traced. They match no real tenant filter, so no user ever sees them —
# they are preserved for audit, not deleted.
LEGACY_TENANT = "__legacy__"


def tenant_key(user: Any | None) -> str:
    """Canonical tenant id for a resolved (or absent) user.

    ``u<immutable account id>`` when authenticated — never an email or
    display name — ``"anon"`` for single-tenant mode. Same shape as
    ``deps.subject_for``. Never returns a falsy value.
    """
    uid = getattr(user, "id", None)
    return f"u{uid}" if uid is not None else ANON_TENANT


def _require_tenant(user_id: str | None) -> str:
    """Validate a tenant key — fail closed on empty or explicitly forged
    values. ``__legacy__`` is a valid tenant to query (returns nothing real
    users would own) but is blocked from writes below."""
    t = (user_id or "").strip()
    if not t:
        raise PermissionError("memory access requires a tenant context")
    return t


def _writable_tenant(user_id: str | None) -> str:
    t = _require_tenant(user_id)
    if t == LEGACY_TENANT:
        raise PermissionError("cannot write into the legacy quarantine")
    return t


def _vt(text: str) -> str:
    return (text or "")[:_VECTORIZE_MAX_CHARS]


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


def astra_db() -> Any | None:
    """Public accessor for the shared Astra database handle (None when
    unconfigured/unreachable). Other vector stores (e.g. the v2 evidence
    index) share this connection."""
    return _db()


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


_ensured = False


def ensure_collections() -> None:
    """Create both collections with built-in vectorize. Idempotent.

    Runs at most once per process — ``list_collection_names`` is a remote
    API call and every chat request used to pay that latency."""
    global _ensured
    if _ensured:
        return
    db = _db()
    if db is None:
        return
    try:
        existing = set(db.list_collection_names())
        _ensured = True  # only cache success — transient failures retry
        for name in (SHORT, LONG):
            if name not in existing:
                try:
                    db.create_collection(
                        name,
                        definition={"vector": {"service": _VECTORIZE}},
                    )
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


def store_short(user_id: str, conversation_id: str, role: str, text: str) -> None:
    """One chat turn into short-term memory, owned by ``user_id``."""
    text = (text or "").strip()
    if not text or _db() is None:
        return
    try:
        tenant = _writable_tenant(user_id)
    except PermissionError:
        log.warning("mem store_short rejected: missing/invalid tenant")
        return
    _insert(SHORT, {
        "_id": uuid.uuid4().hex,
        "$vectorize": _vt(text),
        "user_id": tenant,
        "memory_type": "conversation",
        "conversation_id": conversation_id,
        "role": role,
        "text": text[:4000],
        "ts": time.time(),
        "created_at": time.time(),
        "updated_at": time.time(),
    })


def store_long(user_id: str, fact: str, source: str = "auto") -> None:
    """Durable extracted fact into long-term memory, owned by ``user_id``."""
    fact = (fact or "").strip()
    if not fact or _db() is None:
        return
    try:
        tenant = _writable_tenant(user_id)
    except PermissionError:
        log.warning("mem store_long rejected: missing/invalid tenant")
        return
    # Skip near-duplicates within THIS tenant only — never compare across
    # users (would both leak and wrongly dedupe).
    try:
        col = _collection(LONG)
        hit = col.find_one(
            filter={"user_id": tenant}, sort={"$vectorize": _vt(fact)})
        if hit and hit.get("text", "").strip().lower() == fact.lower():
            return
    except Exception:
        pass
    now = time.time()
    _insert(LONG, {
        "_id": uuid.uuid4().hex,
        "$vectorize": _vt(fact),
        "user_id": tenant,
        "memory_type": "knowledge",
        "text": fact[:2000],
        "source": source,
        "ts": now,
        "created_at": now,
        "updated_at": now,
    })


def _recall(col_name: str, user_id: str, query: str, limit: int,
            extra_filter: dict | None = None) -> list[str]:
    col = _collection(col_name)
    if col is None or not query:
        return []
    try:
        tenant = _require_tenant(user_id)
    except PermissionError:
        return []
    flt: dict[str, Any] = {"user_id": tenant}
    if extra_filter:
        flt.update(extra_filter)
    try:
        docs = col.find(
            filter=flt,
            sort={"$vectorize": _vt(query)},
            limit=limit,
            projection={"text": 1, "role": 1},
        )
        return [d.get("text", "") for d in docs if d.get("text")]
    except Exception:
        # Collection without vectorize or transient error — fall back to
        # most-recent docs IN THE SAME TENANT so something still works.
        try:
            docs = list(col.find(filter=flt, limit=limit,
                                 projection={"text": 1}))
            return [d.get("text", "") for d in docs if d.get("text")]
        except Exception:
            return []


def recall_short(user_id: str, query: str, conversation_id: str,
                 limit: int = 6) -> list[str]:
    return _recall(SHORT, user_id, query, limit,
                   {"conversation_id": conversation_id})


def recall_long(user_id: str, query: str, limit: int = 6) -> list[str]:
    return _recall(LONG, user_id, query, limit)


def recall_text(query: str, user_id: str,
                conversation_id: str | None = None) -> str:
    """Format recalled memories as a system-prompt block (tenant-scoped)."""
    if _db() is None:
        return ""
    parts: list[str] = []
    s = get_settings()
    long_hits = recall_long(user_id, query, s.memory_long_limit)
    if long_hits:
        parts.append("### Long-term memory\n" + "\n".join(f"- {t}" for t in long_hits))
    if conversation_id:
        short_hits = recall_short(user_id, query, conversation_id,
                                  s.memory_short_limit)
        if short_hits:
            parts.append("### Relevant earlier context\n"
                         + "\n".join(f"- {t}" for t in short_hits))
    if not parts:
        return ""
    return "## Memory (Astra)\n" + "\n\n".join(parts)


def delete_doc(user_id: str, col_name: str, doc_id: str) -> int:
    """Delete one document ONLY if owned by ``user_id``. Returns count."""
    try:
        tenant = _writable_tenant(user_id)
    except PermissionError:
        return 0
    col = _collection(col_name)
    if col is None or not doc_id:
        return 0
    try:
        res = col.delete_one({"_id": doc_id, "user_id": tenant})
        return int(getattr(res, "deleted_count", 0) or 0)
    except Exception:
        return 0


def update_doc_text(user_id: str, col_name: str, doc_id: str,
                    text: str) -> bool:
    """Update a document's text ONLY if owned by ``user_id``."""
    text = (text or "").strip()
    try:
        tenant = _writable_tenant(user_id)
    except PermissionError:
        return False
    col = _collection(col_name)
    if col is None or not doc_id or not text:
        return False
    try:
        res = col.update_one(
            {"_id": doc_id, "user_id": tenant},
            {"$set": {"text": text[:4000], "updated_at": time.time()}})
        return bool(getattr(res, "matched_count", 0) or
                    getattr(res, "modified_count", 0))
    except Exception:
        return False


def get_doc(user_id: str, col_name: str, doc_id: str) -> dict[str, Any] | None:
    """Fetch one document ONLY if owned by ``user_id`` (IDOR guard)."""
    try:
        tenant = _require_tenant(user_id)
    except PermissionError:
        return None
    col = _collection(col_name)
    if col is None or not doc_id:
        return None
    try:
        return col.find_one(
            filter={"_id": doc_id, "user_id": tenant},
            projection={"$vector": 0})
    except Exception:
        return None


async def extract_and_store(gateway, user_id: str, user_text: str,
                            assistant_text: str) -> None:
    """Ask the LLM for durable facts from this turn and store them long-term.

    Facts are written under the *verified* tenant — the same one that owns
    the conversation. Fire-and-forget — never raises.
    """
    if _db() is None or not user_text:
        return
    try:
        tenant = _writable_tenant(user_id)
    except PermissionError:
        return
    # Skip the extra LLM call only on truly trivial inputs — short
    # self-disclosures ("my name is Albin") are exactly the durable facts
    # this extractor exists for, so the bar is low on purpose.
    if len(user_text.strip()) < 8 or not assistant_text.strip():
        return
    prompt = (
        "From this exchange, extract 0-5 durable facts worth remembering "
        "about the user long-term (names, preferences, projects, decisions). "
        "Reply with a JSON array of short self-contained statements only — "
        "each must make sense on its own, e.g. \"the user's name is Albin\" "
        "or \"user prefers dark themes\". Bare fragments like \"Albin\" are "
        "wrong. [] if none.\n\n"
        f"USER: {user_text[:2000]}\nASSISTANT: {assistant_text[:2000]}"
    )
    try:
        import json

        from app.db import get_session_factory
        from app.services.model_policy import stage_models

        result = None
        async with get_session_factory()() as s:
            for cand in stage_models("auto", role="fast"):
                try:
                    result, _p, _k, _m = await gateway.chat(s, {
                        "model": cand,
                        "messages": [{"role": "user", "content": prompt}],
                        "stream": False,
                    })
                    break
                except Exception:
                    continue
        if result is None:
            return
        content = ""
        for c in result.get("choices", []):
            content += (c.get("message") or {}).get("content") or ""
        start, end = content.find("["), content.rfind("]")
        if start < 0 or end <= start:
            return
        facts = json.loads(content[start:end + 1])
        for f in facts[:5]:
            if isinstance(f, str):
                # astrapy is synchronous — keep the event loop free.
                await asyncio.to_thread(store_long, tenant, f)
    except Exception:
        pass
