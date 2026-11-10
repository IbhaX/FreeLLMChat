from __future__ import annotations

from app.services import research as R


def test_classify_depth():
    assert R.classify_depth("What is the capital of France?") == "quick"
    assert R.classify_depth("Explain how photosynthesis works in detail please") == "standard"
    assert R.classify_depth("Compare React and Vue for large teams in 2026") == "deep"
    assert R.classify_depth("Give me a thorough market review of EV batteries") == "deep"
    assert R.classify_depth("What is the latest price of Bitcoin?") == "standard"


def test_extract_json_variants():
    assert R._extract_json('{"a": 1}') == {"a": 1}
    assert R._extract_json('Here is the plan:\n```json\n{"a": [1, 2]}\n```\nDone') == {"a": [1, 2]}
    assert R._extract_json('prefix {"x": {"y": 2}} trailing') == {"x": {"y": 2}}
    assert R._extract_json('no json here') is None
    assert R._extract_json('[{"verdict": "supported"}]') == {"items": [{"verdict": "supported"}]}


def test_merge_and_score_sources():
    st = R.ResearchState()
    R._merge_findings(st, {
        "findings": [{"claim": "c", "evidence": "e", "source_id": "s1", "confidence": "high"}],
        "sources": [{"id": "s1", "url": "https://x", "type": "news", "date": "2026-01-01"},
                    {"id": "s2", "url": "https://y", "type": "primary"}],
        "conflicts": ["a vs b"],
        "not_found": ["z"],
    }, "q1")
    assert len(st.findings) == 1 and st.findings[0].sub_question == "q1"
    assert set(st.sources) == {"s1", "s2"}
    assert st.contradictions == ["a vs b"] and st.unverified == ["z"]
    R._score_sources(st)
    assert st.sources["s2"].score > st.sources["s1"].score
    assert st.sources["s2"].quality == "high"


def test_cache_roundtrip(monkeypatch):
    R.clear_cache()
    R._cache_put("c1", "plan", "Q?", {"sub_questions": []})
    assert R._cache_get("c1", "plan", "q?") == {"sub_questions": []}
    assert R._cache_get("c1", "plan", "other") is None
    assert R._cache_get("", "plan", "q?") is None


def test_backfill_sources_and_clean_answer():
    st = R.ResearchState()
    st.harvested["q1"] = ["https://example.com/a", "https://example.com/b"]
    R._merge_findings(st, {"findings": [{"claim": "c1", "source_id": "", "confidence": "low"}],
                           "sources": []}, "q1")
    R._backfill_sources(st)
    assert st.findings[0].source_id in st.sources
    assert st.sources[st.findings[0].source_id].url == "https://example.com/a"

    # explicit source ids are kept
    st2 = R.ResearchState()
    R._merge_findings(st2, {"findings": [{"claim": "c", "source_id": "s1"}],
                            "sources": [{"id": "s1", "url": "https://x"}]}, "q1")
    st2.harvested["q1"] = ["https://y"]
    R._backfill_sources(st2)
    assert st2.findings[0].source_id == "s1"

    # JSON-LD answer is unwrapped/rejected
    st3 = R.ResearchState()
    st3.findings.append(R.Finding(claim="HTTP/3 uses QUIC", evidence="", source_id=""))
    out = R._clean_answer('{"@context":"https://schema.org","@type":"FAQPage","mainEntity":[{"acceptedAnswer":{"text":"QUIC is UDP-based."}}]}', st3)
    assert "QUIC is UDP-based" in out
    assert "@context" not in out
    out2 = R._clean_answer("", st3)
    assert "HTTP/3 uses QUIC" in out2


def test_quality_check_citations():
    from app.services.research import ResearchState, Source, _quality_check
    st = ResearchState(question="q")
    st.sources["s1"] = Source(id="s1", title="t", url="", publisher="", date="2026-01-01")
    st.answer = "Claim one [1] and claim two [2] and [9]."
    import asyncio
    q = asyncio.run(_quality_check(st, lambda ev: None))
    assert q["bad_citations"] == [2, 9]
    assert q["citations"] == 3
    assert q["urls_checked"] == 0
