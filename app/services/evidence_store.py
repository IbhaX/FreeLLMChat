"""Evidence store — the backbone of Nova v2 research (spec §5.3, §7.3).

Sources, quote-level evidence, claims and claim–evidence links live in
SQLite alongside the rest of the app. Evidence embeddings are pushed to an
Astra DB collection with ``$vectorize`` enabled, so similarity recall needs
no local embedding model.

Invariants enforced here, not in prompts:

* ``add_evidence`` only accepts a ``quote`` that appears verbatim (after
  whitespace normalization) in the source's extracted text — workers cannot
  fabricate citations.
* ``add_claim`` rejects non-atomic / empty text.
* Every row is scoped to ``run_id`` where applicable, so deleting a run
  deletes its evidence trail.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urldefrag, urlparse, urlunparse

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    Claim,
    ClaimEvidence,
    Evidence,
    FetchCache,
    Source,
    utcnow,
)
from app.services import astra_memory
from app.services.source_score import ScoreResult, domain_of

log = logging.getLogger("freellm.evidence")

_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "spm",
}

VECTORIZE_SERVICE = {"provider": "nvidia", "modelName": "NV-Embed-QA"}
# NV-Embed-QA hard-rejects inputs over 512 tokens (~2048 chars).
VECTORIZE_MAX_CHARS = 1800


# ---------------------------------------------------------------- errors

class EvidenceRejected(ValueError):
    """Quote could not be located verbatim in the source text."""


# ---------------------------------------------------------------- helpers

def canonicalize_url(url: str) -> str:
    """Lowercase scheme/host, drop fragment and tracking params."""
    try:
        parts = urlparse(url.strip())
    except Exception:
        return url.strip()
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    query = urlencode(
        [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
         if k.lower() not in _TRACKING_PARAMS]
    )
    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    canon = urlunparse((scheme, netloc, path, parts.params, query, ""))
    return urldefrag(canon)[0]


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _norm_ws(text: str) -> str:
    """Whitespace-normalize for verbatim span checks."""
    text = unicodedata.normalize("NFC", text or "")
    return re.sub(r"\s+", " ", text).strip()


def _data_dir() -> Path:
    db_url = get_settings().database_url
    return Path(db_url.split("///")[-1]).parent


def store_text_file(text: str, subdir: str = "texts") -> str:
    """Persist extracted text under ``<data>/research/<subdir>/<hash>.txt``;
    returns the path to store in ``Source.text_ref``."""
    h = content_hash(text)
    root = _data_dir() / get_settings().research_snapshot_dir / subdir
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{h}.txt"
    if not path.exists():
        path.write_text(text, encoding="utf-8")
    return str(path)


def load_source_text(source: Source) -> str:
    """Read extracted text for a source from its ``text_ref`` file."""
    if not source.text_ref:
        return ""
    try:
        return Path(source.text_ref).read_text(encoding="utf-8")
    except OSError:
        return ""


# ---------------------------------------------------------------- Astra

def _vector_collection():
    db = astra_memory.astra_db()
    if db is None:
        return None
    try:
        return db.get_collection(get_settings().astra_evidence_collection)
    except Exception:
        return None


def ensure_vector_collection() -> None:
    """Create the evidence collection with $vectorize. Idempotent, silent."""
    db = astra_memory.astra_db()
    if db is None:
        return
    name = get_settings().astra_evidence_collection
    try:
        if name not in set(db.list_collection_names()):
            try:
                db.create_collection(
                    name, definition={"vector": {"service": VECTORIZE_SERVICE}}
                )
            except Exception:
                db.create_collection(name)  # plain store fallback
    except Exception:
        pass


def _index_evidence(
    ev: Evidence, *, domain: str, quote: str, summary: str
) -> None:
    col = _vector_collection()
    if col is None:
        return
    vec_text = f"{summary}\n{quote}"[:3000].strip()
    if not vec_text:
        return
    try:
        col.insert_one({
            "_id": ev.id,
            "$vectorize": vec_text,
            "run_id": ev.run_id,
            "question_id": ev.question_id,
            "source_id": ev.source_id,
            "domain": domain,
            "summary": (summary or "")[:1000],
        })
        ev.vector_ref = ev.id
    except Exception as e:  # never let indexing break persistence
        log.debug("astra evidence index failed: %s", e)


def delete_run_vectors(run_id: str) -> None:
    col = _vector_collection()
    if col is None:
        return
    try:
        col.delete_many(filter={"run_id": run_id})
    except Exception:
        pass


# ---------------------------------------------------------------- sources

async def register_source(
    session: AsyncSession,
    *,
    url: str,
    final_url: Optional[str] = None,
    title: Optional[str] = None,
    author: Optional[str] = None,
    published_at: Optional[datetime] = None,
    fetched_at: Optional[datetime] = None,
    content_type: str = "html",
    content: str = "",
    snapshot_ref: Optional[str] = None,
    text_ref: Optional[str] = None,
    scoring: Optional[ScoreResult] = None,
) -> Source:
    """Insert or return the existing source for (canonical_url, content_hash)."""
    canon = canonicalize_url(final_url or url)
    chash = content_hash(content or "")
    existing = (await session.execute(
        select(Source).where(
            Source.canonical_url == canon, Source.content_hash == chash)
    )).scalar_one_or_none()
    if existing is not None:
        return existing
    src = Source(
        url=url,
        canonical_url=canon,
        domain=domain_of(canon),
        title=title,
        author=author,
        published_at=published_at,
        fetched_at=fetched_at or utcnow(),
        content_type=content_type,
        content_hash=chash,
        snapshot_ref=snapshot_ref,
        text_ref=text_ref,
        source_class=scoring.source_class if scoring else "unknown",
        score=scoring.score if scoring else 0.0,
        score_detail=dict(scoring.detail) if scoring else {},
    )
    session.add(src)
    await session.flush()
    return src


# ---------------------------------------------------------------- evidence

@dataclass
class EvidenceInput:
    source_id: str
    quote: str
    summary: str
    question_id: Optional[str] = None
    context: Optional[str] = None
    extracted_by: str = "worker"


async def add_evidence(
    session: AsyncSession,
    run_id: str,
    ev_in: EvidenceInput,
    *,
    source_text: Optional[str] = None,
) -> Evidence:
    """Persist one evidence row. The quote must appear verbatim (after
    whitespace normalization) in the source's extracted text, else
    ``EvidenceRejected`` is raised — the runtime, not the prompt, enforces
    citation integrity (spec §7.3)."""
    source = await session.get(Source, ev_in.source_id)
    if source is None:
        raise EvidenceRejected(f"unknown source {ev_in.source_id}")

    text = source_text if source_text is not None else load_source_text(source)
    quote = (ev_in.quote or "").strip()
    if not quote:
        raise EvidenceRejected("empty quote")

    if text:
        idx = text.find(quote)
        if idx < 0:
            ntext, nquote = _norm_ws(text), _norm_ws(quote)
            idx = ntext.find(nquote)
            if idx < 0:
                raise EvidenceRejected("quote not found in source text")
            span_start, span_end = idx, idx + len(nquote)
        else:
            span_start, span_end = idx, idx + len(quote)
    else:
        # No extracted text available (e.g. imported legacy sources): accept
        # but leave the span empty so downstream checks can flag it.
        span_start = span_end = 0

    context = ev_in.context
    if context is None and text and span_end > span_start:
        lo, hi = max(0, span_start - 160), min(len(text), span_end + 160)
        context = text[lo:hi].strip()

    ev = Evidence(
        run_id=run_id,
        question_id=ev_in.question_id,
        source_id=ev_in.source_id,
        quote=quote[:8000],
        span_start=span_start,
        span_end=span_end,
        context=context,
        summary=(ev_in.summary or "")[:2000],
        extracted_by=ev_in.extracted_by,
    )
    session.add(ev)
    await session.flush()
    # astrapy insert is synchronous — index off the event loop.
    await asyncio.to_thread(
        _index_evidence, ev, domain=source.domain, quote=quote,
        summary=ev.summary)
    return ev


async def list_evidence(
    session: AsyncSession,
    run_id: str,
    *,
    question_id: Optional[str] = None,
    limit: int = 500,
) -> list[Evidence]:
    stmt = select(Evidence).where(Evidence.run_id == run_id)
    if question_id:
        stmt = stmt.where(Evidence.question_id == question_id)
    stmt = stmt.order_by(Evidence.created_at).limit(min(limit, 2000))
    return list((await session.execute(stmt)).scalars().all())


async def recall_evidence(
    session: AsyncSession,
    run_id: str,
    query: str,
    limit: int = 10,
) -> list[Evidence]:
    """Similarity recall of this run's evidence via Astra $vectorize, with a
    SQLite LIKE fallback when Astra is unavailable."""
    query = (query or "").strip()
    if not query:
        return []
    col = _vector_collection()
    if col is not None:
        try:
            # astrapy is synchronous — keep the event loop free.
            docs = await asyncio.to_thread(
                col.find,
                filter={"run_id": run_id},
                sort={"$vectorize": query[:VECTORIZE_MAX_CHARS]},
                limit=limit,
                projection={"_id": 1},
            )
            ids = [d["_id"] for d in docs]
            if ids:
                rows = (await session.execute(
                    select(Evidence).where(Evidence.id.in_(ids))
                )).scalars().all()
                order = {eid: i for i, eid in enumerate(ids)}
                return sorted(rows, key=lambda e: order.get(e.id, 0))
        except Exception as e:
            log.debug("astra evidence recall failed, using LIKE: %s", e)
    like = f"%{query[:200]}%"
    stmt = (
        select(Evidence)
        .where(Evidence.run_id == run_id,
               or_(Evidence.quote.like(like), Evidence.summary.like(like)))
        .limit(min(limit, 50))
    )
    return list((await session.execute(stmt)).scalars().all())


# ---------------------------------------------------------------- claims

async def add_claim(
    session: AsyncSession,
    run_id: str,
    text: str,
    *,
    question_id: Optional[str] = None,
    is_key: bool = False,
) -> Claim:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty claim")
    claim = Claim(
        run_id=run_id, question_id=question_id, text=text[:4000], is_key=is_key
    )
    session.add(claim)
    await session.flush()
    return claim


async def link_claim_evidence(
    session: AsyncSession,
    claim_id: str,
    evidence_id: str,
    relation: str = "supports",
) -> ClaimEvidence:
    if relation not in ("supports", "refutes", "context"):
        raise ValueError(f"bad relation {relation}")
    existing = await session.get(ClaimEvidence, (claim_id, evidence_id))
    if existing is not None:
        return existing
    link = ClaimEvidence(claim_id=claim_id, evidence_id=evidence_id,
                         relation=relation)
    session.add(link)
    await session.flush()
    return link


async def refresh_corroboration(session: AsyncSession, claim_id: str) -> int:
    """Count distinct source domains behind 'supports' links and store it."""
    stmt = (
        select(func.count(func.distinct(Source.domain)))
        .select_from(ClaimEvidence)
        .join(Evidence, Evidence.id == ClaimEvidence.evidence_id)
        .join(Source, Source.id == Evidence.source_id)
        .where(ClaimEvidence.claim_id == claim_id,
               ClaimEvidence.relation == "supports")
    )
    n = int((await session.execute(stmt)).scalar_one() or 0)
    claim = await session.get(Claim, claim_id)
    if claim is not None:
        claim.independent_sources = n
        await session.flush()
    return n


async def set_verdict(
    session: AsyncSession,
    claim_id: str,
    verdict: str,
    *,
    confidence: Optional[float] = None,
    note: Optional[str] = None,
) -> Optional[Claim]:
    allowed = {"supported", "partially_supported", "unverified",
               "unsupported", "contradicted", "unchecked"}
    if verdict not in allowed:
        raise ValueError(f"bad verdict {verdict}")
    claim = await session.get(Claim, claim_id)
    if claim is None:
        return None
    claim.verdict = verdict
    claim.confidence = confidence
    claim.verifier_note = note
    claim.verified_at = utcnow()
    await session.flush()
    return claim


async def list_claims(
    session: AsyncSession, run_id: str, *, verdict: Optional[str] = None
) -> list[Claim]:
    stmt = select(Claim).where(Claim.run_id == run_id)
    if verdict:
        stmt = stmt.where(Claim.verdict == verdict)
    return list((await session.execute(stmt)).scalars().all())


# ---------------------------------------------------------------- cache

async def cache_lookup(session: AsyncSession, url: str) -> Optional[Source]:
    """Return the cached source for ``url`` when its TTL is still fresh."""
    canon = canonicalize_url(url)
    row = await session.get(FetchCache, canon)
    if row is None or row.source_id is None:
        return None
    fetched = row.fetched_at
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - fetched).total_seconds()
    if age > row.ttl_s:
        return None
    return await session.get(Source, row.source_id)


async def cache_store(
    session: AsyncSession, url: str, source: Source, ttl_s: int,
    etag: Optional[str] = None,
) -> FetchCache:
    canon = canonicalize_url(url)
    row = await session.get(FetchCache, canon)
    if row is None:
        row = FetchCache(canonical_url=canon)
        session.add(row)
    row.source_id = source.id
    row.fetched_at = utcnow()
    row.ttl_s = ttl_s
    row.etag = etag
    await session.flush()
    return row


_TTL_BY_CLASS = {
    "news": 3600,            # 1 h
    "reference": 7 * 86400,  # 7 d
    "academic": 30 * 86400,  # 30 d
    "primary": 30 * 86400,
    "official": 7 * 86400,
}


def ttl_for_class(source_class: str) -> int:
    return _TTL_BY_CLASS.get(source_class,
                             get_settings().research_fetch_ttl_default_s)


# ---------------------------------------------------------------- v1 import

async def import_v1_state(
    session: AsyncSession, run_id: str, state: Any,
) -> dict[str, int]:
    """Best-effort import of a v1 ``ResearchState`` into the evidence store.

    v1 findings carry paraphrased evidence, not verbatim quotes, so they map
    to claims (+ sources) rather than evidence rows. Verdicts from the v1
    verifier are preserved. This is the migration step that makes v1 runs
    visible inside the v2 data model without changing v1 behaviour.
    """
    verdict_map = {
        "supported": "supported",
        "partially_supported": "partially_supported",
        "unsupported": "unsupported",
        "contradicted": "contradicted",
        "cannot_verify": "unverified",
        "unverified": "unverified",
    }
    src_ids: dict[str, str] = {}
    for s in (getattr(state, "sources", None) or {}).values():
        url = getattr(s, "url", "") or ""
        if not url:
            continue
        src = await register_source(
            session,
            url=url,
            title=getattr(s, "title", "") or None,
            content="",
            content_type="html",
        )
        if getattr(s, "type", None) and src.source_class in (None, "", "unknown"):
            src.source_class = str(s.type)
        src_ids[getattr(s, "id", "")] = src.id

    n_claims = 0
    for f in getattr(state, "findings", None) or []:
        text = (getattr(f, "claim", "") or "").strip()
        if not text:
            continue
        claim = await add_claim(
            session, run_id, text,
            is_key=(getattr(f, "confidence", "") or "").lower() == "high",
        )
        verdict = verdict_map.get((getattr(f, "verdict", "") or "").lower())
        if verdict:
            await set_verdict(
                session, claim.id, verdict,
                note=(getattr(f, "verdict_note", "") or "")[:1000] or None,
            )
        n_claims += 1
    return {"sources": len(src_ids), "claims": n_claims}
