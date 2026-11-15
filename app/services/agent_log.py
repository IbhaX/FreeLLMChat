"""Durable agentic-flow ledger: every agent run and every recorded step.

Write-path is deliberately self-contained — it opens its own session per
batch of events and commits immediately, so the ledger survives request
timeouts, SSE disconnects and process boundaries. All functions swallow
errors: observability must never break the work it records.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import desc, select

from app.models import AgentEvent, AgentRun


def _factory() -> Any:
    from app.db import get_session_factory
    return get_session_factory()


async def start_run(session: Any, kind: str, question: str, *,
                    subject: str = "anon", model: str = "",
                    meta: Optional[dict] = None) -> Optional[int]:
    """Begin a recorded run. Returns the run id, or None on failure."""
    try:
        run = AgentRun(kind=kind, question=(question or "")[:8000],
                       subject=subject, model=model or "", meta=meta or {})
        session.add(run)
        await session.flush()
        return run.id
    except Exception:
        return None


async def record(session: Any, run_id: int | None, seq: int, *,
                 stage: str = "", actor: str = "", event: str = "",
                 detail: str = "", status: str = "ok",
                 duration_ms: float = 0.0) -> None:
    if not run_id:
        return
    try:
        session.add(AgentEvent(
            run_id=run_id, seq=seq, stage=stage[:32], actor=actor[:64],
            event=event[:32], detail=(detail or "")[:8000],
            status=status, duration_ms=float(duration_ms or 0.0)))
    except Exception:
        pass


async def finish_run(session: Any, run_id: int | None, status: str,
                     meta: Optional[dict] = None,
                     duration_ms: int = 0) -> None:
    if not run_id:
        return
    try:
        run = await session.get(AgentRun, run_id)
        if run:
            run.status = status
            run.finished_at = datetime.now(timezone.utc)
            run.duration_ms = duration_ms
            if meta:
                run.meta = {**(run.meta or {}), **meta}
    except Exception:
        pass


async def persist_events(run_id: int | None, events: list[dict[str, Any]]) -> None:
    """Bulk-persist a completed trace (orchestrate path)."""
    if not run_id or not events:
        return
    try:
        factory = _factory()
        async with factory() as s:
            for i, e in enumerate(events, 1):
                await record(s, run_id, i,
                             stage=str(e.get("stage") or ""),
                             actor=str(e.get("actor") or ""),
                             event=str(e.get("event") or ""),
                             detail=str(e.get("detail") or e.get("text") or ""),
                             status=str(e.get("status") or "ok"),
                             duration_ms=float(e.get("duration_ms") or 0))
            await s.commit()
    except Exception:
        pass


def tap_emit(run_id: int | None, inner: Any) -> Any:
    """Wrap an emit callable so every event is also persisted."""
    def _emit(ev: dict) -> None:
        try:
            inner(ev)
        finally:
            asyncio_safe_persist(run_id, ev)
    return _emit


def asyncio_safe_persist(run_id: int | None, ev: dict) -> None:
    """Persist a single event fire-and-forget style (no await needed)."""
    if not run_id:
        return
    try:
        import asyncio
        loop = asyncio.get_running_loop()
        loop.create_task(_persist_one(run_id, ev))
    except Exception:
        pass


async def _persist_one(run_id: int, ev: dict) -> None:
    try:
        factory = _factory()
        async with factory() as s:
            last = (await s.execute(
                select(AgentEvent.seq).where(AgentEvent.run_id == run_id)
                .order_by(desc(AgentEvent.seq)).limit(1))).scalar()
            await record(s, run_id, int(last or 0) + 1,
                         stage=str(ev.get("stage") or ""),
                         actor=str(ev.get("actor") or ""),
                         event=str(ev.get("event") or ""),
                         detail=str(ev.get("text") or ""),
                         status=str(ev.get("status") or "ok"))
            await s.commit()
    except Exception:
        pass


async def list_runs(session: Any, *, limit: int = 30,
                    kind: str | None = None,
                    subject: str | None = None) -> list[AgentRun]:
    q = select(AgentRun).order_by(desc(AgentRun.started_at))
    if kind:
        q = q.where(AgentRun.kind == kind)
    if subject:
        q = q.where(AgentRun.subject == subject)
    return list((await session.execute(q.limit(min(limit, 100)))).scalars().all())
