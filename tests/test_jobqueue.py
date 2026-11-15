"""Durable job queue: claim, execute, retry, cancel, lease reclaim."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Job, utcnow
from app.services import jobqueue as jq


@pytest.fixture
def factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession,
                              expire_on_commit=False)


async def _mk_job(session, kind="t.ok", payload=None, **kw):
    return await jq.enqueue(session, kind, payload or {}, **kw)


async def test_job_runs_to_done(factory):
    seen = {}

    @jq.register("t.ok")
    async def handler(ctx):
        return {"echo": ctx.job.payload.get("x")}

    async with factory() as s:
        job = await _mk_job(s, payload={"x": 42})
        await s.commit()
    runner = jq.JobRunner(factory, poll_s=0.05)
    assert await runner.run_once()
    async with factory() as s:
        done = await s.get(Job, job.id)
        assert done.status == "done" and done.result == {"echo": 42}
        assert done.attempts == 1


async def test_handler_failure_retries_then_fails(factory):
    calls = {"n": 0}

    @jq.register("t.fail")
    async def handler(ctx):
        calls["n"] += 1
        raise RuntimeError("boom")

    async with factory() as s:
        job = await _mk_job(s, "t.fail", max_attempts=2)
        await s.commit()
    runner = jq.JobRunner(factory, poll_s=0.05)
    assert await runner.run_once()          # attempt 1 -> requeued
    assert await runner.run_once()          # attempt 2 -> failed
    async with factory() as s:
        j = await s.get(Job, job.id)
        assert j.status == "failed" and j.attempts == 2 and "boom" in j.error


async def test_cancel_flag_cooperative(factory):
    @jq.register("t.cancel")
    async def handler(ctx):
        async with ctx.session_factory() as s:
            await jq.request_cancel(s, job_id=ctx.job.id)
            await s.commit()
        if await ctx.cancel_requested():
            raise jq.JobCancelled()
        return {}

    async with factory() as s:
        job = await _mk_job(s, "t.cancel")
        await s.commit()
    runner = jq.JobRunner(factory, poll_s=0.05)
    await runner.run_once()
    async with factory() as s:
        assert (await s.get(Job, job.id)).status == "cancelled"


async def test_expired_lease_reclaimed(factory):
    ran = {"n": 0}

    @jq.register("t.reclaim")
    async def handler(ctx):
        ran["n"] += 1
        return {}

    async with factory() as s:
        job = await _mk_job(s, "t.reclaim")
        # Simulate a crashed worker: status running, lease in the past.
        job.status = "running"
        job.attempts = 1
        job.lease_owner = "dead-worker"
        job.lease_until = utcnow() - timedelta(seconds=5)
        await s.commit()
    runner = jq.JobRunner(factory, poll_s=0.05)
    assert await runner.run_once()
    async with factory() as s:
        j = await s.get(Job, job.id)
        assert j.status == "done" and ran["n"] == 1
        # A reclaim does not spend an extra attempt — the dead one already did.
        assert j.attempts == 1


async def test_checkpoints_survive_reclaim(factory):
    @jq.register("t.checkpoint")
    async def handler(ctx):
        if ctx.checkpoint("stage1") is None:
            await ctx.save_checkpoint("stage1", {"done": True})
            raise RuntimeError("crash after stage1")
        return {"resumed": True}

    async with factory() as s:
        job = await _mk_job(s, "t.checkpoint", max_attempts=3)
        await s.commit()
    runner = jq.JobRunner(factory, poll_s=0.05)
    await runner.run_once()   # crashes, checkpoint saved
    await runner.run_once()   # resumes, sees checkpoint, finishes
    async with factory() as s:
        j = await s.get(Job, job.id)
        assert j.status == "done" and j.result == {"resumed": True}


async def test_run_after_defers(factory):
    @jq.register("t.later")
    async def handler(ctx):
        return {}

    async with factory() as s:
        job = await _mk_job(s, "t.later",
                            run_after=utcnow() + timedelta(hours=1))
        await s.commit()
    runner = jq.JobRunner(factory, poll_s=0.05)
    assert not await runner.run_once()
    async with factory() as s:
        assert (await s.get(Job, job.id)).status == "queued"
