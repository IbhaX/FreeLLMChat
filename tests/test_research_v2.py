"""v2 run lifecycle: creation, events, stages, the durable job handler."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Job, Report, ResearchRunV2, RunEvent
from app.services import jobqueue as jq
from app.services import research_v2 as rv2


@pytest.fixture
def factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession,
                              expire_on_commit=False)


async def test_create_run_enqueues_job(session):
    run, created = await rv2.create_run(session, question="What is X?")
    assert created and run.state == "created"
    assert run.budget["max_tool_calls"] == 40  # standard defaults
    events = await rv2.list_events(session, run.id)
    assert [e.type for e in events] == ["state"]
    job = await jq.get_job_for_run(session, run.id)
    assert job is not None and job.kind == "run_research"


async def test_idempotency_key(session):
    r1, c1 = await rv2.create_run(
        session, question="q", idempotency_key="k-1")
    r2, c2 = await rv2.create_run(
        session, question="q", idempotency_key="k-1")
    assert c1 and not c2 and r1.id == r2.id


async def test_budget_override_only_lowers(session):
    run, _ = await rv2.create_run(
        session, question="q", depth="deep",
        budget_override={"max_cost_usd": 1.5, "max_tool_calls": 99999})
    assert run.budget["max_cost_usd"] == 1.5       # lowered
    assert run.budget["max_tool_calls"] == 150   # cannot raise the cap


async def test_emit_monotonic_seq(session):
    run, _ = await rv2.create_run(session, question="q")
    await rv2.emit(session, run.id, "note", {"text": "a"})
    await rv2.emit(session, run.id, "note", {"text": "b"})
    seqs = [e.seq for e in await rv2.list_events(session, run.id)]
    assert seqs == [1, 2, 3]


async def test_cancel_marks_terminal(session):
    run, _ = await rv2.create_run(session, question="q")
    await rv2.cancel_run(session, run)
    assert run.state == "cancelled"
    job = await jq.get_job_for_run(session, run.id)
    assert job.cancel_requested


async def test_steer_persisted(session):
    run, _ = await rv2.create_run(session, question="q")
    msg = await rv2.steer(session, run, "focus on primary sources")
    assert msg.applied_at is None
    types = [e.type for e in await rv2.list_events(session, run.id)]
    assert "steer" in types


def _fake_state() -> SimpleNamespace:
    return SimpleNamespace(
        answer="# Report\n\nSomething happened [1].",
        meta={"depth": "standard", "findings": 1},
        sources={"s1": SimpleNamespace(id="s1", url="https://example.com/x",
                                       title="Doc", type="news")},
        findings=[SimpleNamespace(claim="Something happened.",
                                  confidence="high", verdict="supported",
                                  verdict_note="", source_id="s1")],
    )


async def test_run_research_job_end_to_end(factory, session, monkeypatch):
    """The durable handler drives the pipeline, writes stages/events, builds
    a report, and lands in 'done'."""

    async def fake_run_research(gw, sess, messages, *, model, depth,
                                conversation_id, on_event):
        on_event({"event": "stage", "stage": "plan", "text": "planning"})
        on_event({"event": "research_done", "text": "done", "findings": 1})
        return _fake_state()

    monkeypatch.setattr("app.services.research.run_research",
                        fake_run_research)
    rv2.configure(object())

    async with factory() as s:
        run, _ = await rv2.create_run(s, question="q")
        await s.commit()
        run_id = run.id

    runner = jq.JobRunner(factory, poll_s=0.05)
    assert await runner.run_once()

    async with factory() as s:
        run = await s.get(ResearchRunV2, run_id)
        assert run.state == "done" and run.result_ref
        report = await s.get(Report, run.result_ref)
        assert "# Report" in report.markdown
        events = (await s.execute(
            select(RunEvent).where(RunEvent.run_id == run_id)
            .order_by(RunEvent.seq))).scalars().all()
        types = [e.type for e in events]
        assert "done" in types and "report.ready" in types
        assert types[0] == "state"
        # v1 stage events bridged into the ledger
        assert "stage" in types
        job = await s.get(Job, (await jq.get_job_for_run(s, run_id)).id)
        assert job.status == "done"


async def test_run_research_job_skips_finished(session, monkeypatch):
    """A terminal run is not re-executed."""
    called = {"n": 0}

    async def fake(gw, sess, messages, **kw):
        called["n"] += 1
        return _fake_state()

    monkeypatch.setattr("app.services.research.run_research", fake)
    run, _ = await rv2.create_run(session, question="q")
    await rv2.cancel_run(session, run)
    await session.commit()

    factory = async_sessionmaker(session.bind, class_=AsyncSession,
                                 expire_on_commit=False)
    runner = jq.JobRunner(factory, poll_s=0.05)
    await runner.run_once()
    assert called["n"] == 0
