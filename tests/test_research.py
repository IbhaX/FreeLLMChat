from __future__ import annotations

import pytest

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
    raw = (
        '{"@context":"https://schema.org","@type":"FAQPage","mainEntity":'
        '[{"acceptedAnswer":{"text":"QUIC is UDP-based."}}]}'
    )
    out = R._clean_answer(raw, st3)
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
        pytest.fail("outage should propagate")
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
        pytest.fail("outage should propagate")
    except RuntimeError as e:
        assert "cooldown" in str(e)
    assert not st.findings


def test_clean_answer_fallback_groups_with_numbered_citations():
    st = R.ResearchState()
    st.plan = {"sub_questions": [{"id": "q1", "question": "How does REST work?"},
                                 {"id": "q2", "question": "How does gRPC work?"}]}
    st.sources["q1:s1"] = R.Source(id="q1:s1", title="REST guide",
                                 url="https://a", type="primary")
    st.sources["q2:s1"] = R.Source(id="q2:s1", title="gRPC docs",
                                 url="https://b", type="blog")
    R._score_sources(st)
    st.findings.append(R.Finding(claim="REST uses plain HTTP verbs", evidence="",
                                 source_id="q1:s1", sub_question="q1"))
    st.findings.append(R.Finding(claim="gRPC streams over HTTP/2", evidence="",
                                 source_id="q2:s1", sub_question="q2"))
    out = R._clean_answer("", st)
    assert "grouped by question" in out
    assert "**How does REST work?**" in out and "**How does gRPC work?**" in out
    # citations are [n] numbers matching the score-sorted source order,
    # never internal ids like [q1:s1]
    assert "[q1:s1]" not in out and "[q2:s1]" not in out
    assert "REST uses plain HTTP verbs [1]" in out
    assert "gRPC streams over HTTP/2 [2]" in out


def test_gap_check_skips_when_coverage_complete():
    import asyncio
    st = _state_with_findings()
    st.contradictions = []
    calls: list = []

    class _CountingGateway:
        async def chat(self, session, payload, **kw):
            calls.append(payload)
            return ({"choices": [{"message": {"content": "{}"}}]},
                    "fake", None, "fake/model")

    events: list = []
    out = asyncio.run(R._gap_check(
        _CountingGateway(), None, ["fake/model"], st, events.append))
    assert out is None
    assert not calls  # no LLM call made at all
    assert any("coverage looks complete" in (e.get("text") or "")
               for e in events)


def test_tool_loop_uses_custom_closing_message():
    import asyncio

    from app.services.tools import run_tool_loop

    seen: list[list[dict]] = []

    class _ToolGateway:
        async def chat(self, session, payload, **kw):
            seen.append(list(payload.get("messages") or []))
            if "tools" in payload:
                return ({"choices": [{"message": {
                    "content": "",
                    "tool_calls": [{"id": "c1", "type": "function",
                                    "function": {"name": "get_datetime",
                                                 "arguments": "{}"}}]}}]},
                        "fake", None, "fake/model")
            return ({"choices": [{"message": {"content": '{"findings":[]}'}}]},
                    "fake", None, "fake/model")

    payload = {"model": "m",
               "messages": [{"role": "user", "content": "research this"}]}
    asyncio.run(run_tool_loop(
        _ToolGateway(), None, payload, ["get_datetime"],
        max_rounds=1, closing_message="RETURN JSON NOW."))
    closing = seen[-1][-1]
    assert closing["role"] == "user" and closing["content"] == "RETURN JSON NOW."


def test_merge_findings_alternate_shapes():
    st = R.ResearchState()
    # findings under "results" with "text" claims and "references" sources
    R._merge_findings(st, {
        "results": [{"text": "Proxima b orbits in the habitable zone",
                     "source": "r1", "detail": "ESI 0.87"}],
        "references": [{"id": "r1", "url": "https://nasa/x", "type": "academic"}],
    }, "q1")
    assert len(st.findings) == 1
    assert st.findings[0].claim.startswith("Proxima")
    assert st.findings[0].evidence == "ESI 0.87"

    # findings nested inside an "answer" object
    st2 = R.ResearchState()
    R._merge_findings(st2, {
        "answer": {"findings": [{"claim": "gRPC uses HTTP/2", "source_id": "s1"}],
                   "sources": [{"id": "s1", "url": "https://grpc"}]},
    }, "q2")
    assert len(st2.findings) == 1 and st2.findings[0].sub_question == "q2"
    assert "s1" in st2.sources


