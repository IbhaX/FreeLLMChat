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


class _FakeGateway:
    def __init__(self, text: str = "## Aspect\n\n" + "word " * 600):
        self.text = text
        self.calls: list[str] = []

    async def chat(self, session, payload, **kw):
        self.calls.append(payload["messages"][0]["content"][:60])
        return ({"choices": [{"message": {"content": self.text}}]},
                "fake", None, "fake/model")


def _state_with_findings(n_per_sq: int = 3) -> R.ResearchState:
    st = R.ResearchState()
    st.question = "What is photosynthesis?"
    st.depth = "standard"
    st.plan = {"sub_questions": [{"id": f"sq{i}", "question": f"aspect {i}?"}
                                 for i in range(1, 4)]}
    for sq in st.plan["sub_questions"]:
        for j in range(n_per_sq):
            st.findings.append(R.Finding(
                claim=f"claim {sq['id']}-{j} " + "detail " * 30,
                evidence="e", source_id="s1", confidence="high",
                sub_question=str(sq["id"]), verdict="supported"))
    st.sources["s1"] = R.Source(id="s1", title="T", url="https://x",
                                publisher="P", date="", type="primary")
    st.contradictions = ["a says X, b says Y"]
    return st


def test_classify_depth_short_questions():
    assert R.classify_depth("what is life?") == "standard"
    assert R.classify_depth("what is lofe?") == "standard"
    assert R.classify_depth("who is Albin Anthony Nagarro") == "standard"
    assert R.classify_depth("capital of france") == "quick"
    assert R.classify_depth("find me details about albin anthony") == "standard"


def test_group_findings_plan_order():
    st = _state_with_findings()
    groups = R._group_findings(st)
    assert len(groups) == 3
    assert [g["title"] for g in groups] == ["aspect 1?", "aspect 2?", "aspect 3?"]
    assert all(len(g["findings"]) == 3 for g in groups)
    # findings for unknown sub-questions land at the end, not dropped
    st.findings.append(R.Finding(claim="x", evidence="", source_id="s1",
                                 sub_question="other"))
    assert len(R._group_findings(st)) == 4


def test_synthesize_sections_assembles_report():
    import asyncio
    st = _state_with_findings()
    gw = _FakeGateway()
    report = asyncio.run(R._synthesize_sections(
        gw, None, ["fake/model"], st.question, st,
        R._group_findings(st), lambda ev: None))
    assert "## Executive summary" in report
    assert "Disagreements and uncertainty" in report
    assert "Confidence" in report
    # summary + 3 section writers fired in parallel; 600-word sections
    # are above the thin threshold so no expansion pass fires
    assert len(gw.calls) == 4
    assert len(report.split()) > 1000
    assert "Methodology and evidence base" in report


def test_synthesize_sections_expands_thin_sections():
    import asyncio
    st = _state_with_findings()
    gw = _FakeGateway(text="## Thin\n\n" + "word " * 120)
    report = asyncio.run(R._synthesize_sections(
        gw, None, ["fake/model"], st.question, st,
        R._group_findings(st), lambda ev: None))
    # summary + 3 sections + 3 expansion retries
    assert len(gw.calls) == 7
    assert "Disagreements and uncertainty" in report


def test_synthesize_uses_sectioned_path(monkeypatch):
    import asyncio
    st = _state_with_findings()
    gw = _FakeGateway()
    out = asyncio.run(R._synthesize(
        gw, None, ["fake/model"], st.question, st, lambda ev: None))
    assert "## Executive summary" in out


def test_synthesize_sectioned_empty_falls_back(monkeypatch):
    import asyncio
    st = _state_with_findings()

    async def _empty(*a, **k):
        return ""
    monkeypatch.setattr(R, "_synthesize_sections", _empty)

    async def _one_shot(*a, **k):
        return "Single-shot answer body."
    monkeypatch.setattr(R, "_call", _one_shot)
    out = asyncio.run(R._synthesize(
        _FakeGateway(), None, ["fake/model"], st.question, st,
        lambda ev: None))
    assert "Single-shot answer body." in out


def test_is_outage_matches_provider_failures():
    assert R._is_outage("All 4 provider key(s) are on cooldown after upstream "
                        "errors — soonest retry in ~0m59s")
    assert R._is_outage(RuntimeError("No providers have an enabled API key"))
    assert not R._is_outage("ConnectTimeout")
    assert not R._is_outage("HTTP 429 rate limited")


def test_call_raises_on_provider_outage():
    import asyncio

    class _CooledGateway:
        async def chat(self, session, payload, **kw):
            raise RuntimeError(
                "All 4 provider key(s) are on cooldown after upstream "
                "errors — soonest retry in ~0m59s")

    try:
        asyncio.run(R._call(
            _CooledGateway(), None, "openrouter/qwen/qwen3-32b",
            [{"role": "user", "content": "hi"}]))
        assert False, "outage should propagate"
    except RuntimeError as e:
        assert "cooldown" in str(e)


def test_call_returns_empty_on_transient_errors():
    import asyncio

    class _FlakyGateway:
        async def chat(self, session, payload, **kw):
            raise RuntimeError("HTTP 502 bad gateway")

    # transient errors still degrade to "" — only outages fail fast
    out = asyncio.run(R._call(
        _FlakyGateway(), None, ["m1", "m2"],
        [{"role": "user", "content": "hi"}]))
    assert out == ""


def test_research_one_reraises_outage(monkeypatch):
    import asyncio

    async def _tool_loop(*a, **kw):
        raise RuntimeError(
            "All 2 provider key(s) are on cooldown after upstream errors")
    import app.services.tools as tools
    monkeypatch.setattr(tools, "run_tool_loop", _tool_loop)

    st = R.ResearchState()
    sq = {"id": "q1", "question": "why?"}
    try:
        asyncio.run(R._research_one(
            _FakeGateway(), None, ["openrouter/qwen/qwen3-32b"],
            sq, "standard", st, lambda ev: None))
        assert False, "outage should propagate"
    except RuntimeError as e:
        assert "cooldown" in str(e)
    assert not st.findings
