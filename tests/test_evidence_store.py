"""Evidence store: verbatim span validation, dedupe, claims, cache, v1 import."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models import ResearchRunV2
from app.services import evidence_store as es
from app.services.source_score import score_source


async def _run(session) -> ResearchRunV2:
    run = ResearchRunV2(question="q?", depth="standard")
    session.add(run)
    await session.flush()
    return run


PAGE = "The quick brown fox jumps over the lazy dog. Revenue was $4.2B in FY2023."


async def test_register_source_dedupes(session):
    scoring = score_source("https://example.com/page")
    s1 = await es.register_source(
        session, url="https://example.com/page?utm_source=x",
        title="T", content=PAGE, scoring=scoring)
    s2 = await es.register_source(
        session, url="https://example.com/page", title="T",
        content=PAGE, scoring=scoring)
    assert s1.id == s2.id
    assert s1.canonical_url == "https://example.com/page"
    assert s1.domain == "example.com"


async def test_add_evidence_verbatim_ok(session):
    run = await _run(session)
    src = await es.register_source(
        session, url="https://example.com/a", content=PAGE)
    ev = await es.add_evidence(
        session, run.id,
        es.EvidenceInput(source_id=src.id,
                         quote="Revenue was $4.2B in FY2023.",
                         summary="FY2023 revenue figure"),
        source_text=PAGE)
    assert ev.span_start == PAGE.index("Revenue")
    assert ev.span_end > ev.span_start
    assert "Revenue" in (ev.context or "")


async def test_add_evidence_whitespace_normalized(session):
    run = await _run(session)
    text = "Revenue was\n\n   $4.2B   in FY2023."
    src = await es.register_source(
        session, url="https://example.com/b", content=text)
    ev = await es.add_evidence(
        session, run.id,
        es.EvidenceInput(source_id=src.id,
                         quote="Revenue was $4.2B in FY2023.",
                         summary="s"),
        source_text=text)
    assert ev.span_end > ev.span_start


async def test_add_evidence_rejects_fabricated_quote(session):
    run = await _run(session)
    src = await es.register_source(
        session, url="https://example.com/c", content=PAGE)
    with pytest.raises(es.EvidenceRejected):
        await es.add_evidence(
            session, run.id,
            es.EvidenceInput(source_id=src.id,
                             quote="Revenue was $9.9B in FY2023.",
                             summary="s"),
            source_text=PAGE)


async def test_add_evidence_empty_quote_rejected(session):
    run = await _run(session)
    src = await es.register_source(
        session, url="https://example.com/d", content=PAGE)
    with pytest.raises(es.EvidenceRejected):
        await es.add_evidence(
            session, run.id,
            es.EvidenceInput(source_id=src.id, quote="", summary="s"))


async def test_claims_links_and_corroboration(session):
    run = await _run(session)
    scoring = score_source("https://a.example/x")
    s1 = await es.register_source(session, url="https://a.example/x",
                                  content=PAGE, scoring=scoring)
    s2 = await es.register_source(session, url="https://b.example/y",
                                  content=PAGE, scoring=scoring)
    e1 = await es.add_evidence(
        session, run.id, es.EvidenceInput(source_id=s1.id,
                                          quote="lazy dog", summary="s"),
        source_text=PAGE)
    e2 = await es.add_evidence(
        session, run.id, es.EvidenceInput(source_id=s2.id,
                                          quote="quick brown", summary="s"),
        source_text=PAGE)
    claim = await es.add_claim(session, run.id, "The fox is quick.",
                               is_key=True)
    await es.link_claim_evidence(session, claim.id, e1.id)
    await es.link_claim_evidence(session, claim.id, e2.id)
    n = await es.refresh_corroboration(session, claim.id)
    assert n == 2  # two distinct domains
    await es.set_verdict(session, claim.id, "supported", confidence=0.9)
    claims = await es.list_claims(session, run.id, verdict="supported")
    assert len(claims) == 1 and claims[0].is_key


async def test_fetch_cache_ttl(session):
    src = await es.register_source(session, url="https://example.com/e",
                                   content=PAGE)
    await es.cache_store(session, "https://example.com/e", src, ttl_s=3600)
    hit = await es.cache_lookup(session, "https://example.com/e")
    assert hit is not None and hit.id == src.id
    # Expired TTL -> miss.
    await es.cache_store(session, "https://example.com/e", src, ttl_s=-1)
    assert await es.cache_lookup(session, "https://example.com/e") is None


async def test_recall_evidence_like_fallback(session):
    """Astra is unconfigured in tests — recall falls back to LIKE search."""
    run = await _run(session)
    src = await es.register_source(session, url="https://example.com/f",
                                   content=PAGE)
    await es.add_evidence(
        session, run.id, es.EvidenceInput(
            source_id=src.id, quote="lazy dog",
            summary="contains the pangram tail"), source_text=PAGE)
    hits = await es.recall_evidence(session, run.id, "pangram")
    assert len(hits) == 1


async def test_import_v1_state(session):
    run = await _run(session)
    state = SimpleNamespace(
        sources={
            "s1": SimpleNamespace(id="s1", url="https://example.com/x",
                                  title="Doc", type="news"),
        },
        findings=[
            SimpleNamespace(claim="Something happened in 2024.",
                            confidence="high", verdict="supported",
                            verdict_note="ok", source_id="s1"),
            SimpleNamespace(claim="Unverified rumor.",
                            confidence="low", verdict="unsupported",
                            verdict_note="", source_id="s1"),
        ],
    )
    counts = await es.import_v1_state(session, run.id, state)
    assert counts == {"sources": 1, "claims": 2}
    claims = await es.list_claims(session, run.id)
    assert {c.verdict for c in claims} == {"supported", "unsupported"}
    assert sum(1 for c in claims if c.is_key) == 1