def test_research_one_salvages_wrong_shape_json(monkeypatch):
    import asyncio

    async def _tool_loop(*a, **kw):
        return {"choices": [{"message": {"content":
            '{"report": "Plato wrote about love in the Symposium.",'
            ' "notes": ["eros vs agape"]}'}}]}
    import app.services.tools as tools
    monkeypatch.setattr(tools, "run_tool_loop", _tool_loop)

    st = R.ResearchState()
    notes = []
    asyncio.run(R._research_one(
        _FakeGateway(), None, ["m1"],
        {"id": "q1", "question": "love in Greek philosophy?"}, "standard",
        st, lambda ev: notes.append(ev)))
    assert st.findings, "valid-but-wrong-shape JSON must still salvage a finding"
    assert "Plato" in st.findings[0].claim
    assert any("no findings key" in u for u in st.unverified)


def test_atomize_findings_splits_and_dedupes():
    st = R.ResearchState()
    st.findings = [
        R.Finding(claim="REST uses HTTP verbs. It is stateless.",
                  evidence="e1", source_id="s1", sub_question="q1"),
        R.Finding(claim="it is stateless", evidence="e2", source_id="s2",
                  sub_question="q2", source_guessed=True),
        R.Finding(claim="REST uses HTTP verbs", evidence="e3",
                  source_id="s3", sub_question="q3"),
    ]
    R._atomize_findings(st)
    claims = [f.claim for f in st.findings]
    assert len(claims) == 2
    assert "REST uses HTTP verbs." in claims or "REST uses HTTP verbs" in claims
    dup = next(f for f in st.findings if "stateless" in f.claim.lower())
    assert dup.source_id == "s1" and "s2" in dup.corroborated_by
    verb = next(f for f in st.findings if "HTTP verbs" in f.claim)
    assert "s3" in verb.corroborated_by


def test_backfill_marks_guessed_sources():
    st = R.ResearchState()
    st.harvested["q1"] = ["https://example.com/a"]
    R._merge_findings(st, {"findings": [{"claim": "c1", "source_id": ""}],
                           "sources": []}, "q1")
    R._backfill_sources(st)
    assert st.findings[0].source_id in st.sources
    assert st.findings[0].source_guessed is True


def test_ledger_findings_separates_rejected():
    fs = [
        R.Finding(claim="good", evidence="", verdict="supported"),
        R.Finding(claim="half", evidence="", verdict="partially_supported"),
        R.Finding(claim="bad", evidence="", verdict="unsupported"),
        R.Finding(claim="worse", evidence="", verdict="contradicted"),
        R.Finding(claim="unknown", evidence="", verdict=""),
    ]
    kept, rejected = R._ledger_findings(fs)
    assert {f.claim for f in kept} == {"good", "half", "unknown"}
    assert {f.claim for f in rejected} == {"bad", "worse"}


def test_verify_fetches_sources_and_applies_verdicts(monkeypatch):
    import asyncio

    st = R.ResearchState()
    st.sources["s1"] = R.Source(id="s1", title="t", url="https://page/x")
    st.sources["s2"] = R.Source(id="s2", title="t2", url="https://dead/y")
    st.findings = [
        R.Finding(claim="claim zero", evidence="", source_id="s1"),
        R.Finding(claim="claim one", evidence="", source_id="s2"),
    ] + [R.Finding(claim=f"extra {i}", evidence="", source_id="s1")
         for i in range(R._VERIFY_MAX_CLAIMS)]

    async def _fetch(url):
        return "page text" if "page" in url else ""
    monkeypatch.setattr(R, "_fetch_excerpt", _fetch)

    async def _call(*a, **kw):
        msgs = kw.get("msgs") or (a[3] if len(a) > 3 else [])
        content = msgs[0]["content"]
        assert '"excerpt"' in content or "excerpt" in content
        return ('[{"claim_id": "c0", "verdict": "supported"},'
                ' {"claim_id": "c1", "verdict": "cannot_verify"}]')
    monkeypatch.setattr(R, "_call", _call)

    asyncio.run(R._verify(_FakeGateway(), None, ["m"], st, lambda ev: None))
    assert st.findings[0].verdict == "supported"
    assert st.findings[1].verdict == "cannot_verify"
    # claims beyond the verifier's slice are marked, not left blank
    assert st.findings[-1].verdict == "cannot_verify"
    assert st.verify_stats["sources_fetched"] == 1
    assert st.verify_stats["claims_checked"] == R._VERIFY_MAX_CLAIMS


