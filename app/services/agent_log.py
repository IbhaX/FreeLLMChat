"""Durable agentic-flow ledger: every agent run and every recorded step.

Write-path is deliberately self-contained — it opens its own session per
batch of events and commits immediately, so the ledger survives request
timeouts, SSE disconnects and process boundaries. All functions swallow
errors: observability must never break the work it records.

Activity-log v2: every event passing through ``tap_emit`` is normalized by
``services.activity.enrich`` — the emit dict gains kind/status/title/
parent/level fields (so SSE consumers get structure for free) and the
ledger row stores them plus the raw payload for replay.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, select

from app.models import AgentEvent, AgentRun
from app.services import activity

# Per-run sequence locks — fire-and-forget persist tasks can otherwise race
# on the max(seq) read and collide. Serialized per run, not globally.
_seq_locks: dict[int, asyncio.Lock] = {}


def _run_lock(run_id: int) -> asyncio.Lock:
    lock = _seq_locks.get(run_id)
    if lock is None:
        lock = _seq_locks[run_id] = asyncio.Lock()
    return lock


def _factory() -> Any:
    from app.db import get_session_factory
    return get_session_factory()


async def start_run(session: Any, kind: str, question: str, *,
                    subject: str = "anon", model: str = "",
                    meta: dict | None = None) -> int | None:
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
                 duration_ms: float = 0.0,
                 ev: dict[str, Any] | None = None) -> None:
    """Insert one ledger row. ``ev`` carries the full normalized event."""
    if not run_id:
        return
    try:
        fields = {
            "stage": stage[:32], "actor": actor[:64], "event": event[:32],
            "detail": (detail or "")[:8000], "status": status,
            "duration_ms": float(duration_ms or 0.0),
        }
        if ev is not None:
            fields.update(activity.to_agent_event_fields(ev))
        session.add(AgentEvent(run_id=run_id, seq=seq, **fields))
    except Exception:
        pass


async def finish_run(session: Any, run_id: int | None, status: str,
                     meta: dict | None = None,
                     duration_ms: int = 0) -> None:
    if not run_id:
        return
    try:
        run = await session.get(AgentRun, run_id)
        if run:
            run.status = status
            run.finished_at = datetime.now(UTC)
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
                await record(s, run_id, i, ev=e)
            await s.commit()
    except Exception:
        pass


def tap_emit(run_id: int | None, inner: Any) -> Any:
    """Wrap an emit callable: normalize the event, forward it, persist it."""
    def _emit(ev: dict) -> None:
        try:
            activity.enrich(ev)
            inner(ev)
        finally:
            asyncio_safe_persist(run_id, ev)
    return _emit


def asyncio_safe_persist(run_id: int | None, ev: dict) -> None:
    """Persist a single event fire-and-forget style (no await needed)."""
    if not run_id:
        return
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_persist_one(run_id, dict(ev)))
    except Exception:
        pass


async def _persist_one(run_id: int, ev: dict) -> None:
    # SQLite serializes writers: a request session holding an open write
    # transaction makes this insert fail with "database is locked". Retry a
    # few times; the write is still best-effort and eventually drops quietly.
    for _attempt in range(4):
        try:
            async with _run_lock(run_id):
                factory = _factory()
                async with factory() as s:
                    last = (await s.execute(
                        select(AgentEvent.seq).where(AgentEvent.run_id == run_id)
                        .order_by(desc(AgentEvent.seq)).limit(1))).scalar()
                    await record(s, run_id, int(last or 0) + 1, ev=ev)
                    await s.commit()
            return
        except Exception as e:
            if "locked" not in str(e).lower() or _attempt == 3:
                return
            await asyncio.sleep(0.05 * (_attempt + 1))


async def list_runs(session: Any, *, limit: int = 30,
                    kind: str | None = None,
                    subject: str | None = None) -> list[AgentRun]:
    q = select(AgentRun).order_by(desc(AgentRun.started_at))
    if kind:
        q = q.where(AgentRun.kind == kind)
    if subject:
        q = q.where(AgentRun.subject == subject)
    return list((await session.execute(q.limit(min(limit, 100)))).scalars().all())


async def list_events(session: Any, run_id: int,
                      *, after_seq: int = 0, limit: int = 500) -> list[dict[str, Any]]:
    """Normalized event dicts for replay (SSE reconnect / run detail)."""
    q = (select(AgentEvent)
         .where(AgentEvent.run_id == run_id, AgentEvent.seq > after_seq)
         .order_by(AgentEvent.seq).limit(min(limit, 2000)))
    rows = (await session.execute(q)).scalars().all()
    out: list[dict[str, Any]] = []
    for r in rows:
        ev = dict(r.payload or {})
        ev.setdefault("event", r.event)
        ev.setdefault("text", r.detail)
        ev.update({
            "id": f"{run_id}:{r.seq}", "seq": r.seq,
            "kind": r.kind, "status": r.status, "title": r.title or r.detail[:160],
            "parent": r.parent or None, "level": r.level,
            "duration_ms": r.duration_ms or None,
            "actor": r.actor, "ts": r.ts.isoformat() if r.ts else None,
        })
        out.append(ev)
    return out
