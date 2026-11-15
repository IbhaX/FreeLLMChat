"""Activity-log v2: normalization contract + ledger persistence."""
from __future__ import annotations

import pytest

from app.services import activity


def n(ev):
    return activity.normalize(ev)


class TestNormalize:
    def test_stage_maps_kind_and_group(self):
        a = n({"event": "stage", "stage": "plan", "text": "Planning research…"})
        assert a["kind"] == "plan" and a["status"] == "running"
        assert a["group"] is True and a["parent"] is None
        a2 = n({"event": "stage", "stage": "synthesize", "text": "Writing…"})
        assert a2["kind"] == "write"
        a3 = n({"event": "stage", "stage": "route", "text": "Routing…"})
        assert a3["kind"] == "decide"

    def test_stage_sub_becomes_parent(self):
        a = n({"event": "note", "stage": "research", "sub": "q2",
               "text": "found 4 sources"})
        assert a["parent"] == "research:q2"

    def test_tool_event_typed_and_timed(self):
        a = n({"event": "tool", "tool": "web_search",
               "args": "nova benchmarks", "ok": True,
               "duration_ms": 842.0})
        assert a["kind"] == "search" and a["status"] == "done"
        assert "Searching the web" in a["title"]
        assert a["meta"]["tool"] == "web_search"
        assert a["duration_ms"] == 842.0

    def test_tool_legacy_text_shape(self):
        a = n({"event": "tool", "text": "🔧 http_get(url=https://x.io)"})
        assert a["kind"] == "read"
        assert a["meta"]["tool"] == "http_get"

    def test_tool_failure(self):
        a = n({"event": "tool", "tool": "fetch_page",
               "result": "fetch error: timeout", "ok": False})
        assert a["status"] == "failed"

    def test_step_lifecycle(self):
        s = n({"event": "step_start", "actor": "deep_researcher",
               "text": "Deep Researcher — gather findings"})
        d = n({"event": "step_done", "actor": "deep_researcher",
               "duration_ms": 3000})
        f = n({"event": "step_fail", "actor": "deep_researcher"})
        assert (s["status"], s["group"]) == ("running", True)
        assert d["status"] == "done" and f["status"] == "failed"
        assert f["kind"] == "error"

    def test_status_override(self):
        a = n({"event": "stage", "stage": "verify",
               "text": "x", "status": "fail"})
        assert a["status"] == "failed"
        b = n({"event": "note", "text": "skipping", "status": "skip"})
        assert b["status"] == "skipped"

    def test_routing_events_verbose(self):
        for ev in ("routing", "try", "skip", "next"):
            assert n({"event": ev, "text": "x"})["level"] == "verbose"
        assert n({"event": "fail", "text": "x"})["status"] == "failed"
        assert n({"event": "try", "text": "x"})["status"] == "waiting"

    def test_error_and_terminal(self):
        assert n({"event": "error", "text": "boom"})["kind"] == "error"
        assert n({"event": "research_done", "text": "done"})["kind"] == "result"
        assert n({"event": "error", "text": "boom"})["status"] == "failed"

    def test_note_fallback_and_error_text(self):
        assert n({"event": "note", "text": "reusing prior findings"})["kind"] == "note"
        assert n({"event": "note", "text": "⚠ empty reply"})["kind"] == "error"

    def test_title_clipped_and_detail(self):
        long = "x" * 500
        a = n({"event": "note", "text": long})
        assert len(a["title"]) <= 160 and a["detail"]

    def test_enrich_merges_into_event(self):
        ev = {"event": "stage", "stage": "research", "text": "Researching…"}
        out = activity.enrich(ev)
        assert out is ev
        assert ev["kind"] == "search" and ev["status"] == "running"
        assert ev["text"] == "Researching…"   # legacy keys kept

    def test_enrich_survives_garbage(self):
        ev = {"event": object()}
        activity.enrich(ev)   # must not raise

    def test_usage_event_model_card(self):
        a = n({"event": "usage", "model": "llama-3.3-70b",
               "provider": "groq", "key_label": "work",
               "tokens_in": 1200, "tokens_out": 90,
               "duration_ms": 4210, "cost_usd": 0.0008})
        assert a["kind"] == "usage" and a["status"] == "done"
        assert "llama-3.3-70b" in a["title"] and "1,290" in a["title"]
        m = a["meta"]
        assert m["tokens_in"] == 1200 and m["tokens_out"] == 90
        assert m["tokens_total"] == 1290 and m["cost_usd"] == 0.0008
        assert a["duration_ms"] == 4210
        # missing pieces degrade gracefully
        b = n({"event": "usage"})
        assert b["kind"] == "usage" and b["meta"]["tokens_total"] == 0

    def test_plan_event_carries_detail(self):
        a = n({"event": "plan", "stage": "plan",
               "text": "Plan ready: 2 sub-question(s)",
               "detail": "1. who\n2. what"})
        assert a["kind"] == "plan" and a["status"] == "done"
        assert a["detail"].startswith("1. who")

    def test_structured_detail_serialized_as_json(self):
        a = n({"event": "plan", "detail": {"sub_questions": ["who", "what"]}})
        assert '"sub_questions"' in a["detail"] and "who" in a["detail"]
        assert "{'" not in a["detail"]   # never a Python repr

    def test_to_agent_event_fields(self):
        ev = activity.enrich({"event": "tool", "tool": "calculator",
                              "args": "2+2", "ok": True, "duration_ms": 5})
        f = activity.to_agent_event_fields(ev)
        assert f["kind"] == "run" and f["status"] == "done"
        assert "Calculating" in f["title"]
        assert f["payload"]["tool"] == "calculator"