def test_synthesize_sections_excludes_rejected_claims():
    import asyncio
    st = _state_with_findings()
    st.findings.append(R.Finding(
        claim="a fabricated claim", evidence="", source_id="s1",
        sub_question="sq1", verdict="unsupported"))
    calls = []

    class _CapturingGateway:
        async def chat(self, session, payload, **kw):
            calls.append(payload["messages"][-1]["content"])
            return ({"choices": [{"message": {"content":
                "## Aspect\n\n" + "word " * 600}}]}, "fake", None, "fake/model")

    asyncio.run(R._synthesize_sections(
        _CapturingGateway(), None, ["m"], st.question, st,
        R._group_findings(st), lambda ev: None))
    section_payloads = [c for c in calls if '"aspect"' in c]
    for p in section_payloads:
        assert '"fabricated claim"' not in p or "rejected_claims" in p
        import json as _json
        data = _json.loads(p.split("\n\nThe previous draft")[0])
        kept = [f["claim"] for f in data["findings"]]
        assert "a fabricated claim" not in kept


def test_crosscheck_records_disagreements(monkeypatch):
    import asyncio

    st = _state_with_findings()

    async def _call(*a, **kw):
        return ('[{"topic": "speed of X", "claim_ids": ["c0", "c4"],'
                ' "explanation": "s1 says 10 m/s, s2 says 12 m/s — '
                'different measurement dates", "severity": "major",'
                ' "resolution": "more_recent_wins"}]')
    monkeypatch.setattr(R, "_call", _call)

    asyncio.run(R._crosscheck(_FakeGateway(), None, ["m"], st,
                              lambda ev: None))
    assert len(st.contradiction_report) == 1
    assert st.contradiction_report[0]["severity"] == "major"
    # fed into the synthesis-facing contradictions list too
    assert any("speed of X" in c for c in st.contradictions)


def test_crosscheck_skips_tiny_finding_sets():
    import asyncio
    st = R.ResearchState()
    st.findings = [R.Finding(claim="a", evidence=""),
                   R.Finding(claim="b", evidence="")]
    asyncio.run(R._crosscheck(_FakeGateway(), None, ["m"], st,
                              lambda ev: None))
    assert st.contradiction_report == []


def test_qa_gate_static_checks():
    st = R.ResearchState()
    st.sources["s1"] = R.Source(id="s1", title="t", url="https://x")
    st.answer = ("## Executive summary\n\nClaim [1] and dangling [7].\n\n"
                 "## Body\n\ntext")
    issues = R._qa_static_checks(st.answer, st)
    assert issues["bad_citations"] == [7]

    st2 = R.ResearchState()
    st2.answer = ('{"findings": [{"claim": "x"}]}')
    assert "raw_artifacts" in R._qa_static_checks(st2.answer, st2)


def test_qa_gate_marks_unverified_on_critical(monkeypatch):
    import asyncio

    st = R.ResearchState()
    st.sources["s1"] = R.Source(id="s1", title="t", url="https://x")
    st.answer = ("## Executive summary\n\nThe answer [9].\n\n"
                 "## Details\n\nbody [1].\n\n```nova-response\n{}\n```")

    async def _call(*a, **kw):
        return '{"consistent": true, "issues": []}'
    monkeypatch.setattr(R, "_call", _call)

    asyncio.run(R._qa_gate(_FakeGateway(), None, ["m"], "q?", st,
                           lambda ev: None))
    assert st.qa["passed"] is False
    assert "bad_citations" in st.qa["critical"]
    assert "## Report status" in st.answer
    assert "unverified" in st.answer
    # status lands before the envelope fence, not after it
    assert st.answer.index("## Report status") < st.answer.index("```nova-response")


