"""Nova v2 research run lifecycle (spec §4, §14 — Phase 1 implementation).

State machine + durable execution for research runs. Phase 1 drives the
existing v1 pipeline inside the durable runner while persisting the v2
trail: stage records, monotonic events, checkpoints, and evidence-store
import of findings. The adaptive lead loop replaces the v1 engine in
Phase 2 behind ``research_engine=v2``.

States: created → researching → verifying → writing → editing → done.
``clarifying``/``planning``/``awaiting_approval``/``outlining``/
``building_artifacts`` are recorded as skipped in Phase 1 (their inputs
are not wired yet). Any state may go cancelled / failed / degraded.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Job,
    Report,
    ResearchRunV2,
    RunEvent,
    RunStage,
    SteerMessage,
    utcnow,
)
from app.services import evidence_store as es
from app.services.jobqueue import (
    JobContext,
    JobCancelled,
    enqueue,
    get_job_for_run,
    register,
    request_cancel,
)

log = logging.getLogger("nova.research_v2")

# ---------------------------------------------------------------- budgets

DEPTH_BUDGETS: dict[str, dict[str, Any]] = {
    "quick": {"max_rounds": 1, "max_parallel": 2, "max_tool_calls": 8,
              "max_wall_s": 60, "max_cost_usd": 0.05, "max_tokens": 200_000},
    "standard": {"max_rounds": 4, "max_parallel": 4, "max_tool_calls": 40,
                 "max_wall_s": 360, "max_cost_usd": 0.60, "max_tokens": 800_000},
    "deep": {"max_rounds": 10, "max_parallel": 6, "max_tool_calls": 150,
             "max_wall_s": 1500, "max_cost_usd": 3.00, "max_tokens": 1_500_000},
}

TERMINAL_STATES = {"done", "failed", "cancelled"}

_STAGES = ("clarifying", "planning", "awaiting_approval", "researching",
           "verifying", "outlining", "writing", "editing",
           "building_artifacts")

_gateway: Any = None


def configure(gateway: Any) -> None:
    """Inject the app gateway for the job handler (called at startup)."""
    global _gateway
    _gateway = gateway


def _require_gateway() -> Any:
    global _gateway
    if _gateway is None:
        from app.gateway import Gateway
        _gateway = Gateway()
    return _gateway


# ---------------------------------------------------------------- events

async def emit(
    session: AsyncSession,
    run_id: str,
    type: str,
    payload: dict[str, Any],
    *,
    actor: str = "system",
) -> RunEvent:
    """Append a monotonic event — basis for SSE replay (``id: <seq>``)."""
    seq = int((await session.execute(
        select(func.coalesce(func.max(RunEvent.seq), 0))
        .where(RunEvent.run_id == run_id)
    )).scalar_one()) + 1
    ev = RunEvent(run_id=run_id, seq=seq, type=type, actor=actor,
                  payload=payload)
    session.add(ev)
    await session.flush()
    return ev


async def list_events(
    session: AsyncSession, run_id: str, *, after_seq: int = 0, limit: int = 500
) -> list[RunEvent]:
    return list((await session.execute(
        select(RunEvent)
        .where(RunEvent.run_id == run_id, RunEvent.seq > after_seq)
        .order_by(RunEvent.seq).limit(min(limit, 2000))
    )).scalars().all())


# ---------------------------------------------------------------- stages

async def begin_stage(session: AsyncSession, run: ResearchRunV2,
                      stage: str) -> RunStage:
    run.state = stage
    await emit(session, run.id, "state", {"state": stage}, actor="system")
    rec = RunStage(run_id=run.id, stage=stage, status="running",
                   started_at=utcnow())
    session.add(rec)
    await session.flush()
    return rec


async def end_stage(session: AsyncSession, run: ResearchRunV2,
                    stage: str, *,
                    status: str = "done",
                    checkpoint: Optional[dict[str, Any]] = None) -> None:
    """Close the latest running RunStage for ``stage`` (skipped stages get a
    record with no start)."""
    rec = (await session.execute(
        select(RunStage)
        .where(RunStage.run_id == run.id, RunStage.stage == stage,
               RunStage.status == "running")
        .order_by(RunStage.attempt.desc()).limit(1)
    )).scalar_one_or_none()
    if rec is None:
        rec = RunStage(run_id=run.id, stage=stage)
        session.add(rec)
    rec.status = status
    rec.checkpoint = checkpoint
    rec.finished_at = utcnow()
    await session.flush()


async def skip_stage(session: AsyncSession, run: ResearchRunV2,
                     stage: str) -> None:
    await emit(session, run.id, "state",
               {"state": stage, "skipped": True}, actor="system")
    await end_stage(session, run, stage, status="skipped")


# ---------------------------------------------------------------- lifecycle

async def create_run(
    session: AsyncSession,
    *,
    question: str,
    depth: Optional[str] = None,
    options: Optional[dict[str, Any]] = None,
    budget_override: Optional[dict[str, Any]] = None,
    owner_id: Optional[int] = None,
    conversation_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> tuple[ResearchRunV2, bool]:
    """Create a run and enqueue its job. Returns (run, created?).

    ``idempotency_key`` makes creation idempotent for 24 h: a repeat key
    returns the original run with ``created=False``.
    """
    if idempotency_key:
        existing = (await session.execute(
            select(ResearchRunV2)
            .where(ResearchRunV2.idempotency_key == idempotency_key)
        )).scalar_one_or_none()
        if existing is not None:
            return existing, False

    opts = dict(options or {})
    resolved_depth = depth if depth in DEPTH_BUDGETS else "standard"
    budget = dict(DEPTH_BUDGETS.get(resolved_depth, DEPTH_BUDGETS["standard"]))
    if budget_override:
        for k, v in budget_override.items():
            if k in budget and isinstance(v, (int, float)) and v > 0:
                budget[k] = min(budget[k], v)
    run = ResearchRunV2(
        question=question.strip()[:8000], depth=resolved_depth,
        state="created", options=opts, budget=budget, spent={},
        owner_id=owner_id, conversation_id=conversation_id,
        idempotency_key=idempotency_key,
    )
    session.add(run)
    await session.flush()
    await emit(session, run.id, "state", {"state": "created"}, actor="system")
    await enqueue(session, "run_research",
                  {"run_id": run.id, "depth": resolved_depth},
                  run_id=run.id)
    return run, True


async def cancel_run(session: AsyncSession, run: ResearchRunV2) -> None:
    if run.state in TERMINAL_STATES:
        return
    await request_cancel(session, run_id=run.id)
    run.state = "cancelled"
    run.finished_at = utcnow()
    await emit(session, run.id, "state", {"state": "cancelled"})


async def steer(session: AsyncSession, run: ResearchRunV2,
                text: str) -> SteerMessage:
    msg = SteerMessage(run_id=run.id, text=text.strip()[:4000])
    session.add(msg)
    await session.flush()
    await emit(session, run.id, "steer", {"text": msg.text[:200]})
    return msg


# ---------------------------------------------------------------- handler

def _v1_event_to_run_event(ev: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """Map a v1 pipeline emit dict to (type, actor, payload).

    The emit dict is first normalized by ``activity.enrich`` so the stored
    payload carries the canonical activity fields (kind/status/title/…)
    alongside the legacy keys.
    """
    from app.services.activity import enrich
    ev = enrich(ev)
    kind = str(ev.get("event") or "note")
    actor = str(ev.get("actor") or ev.get("agent")
              or ("lead" if ev.get("stage") else "system"))
    payload = {k: v for k, v in ev.items() if k != "event"}
    type_map = {"stage": "stage", "note": "note",
                "research_done": "research.done",
                "research_answer": "report.ready"}
    return type_map.get(kind, kind), actor, payload


@register("run_research")
async def run_research_job(ctx: JobContext) -> dict[str, Any]:
    """Durable job: drive the v1 pipeline, persist the v2 trail.

    Phase 1 semantics: the whole v1 pipeline is one checkpointed stage —
    a reclaimed job after a crash skips a stage that already finished.
    """
    from app.db import get_session_factory
    from app.services.research import run_research

    run_id = str(ctx.job.payload.get("run_id") or ctx.job.run_id or "")
    factory = ctx.session_factory
    async with factory() as session:
        run = await session.get(ResearchRunV2, run_id)
        if run is None:
            raise ValueError(f"run {run_id} not found")
        if run.state in TERMINAL_STATES:
            return {"skipped": f"already {run.state}"}
        if await ctx.cancel_requested():
            raise JobCancelled()

        gateway = _require_gateway()

        for stage in ("clarifying", "planning", "awaiting_approval"):
            await skip_stage(session, run, stage)

        await begin_stage(session, run, "researching")
        await session.commit()

    # -- research (checkpointed: a reclaimed job does not re-pay) -----------
    state_box: dict[str, Any] = {}
    done_cp = ctx.checkpoint("researching")
    if done_cp and done_cp.get("answer"):
        answer = done_cp["answer"]
        async with factory() as session:
            await emit(session, run_id, "note",
                       {"text": "research stage restored from checkpoint"})
            await session.commit()
    else:
        # Serialize ledger writes through one queue+flusher so seq stays
        # monotonic and events are never interleaved by create_task races.
        ev_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def flush_loop() -> None:
            while True:
                ev = await ev_queue.get()
                etype, actor, payload = _v1_event_to_run_event(ev)
                try:
                    async with factory() as esession:
                        await emit(esession, run_id, etype, payload,
                                   actor=actor)
                        await esession.commit()
                except Exception as e:
                    log.debug("event write failed: %s", e)
                finally:
                    ev_queue.task_done()

        flusher = asyncio.ensure_future(flush_loop())
        try:
            messages = [{"role": "user", "content": run.question}]
            async with factory() as rsession:
                state = await run_research(
                    gateway, rsession, messages,
                    model=(run.options or {}).get("model", "auto"),
                    depth=run.depth, conversation_id=run.conversation_id,
                    on_event=ev_queue.put_nowait)
            state_box["state"] = state
            answer = state.answer
        finally:
            await ev_queue.join()
            flusher.cancel()
        await ctx.save_checkpoint("researching", {"answer": answer})

    if await ctx.cancel_requested():
        raise JobCancelled()

    # -- verify + persist findings into the evidence store -------------------
    async with factory() as session:
        run = await session.get(ResearchRunV2, run_id)
        assert run is not None
        await end_stage(session, run, "researching", status="done")
        await begin_stage(session, run, "verifying")
        imported = {"sources": 0, "claims": 0}
        state = state_box.get("state")
        if state is not None:
            try:
                imported = await es.import_v1_state(session, run_id, state)
                await emit(session, run_id, "evidence.imported", imported)
            except Exception as e:
                run.degraded = True
                await emit(session, run_id, "degraded",
                           {"reason": f"evidence import failed: {e}"[:200]})
        await end_stage(session, run, "verifying",
                        checkpoint=imported)
        await skip_stage(session, run, "outlining")
        await begin_stage(session, run, "writing")

        report = Report(
            run_id=run_id, version=1, outline={},
            markdown=answer or "",
            components=None,
            quality={
                "degraded": bool(run.degraded),
                "depth": run.depth,
                **(getattr(state, "meta", None) or {}),
            },
        )
        session.add(report)
        await session.flush()
        run.result_ref = report.id
        await end_stage(session, run, "writing",
                        checkpoint={"report_id": report.id})
        await end_stage(session, run, "editing", status="skipped")
        await end_stage(session, run, "building_artifacts", status="skipped")

        run.state = "degraded" if run.degraded else "done"
        run.finished_at = utcnow()
        await emit(session, run_id, "report.ready", {"report_id": report.id})
        await emit(session, run_id, "done",
                   {"report_id": report.id, "degraded": run.degraded,
                    "quality": report.quality})
        await session.commit()
    return {"report_id": run.result_ref, "degraded": run.degraded}


async def resume_run(session: AsyncSession, run: ResearchRunV2) -> Job:
    """Re-enqueue a failed/cancelled run; completed stages are skipped via
    the job's persisted checkpoints."""
    job = await get_job_for_run(session, run.id)
    if job is not None and job.status in ("queued", "running"):
        return job
    run.state = "researching"
    run.finished_at = None
    await emit(session, run.id, "state", {"state": "researching"})
    prior = (job.payload or {}).get("_checkpoints") if job else None
    return await enqueue(
        session, "run_research",
        {"run_id": run.id, "depth": run.depth,
         "_checkpoints": prior or {}},
        run_id=run.id)
