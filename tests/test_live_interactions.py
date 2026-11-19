"""Focused regressions for the live agent-interaction contract.

Each test documents: TC id, purpose, preconditions, inputs, steps, expected.
All boundaries (gateway, tool execution) are scripted; no network is used.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest

from app.services import activity
from app.services import model_policy as mp
from app.services import roleplay as rp
from app.services import tools as tl
from tests.test_activity import patched_factory  # noqa: F401


def _sse_line(content: str) -> bytes:
    import json
    body = json.dumps({"choices": [{"delta": {"content": content}}]},
                      ensure_ascii=False)
    return f"data: {body}\n\n".encode("utf-8")


async def _chunks(parts: list[bytes]) -> AsyncIterator[bytes]:
    for p in parts:
        yield p


class _StreamGW:
    """Scripted streaming gateway. ``plans`` maps model -> chunks or Exception."""

    def __init__(self, plans: dict[str, Any]) -> None:
        self.plans = plans
        self.calls: list[str] = []
        self.closed: list[str] = []

    async def chat_stream(self, session: Any, payload: dict[str, Any],
                          on_event: Any = None) -> tuple[Any, Any, Any, str]:
        model = str(payload["model"])
        self.calls.append(model)
        plan = self.plans[model]
        if isinstance(plan, Exception):
            raise plan

        async def gen() -> AsyncIterator[bytes]:
            try:
                for part in plan:
                    if isinstance(part, Exception):
                        raise part
                    yield part
            finally:
                self.closed.append(model)
        return gen(), None, None, model


class _BufferedGW:
    """Gateway without chat_stream: exercises the buffered candidate path."""

    def __init__(self, fail: set[str]) -> None:
        self.fail = fail
        self.calls: list[str] = []

    async def chat(self, session: Any, payload: dict[str, Any],
                   on_event: Any = None, **kw: Any) -> tuple[Any, Any, Any, str]:
        model = str(payload["model"])
        self.calls.append(model)
        if model in self.fail:
            raise RuntimeError("provider down")
        return ({"choices": [{"message": {"content": f"answer from {model}"}}]},
                None, None, model)


# ---------------------------------------------------------------- SSE parsing

def test_tc_sse_01_split_multibyte_and_json_across_chunks() -> None:
    """TC-SSE-01 — Purpose: chunk-split UTF-8 and JSON reassemble losslessly.

    Preconditions: none (pure parser).
    Inputs: one SSE event whose 'é' (2 bytes) and JSON body are split across
    single-byte chunks, then a second event.
    Steps: feed bytes one at a time, then flush.
    Expected: content pieces are exactly 'café' and ' ok', in order.
    """
    raw = _sse_line("café") + _sse_line(" ok") + b"data: [DONE]\n\n"
    parser = mp.SSEDeltaParser()
    out: list[str] = []
    for i in range(len(raw)):
        out.extend(parser.feed(raw[i:i + 1]))
    out.extend(parser.flush())
    assert "".join(out) == "café ok"


def test_tc_sse_02_reasoning_fields_never_forwarded() -> None:
    """TC-SSE-02 — Purpose: reasoning deltas are never shown as answer text.

    Inputs: a delta carrying only ``reasoning_content`` and one with content.
    Expected: only the content text is emitted.
    """
    import json
    reasoning = json.dumps({"choices": [{"delta": {"reasoning_content": "hmm"}}]})
    parser = mp.SSEDeltaParser()
    pieces = parser.feed(f"data: {reasoning}\n\n".encode()) + \
        parser.feed(_sse_line("visible"))
    assert pieces == ["visible"]


# ------------------------------------------------------- stream_completion

def test_tc_stream_01_failover_only_before_output() -> None:
    """TC-STREAM-01 — Purpose: one failover before first text, none after.

    Preconditions: candidates [bad, good]; 'bad' raises before output.
    Expected: 'good' answers and 'bad' is tried exactly once.
    """
    gw = _StreamGW({"bad": RuntimeError("boom"),
                    "good": [_sse_line("hello")]})
    res = asyncio.run(mp.stream_completion(
        gw, None, {"messages": []}, candidates=["bad", "good"],
        timeout_s=5))
    assert res.text == "hello"
    assert gw.calls == ["bad", "good"]


def test_tc_stream_02_mid_stream_failure_degrades_without_replay() -> None:
    """TC-STREAM-02 — Purpose: partial output is kept, never replayed.

    Preconditions: 'first' yields text then raises mid-stream; 'second' healthy.
    Expected: result is degraded with the partial text; 'second' never called.
    """
    gw = _StreamGW({"first": [_sse_line("part"), RuntimeError("cut")],
                    "second": [_sse_line("other")]})
    res = asyncio.run(mp.stream_completion(
        gw, None, {"messages": []}, candidates=["first", "second"],
        timeout_s=5))
    assert res.text == "part"
    assert res.degraded is True
    assert gw.calls == ["first"]


def test_tc_stream_03_cancel_closes_iterator() -> None:
    """TC-STREAM-03 — Purpose: cancellation closes the provider iterator.

    Preconditions: 'slow' yields one chunk then blocks.
    Steps: start the call, wait for the first delta, cancel the task.
    Expected: the stream generator's finally ran (closed recorded).
    """
    started = asyncio.Event()

    async def run() -> None:
        async def gen() -> AsyncIterator[bytes]:
            try:
                yield _sse_line("x")
                await asyncio.Event().wait()
            finally:
                closed.append("slow")

        closed: list[str] = []

        class _GW:
            async def chat_stream(self, session: Any, payload: dict[str, Any],
                                  on_event: Any = None) -> tuple[Any, Any, Any, str]:
                return gen(), None, None, "slow"

        task = asyncio.ensure_future(mp.stream_completion(
            _GW(), None, {"messages": []}, candidates=["slow"],
            on_delta=lambda _p: started.set(), timeout_s=30))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed == ["slow"]

    asyncio.run(run())


def test_tc_stream_04_buffered_fallback_honors_explicit_candidates() -> None:
    """TC-STREAM-04 — Purpose: non-streaming gateways use the given candidates.

    Preconditions: gateway has no chat_stream; first candidate fails.
    Expected: the second explicit candidate answers; unrelated auto list unused.
    """
    gw = _BufferedGW(fail={"alpha"})
    deltas: list[str] = []
    res = asyncio.run(mp.stream_completion(
        gw, None, {"messages": []}, candidates=["alpha", "beta"],
        on_delta=deltas.append, timeout_s=5))
    assert res.text == "answer from beta"
    assert gw.calls == ["alpha", "beta"]
    assert deltas == ["answer from beta"]


# ------------------------------------------------------ activity boundary

def test_tc_act_01_legacy_status_normalized_to_canonical() -> None:
    """TC-ACT-01 — Purpose: legacy 'ok' never survives as a status.

    Inputs: a step_done event with status 'ok' and a custom title.
    Expected: status == 'done'; producer title preserved.
    """
    ev = activity.enrich({"event": "step_done", "task_id": "t1",
                          "status": "ok", "title": "Custom"})
    assert ev["status"] == "done"
    assert ev["title"] == "Custom"


def test_tc_act_02_enriched_answer_keeps_answer_channel() -> None:
    """TC-ACT-02 — Purpose: enriched presentation kind never hides channels.

    Inputs: answer_delta / finding events whose enriched kind is 'write'/'note'.
    Expected: channels are 'answer' and 'finding' respectively.
    """
    assert activity._channel_for({"event": "answer_delta", "kind": "write"}) == "answer"
    assert activity._channel_for({"event": "finding", "kind": "note"}) == "finding"
    assert activity._channel_for({"event": "step_done"}) == "activity"


# ------------------------------------------------------------- tool boundary

def test_tc_tool_01_article_body_word_is_not_a_failure() -> None:
    """TC-TOOL-01 — Purpose: classify by prefix, not body text.

    Inputs: an HTTP 200 body containing the word 'unavailable'.
    Expected: success; a real Exa unavailable prefix is a failure.
    """
    ok, _ = tl._tool_outcome("http_get", "HTTP 200\nService unavailable on Sundays")
    assert ok is True
    ok2, _ = tl._tool_outcome("exa_search", "exa_search unavailable: no key")
    assert ok2 is False


def test_tc_tool_02_unadvertised_tool_never_executes(monkeypatch: pytest.MonkeyPatch) -> None:
    """TC-TOOL-02 — Purpose: allowlist blocks execution at the call boundary.

    Preconditions: run_tool is replaced by a recorder.
    Inputs: a tool call for 'shell_exec' while only 'web_search' is allowed.
    Expected: blocked error text, recorder never called.
    """
    calls: list[str] = []

    async def recorder(name: str, args: dict[str, Any]) -> str:
        calls.append(name)
        return "should not run"

    monkeypatch.setattr(tl, "run_tool", recorder)
    tc = {"id": "c1", "type": "function",
          "function": {"name": "shell_exec", "arguments": "{}"}}
    res = asyncio.run(tl._timed_tool_call(tc, allowed={"web_search"}))
    assert "not in the allowed tool set" in res[3]
    assert calls == []


def test_tc_tool_03_cancelled_call_emits_cancelled_not_success(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """TC-TOOL-03 — Purpose: a cancelled observed call never reports success.

    Inputs: a tool that blocks until cancelled.
    Expected: the emitted completion has status 'cancelled' and ok False.
    """
    events: list[dict[str, Any]] = []

    async def blocker(name: str, args: dict[str, Any]) -> str:
        await asyncio.Event().wait()
        return "never"

    monkeypatch.setattr(tl, "run_tool", blocker)

    async def run() -> None:
        task = asyncio.ensure_future(tl.execute_observed_tool(
            "web_search", {"query": "x"}, emit=events.append))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    done = [e for e in events if e.get("event") == "tool"]
    assert done and done[-1]["status"] == "cancelled" and done[-1]["ok"] is False


# ----------------------------------------------------- roleplay boundaries

def test_tc_rp_01_components_only_envelope_yields_readable_text() -> None:
    """TC-RP-01 — Purpose: a components-only envelope never leaks raw JSON.

    Inputs: envelope with empty content and one table component.
    Expected: readable text holds the table title, no fence or JSON keys.
    """
    raw = ('```nova-response\n{"response_type":"table","content":"",'
           '"components":[{"type":"table","title":"Compare",'
           '"rows":[["a","b"]]}]}\n```')
    fin = rp._finalize_reply(raw, name="X", fallback="fallback")
    assert "```" not in fin.text and "response_type" not in fin.text
    assert "Compare" in fin.text


def test_tc_rp_02_plain_and_empty_replies_are_safe() -> None:
    """TC-RP-02 — Purpose: plain text passes through; empty uses fallback.

    Expected: 'plain words' kept verbatim; '' becomes the fallback; both carry
    a general envelope with an empty components list.
    """
    plain = rp._finalize_reply("plain words", name="X", fallback="fb")
    empty = rp._finalize_reply("", name="X", fallback="fb")
    assert plain.text == "plain words"
    assert plain.envelope["content"] == ""
    assert plain.envelope["components"] == [
        {"type": "paragraph", "data": {"text": "plain words"}}]
    assert empty.text == "fb"


def test_tc_rp_03_greeting_gate_requires_small_talk_or_opener() -> None:
    """TC-RP-03 — Purpose: only trivial beats may replay a greeting.

    Inputs: 'hi', 'start the scene' (True); 'hi, research x', 'write code' (False).
    """
    assert rp._opening_beat("hi") is True
    assert rp._opening_beat("start the scene") is True
    assert rp._opening_beat("hi, research the moon") is False
    assert rp._opening_beat("write code") is False


def test_tc_rp_04_lookup_question_is_research() -> None:
    """TC-RP-04 — Purpose: 'who is …' is a real-world lookup, fiction is not.

    Expected: research for the lookup question; not for ordinary dialogue.
    """
    assert rp._research_requested("who is albin anthony from nagarro?") is True
    assert rp._research_requested("the dragon sneezes softly") is False


def test_tc_rp_05_page_excerpt_strips_status_and_scripts() -> None:
    """TC-RP-05 — Purpose: only 2xx/3xx bodies become readable excerpts.

    Expected: script content removed, status line removed, 404 rejected.
    """
    html = ("HTTP 200\n<html><body><script>evil()</script>"
            "<p>Visible fact</p></body></html>")
    text = rp._page_excerpt(html, "https://example.org/a")
    assert "Visible fact" in text and "evil" not in text and "HTTP" not in text
    assert rp._page_excerpt("HTTP 404\nnope", "https://example.org/a") == ""


def test_tc_rp_06_ambiguous_pronoun_never_searches(monkeypatch: pytest.MonkeyPatch) -> None:
    """TC-RP-06 — Purpose: a pronoun with no prior subject is not searched.

    Preconditions: empty prior transcript; web_search recorder.
    Inputs: 'what did he say about it?'
    Expected: no tool call; clarification context; no sources.
    """
    calls: list[str] = []

    async def recorder(name: str, args: dict[str, Any]) -> str:
        calls.append(name)
        return ""

    monkeypatch.setattr(tl, "run_tool", recorder)
    room = SimpleNamespace(id=1, tools_enabled=True)
    char = SimpleNamespace(id=2, name="Holmes")
    ctx, sources = asyncio.run(rp._research_prefetch(
        None, None, "what did he say about it?", room, char,
        emit=lambda ev: None, prior=[]))
    assert calls == []
    assert sources == []
    assert "no referent" in ctx


# ------------------------------------------------ component finalization

def test_tc_comp_01_markdown_table_becomes_native_component() -> None:
    """TC-COMP-01 — Purpose: a content-only envelope with a markdown table
    becomes a real table component; prose stays in content.

    Inputs: content-only envelope whose content holds prose + a 2-row table.
    Expected: components_count > 0, a 'table' component with 2 rows, content
    keeps the prose and no fabricated extra facts appear.
    """
    md = ("Here are the options.\n\n| Option | Summary |\n|---|---|\n"
          "| Baseline | Primary [1] |\n| Alt | Secondary [2] |\n")
    raw = ("```nova-response\n" + json.dumps({
        "response_type": "general", "content": md, "components": []})
           + "\n```")
    fin = rp._finalize_reply(raw, name="X", fallback="fb")
    comps = fin.envelope["components"]
    tables = [c for c in comps if c["type"] == "table"]
    assert len(comps) > 0 and len(tables) == 1
    assert len(tables[0]["data"]["rows"]) == 2
    assert fin.envelope["content"] == "Here are the options."
    assert "Primary [1]" in json.dumps(tables[0])


def test_tc_comp_02_plain_sourced_prose_uses_paragraph_and_search_card() -> None:
    """TC-COMP-02 — Purpose: plain sourced prose is kept as readable
    paragraph content with the sources shown once as a search_result card.

    Inputs: plain prose with [1] citation and one safe http(s) source.
    Expected: a non-empty paragraph component; exactly one search_result with
    the source; no envelope-level sources duplicate; FinalReply.sources kept.
    """
    src = [{"title": "Primary", "url": "https://example.org/primary"}]
    fin = rp._finalize_reply("Plain sourced answer [1].\n\nSecond point.",
                             name="X", fallback="fb", sources=src)
    comps = fin.envelope["components"]
    paras = [c for c in comps if c["type"] == "paragraph"]
    cards = [c for c in comps if c["type"] == "search_result"]
    assert paras and paras[0]["data"]["text"].startswith("Plain sourced answer [1]")
    assert len(cards) == 1
    assert cards[0]["data"]["results"] == src
    assert "sources" not in fin.envelope
    assert fin.sources == src


def test_tc_comp_03_bare_code_block_preserved_as_code_component() -> None:
    """TC-COMP-03 — Purpose: fenced code is a code component, byte-for-byte.

    Inputs: bare fenced python block.
    Expected: one code component with language 'python' and the exact code.
    """
    fin = rp._finalize_reply("```python\nprint(1)\n```", name="X", fallback="fb")
    assert fin.envelope["components"] == [
        {"type": "code", "data": {"language": "python", "code": "print(1)"}}]


def test_tc_comp_04_model_table_unchanged_and_not_duplicated() -> None:
    """TC-COMP-04 — Purpose: a model-supplied table is kept as-is, not
    re-derived, and sources are not duplicated.

    Inputs: envelope with one model table and content prose; one source.
    Expected: the table component equals the model's; exactly one
    search_result; the readable text carries the table cells once.
    """
    table = {"type": "table", "data": {"columns": ["A"], "rows": [["x"]]}}
    raw = ("```nova-response\n" + json.dumps({
        "response_type": "general", "content": "Intro.",
        "components": [table]}) + "\n```")
    src = [{"title": "P", "url": "https://example.org/p"}]
    fin = rp._finalize_reply(raw, name="X", fallback="fb", sources=src)
    assert [c for c in fin.envelope["components"] if c["type"] == "table"] == [table]
    assert len([c for c in fin.envelope["components"]
                if c["type"] == "search_result"]) == 1


def test_tc_comp_05_empty_and_malformed_fall_back_without_raw_json() -> None:
    """TC-COMP-05 — Purpose: blank or broken replies never show protocol JSON.

    Inputs: '' and a malformed nova-response fence.
    Expected: readable fallback text, no 'nova-response' or '{' protocol leak.
    """
    empty = rp._finalize_reply("", name="X", fallback="fb")
    bad = rp._finalize_reply('```nova-response\n{"content": "ok",', name="X",
                             fallback="fb")
    assert empty.text == "fb"
    assert "nova-response" not in bad.text and not bad.text.lstrip().startswith("{")


def test_tc_rp_07_prior_subject_skips_pronoun_followups() -> None:
    """TC-RP-07 — Purpose: follow-ups use the nearest subject-bearing prior
    question, never a pronoun-only follow-up like 'does he have a sister?'.

    Preconditions: visible talk history with a named subject, then two
    pronoun follow-ups; web search recorded; exa treated as not configured.
    Inputs: current beat 'what about his work life?'.
    Expected: the query contains 'Albin Anthony' and 'Nagarro', not 'sister'.
    """
    prior = [
        {"role": "user", "kind": "talk", "text": "who is Albin Anthony from Nagarro?"},
        {"role": "character", "text": "reply"},
        {"role": "user", "kind": "talk", "text": "does he have a sister?"},
        {"role": "character", "text": "reply"},
        {"role": "user", "kind": "talk", "text": "tell me more about his sister"},
    ]
    queries: list[str] = []

    async def recorder(name: str, args: dict[str, Any]) -> str:
        if name == "web_search":
            queries.append(str(args.get("query")))
        return ""

    async def no_exa() -> str:
        return ""

    room = SimpleNamespace(id=1, tools_enabled=True)
    char = SimpleNamespace(id=2, name="Holmes")
    old_run, old_key = tl.run_tool, tl._exa_api_key
    tl.run_tool, tl._exa_api_key = recorder, no_exa
    try:
        asyncio.run(rp._research_prefetch(
            None, None, "what about his work life?", room, char,
            emit=lambda ev: None, prior=prior))
    finally:
        tl.run_tool, tl._exa_api_key = old_run, old_key
    assert len(queries) == 1
    assert "Albin Anthony" in queries[0] and "Nagarro" in queries[0]
    assert "sister" not in queries[0].lower()


# ------------------------------------------------ event identity / ledger

def test_tc_id_01_prepare_keeps_identified_event_and_copies() -> None:
    """TC-ID-01 — Purpose: identified events keep their identity; the producer
    dict is never mutated and seq never moves backwards.

    Inputs: one already-identified event (run_id/seq/event_id) from another
    run, plus an unidentified event on this emitter.
    Expected: identified fields unchanged; input dict untouched; the
    unidentified event gets this run's identity with the next seq.
    """
    from app.services.activity import InteractionEmitter
    got: list[dict[str, Any]] = []
    em = InteractionEmitter(got.append, run_id="runA")
    raw = {"event": "step_start", "task_id": "t1", "run_id": "other",
           "seq": 7, "event_id": "other:7", "channel": "activity"}
    snapshot = dict(raw)
    em(raw)
    em({"event": "note", "text": "n"})
    assert raw == snapshot
    assert got[0]["run_id"] == "other" and got[0]["seq"] == 7
    assert got[0]["event_id"] == "other:7"
    assert got[1]["run_id"] == "runA" and got[1]["seq"] == 1
    assert got[1]["event_id"] == "runA:1"


def test_tc_id_02_fields_survive_raw_event_without_task() -> None:
    """TC-ID-02 — Purpose: ledger field extraction never KeyErrors on raw
    events and keeps task/parent identity when present.

    Inputs: a raw tool event with task_id/parent_task_id and no normalized keys.
    Expected: payload carries task_id, parent_task_id and event_id.
    """
    from app.services import activity
    fields = activity.to_agent_event_fields({
        "event": "tool", "tool": "web_search", "task_id": "tk",
        "parent_task_id": "rp:1", "event_id": "r:3", "run_id": "r"})
    assert fields["payload"]["task_id"] == "tk"
    assert fields["payload"]["parent_task_id"] == "rp:1"
    assert fields["payload"]["event_id"] == "r:3"


def test_tc_id_03_ledger_roundtrip_preserves_producer_identity(
        patched_factory: Any, session: Any) -> None:
    """TC-ID-03 — Purpose: persisted events list back with producer identity
    (event_id/run_id/seq/task links) and a separate storage cursor ledger_seq.

    Preconditions: temp DB via the shared engine fixture.
    Inputs: one identified tool event written through agent_log.record.
    Expected: listed event keeps event_id, run_id, producer seq, task_id,
    parent_task_id; ledger_seq is the storage cursor.
    """
    from app.services import agent_log

    async def go() -> None:
        run_id = await agent_log.start_run(session, "test", "q?")
        await session.commit()
        ev = {"event": "tool", "tool": "web_search", "task_id": "tk",
              "parent_task_id": "rp:1", "run_id": "rX", "seq": 42,
              "event_id": "rX:42", "text": "searched"}
        await agent_log.record(session, run_id, 1, ev=ev)
        await session.commit()
        listed = await agent_log.list_events(session, run_id)
        assert len(listed) == 1
        item = listed[0]
        assert item["event_id"] == "rX:42"
        assert item["run_id"] == "rX"
        assert item["seq"] == 42
        assert item["task_id"] == "tk"
        assert item["parent_task_id"] == "rp:1"
        assert item["ledger_seq"] == 1
        assert item["id"] == "rX:42"

    asyncio.run(go())


def test_tc_id_04_trace_events_keep_unique_transport_ids() -> None:
    """TC-ID-04 — Purpose: trace records and emitter events interleave without
    reusing an event_id (trace keeps its own trace_seq).

    Inputs: trace step_start, an emitter tool event, then trace step_done, all
    sharing one InteractionEmitter run.
    Expected: transport seq 1,2,3; three unique event_ids; trace_seq 1 and 2.
    """
    from app.services.activity import InteractionEmitter
    from app.services.execution import ExecutionTrace
    out: list[dict[str, Any]] = []
    em = InteractionEmitter(out.append, run_id="R")
    trace = ExecutionTrace(em)
    trace.record("step_start", "expert", "start", task_id="t1")
    em({"event": "tool", "tool": "web_search", "task_id": "k1"})
    trace.record("step_done", "expert", "done", task_id="t1")
    assert [e["seq"] for e in out] == [1, 2, 3]
    assert len({e["event_id"] for e in out}) == 3
    assert [e["trace_seq"] for e in out if "trace_seq" in e] == [1, 2]
    assert all(e["run_id"] == "R" for e in out)


# ------------------------------------------------ dossier (deep research)

from app.models import RoleplayRoom  # noqa: E402
from tests.test_roleplay_turn import _collect, _mk_cast, session_factory  # noqa: E402,F401

_DOSSIER_REPORT = (
    "## Identity\n\nSubject summary sentence [1].\n\n"
    "## Career timeline\n\n"
    + "\n\n".join(f"Dated career paragraph {i} with citation [{(i % 13) + 1}]."
                 for i in range(1, 15))
    + "\n\nLAST-SECTION-SENTINEL: final section content [13].\n\n"
    "```nova-response\n"
    '{"response_type":"general","content":"Short summary [1].","components":[]}'
    "\n```")


def _dossier_fake(calls: list[dict[str, Any]]) -> Any:
    async def fake(gateway: Any, session: Any, messages: list[dict[str, Any]],
                   *, model: str = "auto", conversation_id: str | None = None,
                   depth: str | None = None, on_event: Any = None) -> str:
        calls.append({"messages": messages, "conversation_id": conversation_id,
                      "depth": depth, "model": model})
        on_event({"event": "stage", "text": "planning", "stage": "plan"})
        await asyncio.sleep(0.05)
        on_event({"event": "finding", "result_id": "find:x:001",
                  "claim": "first finding", "evidence_class": "unverified",
                  "task_id": "sq:1"})
        await asyncio.sleep(0.05)
        return _DOSSIER_REPORT
    return fake


def test_tc_dos_01_deep_run_gets_resolved_subject_and_full_report(
        session_factory: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """TC-DOS-01 — Purpose: a life-history request runs the deep engine with
    the resolved subject, visible history, and returns the full report.

    Preconditions: tools enabled; visible prior question names the subject.
    Inputs: prior 'who is Albin Anthony from Nagarro?', then 'give me his
    complete life history'.
    Expected: one run_research call with depth 'deep', query naming Albin
    Anthony, the brief prefix, the visible prior history; final char_end
    text keeps the body before the envelope fence (sentinel present) and the
    first finding frame arrives before char_end.
    """
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(rp, "run_research", _dossier_fake(calls))

    async def go() -> list[dict[str, Any]]:
        async with session_factory() as s:
            sher, _wat = _mk_cast(s)
            await s.flush()
            room = RoleplayRoom(
                name="221B", character_ids=[sher.id], tools_enabled=True,
                messages=[{"id": "a1", "role": "user", "kind": "talk",
                           "text": "who is Albin Anthony from Nagarro?"},
                          {"id": "a2", "role": "character", "kind": "dialogue",
                           "speaker": "Sherlock", "text": "Certainly."}])
            s.add(room)
            await s.commit()
            rid = room.id
        return await _collect(rp.turn_stream(
            object(), session_factory, rid, kind="talk",
            text="give me his complete life history"))

    frames = asyncio.run(go())
    assert len(calls) == 1
    call = calls[0]
    assert call["depth"] == "deep"
    last = call["messages"][-1]
    assert last["role"] == "user" and last["content"].startswith(rp._DOSSIER_BRIEF)
    assert "Albin Anthony" in last["content"] and "sister" not in last["content"]
    assert any("who is Albin Anthony" in str(m.get("content"))
               for m in call["messages"][:-1])
    ends = [f for f in frames if f.get("event") == "char_end"]
    assert len(ends) == 1
    assert "LAST-SECTION-SENTINEL" in ends[0]["text"]
    assert "nova-response" in ends[0]["env_text"]
    names = [f.get("event") for f in frames]
    assert names.index("finding") < names.index("char_end")


def test_finalize_reply_renders_json_fence_with_surrounding_prose() -> None:
    raw = ('Before the table.\n\n```json\n{"response_type":"table","content":"",'
           '"components":[{"type":"table","title":"Compare",'
           '"rows":[["a","b"]]}]}\n```\n\nAfter it.')
    fin = rp._finalize_reply(raw, name="X", fallback="fallback")
    assert any(c.get("type") == "table" for c in fin.envelope["components"])
    assert "response_type" not in fin.text and "```" not in fin.text
    assert "Before the table." in fin.text


def test_finalize_reply_drops_malformed_bare_protocol_json() -> None:
    raw = '{"response_type": "table", "content": "oops", "components": [{"type"'
    fin = rp._finalize_reply(raw, name="X", fallback="fallback")
    assert fin.text == "fallback"
    assert "response_type" not in fin.text
    assert "response_type" not in fin.envelope["content"]


def test_finalize_reply_drops_paragraph_components_repeating_body() -> None:
    import json

    body = "First point stands.\n\nSecond   point\nfollows."
    env = {
        "response_type": "general", "content": body,
        "components": [
            {"type": "paragraph", "data": {"text": "First point stands."}},
            {"type": "paragraph", "data": {"text": "Second point follows."}},
            {"type": "paragraph", "text": "First point stands. Second point follows."},
            {"type": "paragraph", "data": {"text": "First point"}},
            {"type": "table", "title": "Compare", "rows": [["a", "b"]]},
        ],
    }
    raw = "```nova-response\n" + json.dumps(env) + "\n```"
    fin = rp._finalize_reply(raw, name="X", fallback="fb")
    types = [c.get("type") for c in fin.envelope["components"]]
    assert types.count("table") == 1
    paras = [c["data"]["text"] for c in fin.envelope["components"]
             if c.get("type") == "paragraph"]
    assert paras == ["First point"]
    assert fin.envelope["content"] == body