def test_qa_gate_passes_clean_report(monkeypatch):
    import asyncio

    st = R.ResearchState()
    st.sources["s1"] = R.Source(id="s1", title="t", url="https://x")
    st.answer = "## Executive summary\n\nFine [1].\n\n## Details\n\nMore [1]."

    async def _call(*a, **kw):
        return '{"consistent": true, "issues": []}'
    monkeypatch.setattr(R, "_call", _call)

    asyncio.run(R._qa_gate(_FakeGateway(), None, ["m"], "q?", st,
                           lambda ev: None))
    assert st.qa["passed"] is True
    assert "## Report status" not in st.answer


def test_canon_url_dedupes_tracking_params():
    st = R.ResearchState()
    id_map = {}
    R._merge_findings_sources_only(st, [
        {"id": "s1", "url": "https://www.Example.com/a/?utm_source=x&ref_src=y"},
    ], "q1", id_map)
    R._merge_findings_sources_only(st, [
        {"id": "s2", "url": "https://example.com/a"},
    ], "q2", id_map)
    assert len(st.sources) == 1
    assert id_map["s2"] == id_map["s1"] == "s1"
    # authors/doi/access metadata captured
    R._merge_findings_sources_only(st, [
        {"id": "s3", "url": "https://doi.org/10.1/x", "authors": "Doe",
         "doi": "10.1/x", "access": "open", "type": "academic"},
    ], "q3", id_map)
    s3 = st.sources["s3"]
    assert s3.authors == "Doe" and s3.doi == "10.1/x" and s3.access == "open"


def test_classify_evidence_classes():
    st = R.ResearchState()
    st.sources["dead"] = R.Source(id="dead", url="https://x",
                                  fetched="unreachable")
    st.sources["ok"] = R.Source(id="ok", url="https://y", fetched="fetched")
    st.findings = [
        R.Finding(claim="a", evidence="", source_id="ok",
                  verdict="supported", corroborated_by=["s2"]),
        R.Finding(claim="b", evidence="", source_id="ok",
                  verdict="supported"),
        R.Finding(claim="c", evidence="", source_id="ok",
                  verdict="partially_supported"),
        R.Finding(claim="d", evidence="", source_id="ok",
                  verdict="contradicted"),
        R.Finding(claim="e", evidence="", source_id="dead",
                  verdict="cannot_verify"),
        R.Finding(claim="f", evidence="", source_id="",
                  verdict="cannot_verify"),
        R.Finding(claim="g", evidence="", source_id="ok",
                  verdict="cannot_verify"),
    ]
    R._classify_evidence(st)
    got = {f.claim: f.evidence_class for f in st.findings}
    assert got == {
        "a": "corroborated", "b": "verified", "c": "partially_supported",
        "d": "contradicted", "e": "inaccessible", "f": "not_found",
        "g": "unverified",
    }


def test_score_sources_retrievability():
    st = R.ResearchState()
    st.sources["a"] = R.Source(id="a", url="https://x", type="news",
                               fetched="fetched")
    st.sources["b"] = R.Source(id="b", url="https://y", type="news",
                               fetched="unreachable")
    st.sources["c"] = R.Source(id="c", url="https://z", type="news")
    R._score_sources(st)
    assert st.sources["a"].score > st.sources["c"].score
    assert st.sources["b"].score < st.sources["c"].score


def test_research_one_notes_when_empty(monkeypatch):
    import asyncio

    async def _tool_loop(*a, **kw):
        return {"choices": [{"message": {"content": ""}}]}
    import app.services.tools as tools
    monkeypatch.setattr(tools, "run_tool_loop", _tool_loop)

    st = R.ResearchState()
    notes = []
    asyncio.run(R._research_one(
        _FakeGateway(), None, ["m1"],
        {"id": "q3", "question": "?"}, "standard",
        st, lambda ev: notes.append(ev)))
    assert not st.findings
    assert any("no usable findings" in n.get("text", "") for n in notes)