@pytest.fixture
async def patched_factory(engine, monkeypatch):
    """agent_log opens its own sessions via _factory(); point it at the
    test engine so ledger writes land in the temp DB."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.services import agent_log
    factory = async_sessionmaker(engine, class_=AsyncSession,
                                 expire_on_commit=False)
    monkeypatch.setattr(agent_log, "_factory", lambda: factory)
    return agent_log


class TestLedger:
    async def test_persist_and_replay(self, session, patched_factory):
        agent_log = patched_factory
        run_id = await agent_log.start_run(session, "test", "q?")
        await session.commit()   # release the write lock: the factory opens a
        # second connection and SQLite serializes writers
        ev = {"event": "tool", "tool": "web_search", "args": "x",
              "ok": True, "text": "🔧 web_search(x)"}
        await agent_log.persist_events(run_id, [
            {"event": "stage", "stage": "plan", "text": "Planning…"},
            ev,
        ])
        rows = await agent_log.list_events(session, run_id)
        assert len(rows) == 2
        assert rows[0]["kind"] == "plan" and rows[0]["group"] is True
        assert rows[1]["kind"] == "search" and rows[1]["id"] == f"{run_id}:2"

    async def test_tap_emit_enriches_and_persists(self, session,
                                                  patched_factory):
        agent_log = patched_factory
        run_id = await agent_log.start_run(session, "test", "q?")
        await session.commit()
        got = []
        tap = agent_log.tap_emit(run_id, got.append)
        ev = {"event": "note", "text": "hello"}
        tap(ev)
        assert ev["kind"] == "note" and got == [ev]
        # tap_emit persists fire-and-forget via create_task — drain it so the
        # stray task can't leak into the next test's fresh run/DB.
        import asyncio
        await asyncio.gather(
            *[t for t in asyncio.all_tasks() if t is not asyncio.current_task()],
            return_exceptions=True)
        rows = await agent_log.list_events(session, run_id)
        assert len(rows) == 1 and rows[0]["title"] == "hello"

    async def test_seq_no_collisions(self, session, patched_factory):
        import asyncio
        agent_log = patched_factory
        run_id = await agent_log.start_run(session, "test", "q?")
        await session.commit()
        await asyncio.gather(*[
            agent_log._persist_one(run_id, {"event": "note", "text": f"e{i}"})
            for i in range(6)])
        rows = await agent_log.list_events(session, run_id)
        assert [r["seq"] for r in rows] == [1, 2, 3, 4, 5, 6]
