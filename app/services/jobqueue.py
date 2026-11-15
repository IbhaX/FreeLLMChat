"""SQLite-backed durable job queue (spec §14, adapted: no Redis).

A job row is the unit of work. A worker claims a queued job by setting
``status='running'`` with a lease; the lease is renewed while the handler
runs. If the worker dies, another worker reclaims the job once the lease
expires and re-runs it — handlers are responsible for checking
``ctx.checkpoint()`` so completed stages are not repeated (and not paid
for twice).

Cancellation is cooperative: ``request_cancel`` sets a flag the handler
checks via ``ctx.cancel_requested()`` between steps.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.models import Job, utcnow

log = logging.getLogger("freellm.jobs")

Handler = Callable[["JobContext"], Awaitable[dict[str, Any] | None]]

_HANDLERS: dict[str, Handler] = {}


def register(kind: str) -> Callable[[Handler], Handler]:
    """Decorator: register a coroutine handler for a job kind."""
    def deco(fn: Handler) -> Handler:
        _HANDLERS[kind] = fn
        return fn
    return deco


# ---------------------------------------------------------------- enqueue

async def enqueue(
    session: AsyncSession,
    kind: str,
    payload: Optional[dict[str, Any]] = None,
    *,
    run_id: Optional[str] = None,
    priority: int = 5,
    run_after: Optional[datetime] = None,
    max_attempts: int = 3,
) -> Job:
    job = Job(
        kind=kind, run_id=run_id, payload=payload or {}, priority=priority,
        run_after=run_after, max_attempts=max_attempts,
    )
    session.add(job)
    await session.flush()
    return job


async def request_cancel(session: AsyncSession, *, job_id: Optional[str] = None,
                         run_id: Optional[str] = None) -> int:
    """Flag cancel on matching non-terminal jobs. Returns rows updated."""
    stmt = update(Job).where(Job.status.in_(["queued", "running"]))
    if job_id:
        stmt = stmt.where(Job.id == job_id)
    elif run_id:
        stmt = stmt.where(Job.run_id == run_id)
    else:
        return 0
    res = await session.execute(stmt.values(cancel_requested=True))
    await session.flush()
    return int(res.rowcount or 0)


# ---------------------------------------------------------------- context

@dataclass
class JobContext:
    """What a handler sees: the job, a session factory, and control helpers.

    ``checkpoints`` persists through ``Job.payload["_checkpoints"]`` so a
    reclaimed run can skip completed work.
    """

    job: Job
    session_factory: async_sessionmaker[AsyncSession]
    checkpoints: dict[str, Any] = field(default_factory=dict)
    _cancelled: bool = False

    def checkpoint(self, stage: str) -> Optional[Any]:
        return self.checkpoints.get(stage)

    async def save_checkpoint(self, stage: str, data: Any) -> None:
        self.checkpoints[stage] = data
        async with self.session_factory() as s:
            job = await s.get(Job, self.job.id)
            if job is not None:
                payload = dict(job.payload or {})
                payload["_checkpoints"] = self.checkpoints
                job.payload = payload
                await s.commit()

    async def cancel_requested(self) -> bool:
        if self._cancelled:
            return True
        async with self.session_factory() as s:
            job = await s.get(Job, self.job.id)
            self._cancelled = bool(job and job.cancel_requested)
        return self._cancelled


class JobCancelled(Exception):
    """Raised by handlers to abort cooperatively on a cancel request."""


# ---------------------------------------------------------------- worker

def _lease_expired(job: Job, now: datetime) -> bool:
    lease = job.lease_until
    if lease is None:
        return True
    if lease.tzinfo is None:
        lease = lease.replace(tzinfo=timezone.utc)
    return lease <= now


class JobRunner:
    """In-process worker loop. One instance per app process; jobs are claimed
    atomically so multiple processes are also safe."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        worker_id: Optional[str] = None,
        poll_s: Optional[float] = None,
        lease_s: Optional[int] = None,
    ) -> None:
        s = get_settings()
        self.factory = session_factory
        self.worker_id = worker_id or f"{socket.gethostname()}-{uuid.uuid4().hex[:6]}"
        self.poll_s = poll_s if poll_s is not None else s.research_worker_poll_s
        self.lease_s = lease_s if lease_s is not None else s.research_job_lease_s
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    # -- claiming -----------------------------------------------------------

    async def _claim_next(self) -> Optional[Job]:
        """Atomically claim one runnable job (queued, or running with an
        expired lease) and extend the lease."""
        now = utcnow()
        lease_until = now + timedelta(seconds=self.lease_s)
        async with self.factory() as session:
            # Sweep: running jobs past their lease that exhausted attempts
            # are unclaimable — mark them failed rather than stuck.
            stale = (await session.execute(
                select(Job)
                .where(Job.status == "running",
                       Job.attempts >= Job.max_attempts)
            )).scalars().all()
            for job in stale:
                if _lease_expired(job, now):
                    job.status = "failed"
                    job.error = "attempts exhausted (lease expired)"
                    job.finished_at = now
                    await session.commit()
            candidates = (await session.execute(
                select(Job)
                .where(Job.status.in_(["queued", "running"]))
                .where(Job.attempts < Job.max_attempts)
                .order_by(Job.priority, Job.created_at)
                .limit(10)
            )).scalars().all()
            for job in candidates:
                if job.status == "running" and not _lease_expired(job, now):
                    continue
                if job.status == "queued" and job.run_after is not None:
                    ra = job.run_after
                    if ra.tzinfo is None:
                        ra = ra.replace(tzinfo=timezone.utc)
                    if ra > now:
                        continue
                # CAS: only one worker flips the row.
                res = await session.execute(
                    update(Job)
                    .where(Job.id == job.id)
                    .where(Job.status.in_(["queued", "running"]))
                    .values(status="running", lease_owner=self.worker_id,
                            lease_until=lease_until,
                            attempts=job.attempts + (0 if job.status == "running" else 1),
                            error=None)
                )
                await session.commit()
                if res.rowcount:
                    await session.refresh(job)
                    return job
            return None

    async def _renew(self, job_id: str) -> None:
        async with self.factory() as session:
            await session.execute(
                update(Job).where(Job.id == job_id)
                .where(Job.lease_owner == self.worker_id)
                .values(lease_until=utcnow() + timedelta(seconds=self.lease_s))
            )
            await session.commit()

    async def _finish(self, job_id: str, status: str,
                      result: Optional[dict[str, Any]] = None,
                      error: Optional[str] = None) -> None:
        async with self.factory() as session:
            await session.execute(
                update(Job).where(Job.id == job_id).values(
                    status=status, result=result, error=error,
                    lease_owner=None, lease_until=None, finished_at=utcnow())
            )
            await session.commit()

    # -- loop -----------------------------------------------------------------

    async def run_once(self) -> bool:
        """Claim and execute one job. Returns True when work was done."""
        job = await self._claim_next()
        if job is None:
            return False
        handler = _HANDLERS.get(job.kind)
        if handler is None:
            await self._finish(job.id, "failed",
                               error=f"no handler for kind '{job.kind}'")
            return True

        renew_task = asyncio.ensure_future(self._heartbeat(job.id))
        try:
            ctx = JobContext(
                job=job, session_factory=self.factory,
                checkpoints=dict((job.payload or {}).get("_checkpoints") or {}),
            )
            result = await handler(ctx)
            await self._finish(job.id, "done", result=result)
        except JobCancelled:
            await self._finish(job.id, "cancelled", error="cancelled by user")
        except asyncio.CancelledError:
            # Process shutting down — release the lease so another worker
            # can reclaim promptly instead of waiting for expiry.
            await self._finish(job.id, "queued", error="worker shutdown")
            raise
        except Exception as e:
            log.exception("job %s (%s) failed", job.id, job.kind)
            status = "queued" if job.attempts < job.max_attempts else "failed"
            await self._finish(job.id, status, error=str(e)[:500])
        finally:
            renew_task.cancel()
        return True

    async def _heartbeat(self, job_id: str) -> None:
        while True:
            await asyncio.sleep(max(1, self.lease_s // 3))
            try:
                await self._renew(job_id)
            except Exception as e:
                log.debug("lease renew failed for %s: %s", job_id, e)

    async def run_forever(self) -> None:
        log.info("job runner %s started (poll %.1fs, lease %ds)",
                 self.worker_id, self.poll_s, self.lease_s)
        while not self._stop.is_set():
            try:
                did_work = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("job runner poll error: %s", e)
                did_work = False
            if not did_work:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.poll_s)
                except asyncio.TimeoutError:
                    pass


async def get_job_for_run(
    session: AsyncSession, run_id: str
) -> Optional[Job]:
    return (await session.execute(
        select(Job).where(Job.run_id == run_id)
        .order_by(Job.created_at.desc()).limit(1)
    )).scalar_one_or_none()
