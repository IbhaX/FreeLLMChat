"""Detached per-conversation chat jobs.

Keeps a chat-completion generation running after the client disconnects,
buffers every SSE frame, and persists the finished assistant reply into
the Conversation row — so a page refresh can re-attach live
(``live/stream``) or pick up the completed answer (``live`` status).

Registry state is in-memory and disposable (single-process scope); the
durable hand-off is the Conversation.messages append on completion.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_DONE = b"data: [DONE]\n\n"
_KEEPALIVE = b": ka\n\n"
_TTL_S = 3600.0          # finished jobs stay replayable for 1h
_MAX_REPLY = 200_000     # bound accumulated reply text


class ChatJob:
    """One detached generation for a conversation.

    ``frames`` is the append-only SSE buffer (replay source for late
    subscribers); ``reply`` is the best-effort extraction of the assistant
    text — completed by `replace_content`/`research_answer` meta frames
    and delta content alike.
    """

    __slots__ = (
        "conv_id", "frames", "reply", "done", "error", "model",
        "started_at", "done_at", "_cond", "_task", "linked",
    )

    def __init__(self, conv_id: str) -> None:
        self.conv_id = conv_id
        self.frames: list[bytes] = []
        self.reply = ""
        self.done = False
        self.error: str | None = None
        self.model = ""
        self.started_at = time.time()
        self.done_at = 0.0
        self._cond = asyncio.Condition()
        self._task: asyncio.Task[None] | None = None
        # Extra tasks torn down with the job (e.g. a detached research run
        # that owns the frame queue the pump consumes).
        self.linked: list[asyncio.Task[Any]] = []

    def _pump_dead(self) -> bool:
        """Pump task finished without marking done (e.g. cancelled before
        its first tick, so its finally never ran)."""
        return self._task is not None and self._task.done() and not self.done

    async def stream(self) -> AsyncIterator[bytes]:
        """Replay buffered frames then tail live until the job is done."""
        idx = 0
        while True:
            async with self._cond:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._cond.wait_for(
                            lambda idx=idx: idx < len(self.frames)
                            or self.done or self._pump_dead()),
                        timeout=20.0)
                batch = self.frames[idx:]
                idx = len(self.frames)
                done = self.done or self._pump_dead()
            for f in batch:
                yield f
            if done:
                yield _DONE
                return
            if not batch:
                yield _KEEPALIVE

    def cancel(self) -> None:
        for t in self.linked:
            if not t.done():
                t.cancel()
        if self._task is not None and not self._task.done():
            self._task.cancel()

    def _mark_finished(self, _task: Any = None) -> None:
        """Task completion hook — covers paths where _pump's finally can't
        run (task cancelled before its first tick)."""
        if self.done:
            return
        self.done = True
        self.done_at = self.done_at or time.time()
        with contextlib.suppress(RuntimeError):
            asyncio.get_running_loop().create_task(self._notify())

    async def _notify(self) -> None:
        async with self._cond:
            self._cond.notify_all()


_jobs: dict[str, ChatJob] = {}


def get(conv_id: str) -> ChatJob | None:
    return _jobs.get(str(conv_id))


def get_running(conv_id: str) -> ChatJob | None:
    job = _jobs.get(str(conv_id))
    return job if job is not None and not job.done and not job._pump_dead() else None


def start(conv_id: str, agen: AsyncIterator[bytes],
          session: AsyncSession | None = None) -> ChatJob:
    """Consume ``agen`` in a detached task; returns the existing running
    job when one is already live for this conversation (re-attach)."""
    _sweep()
    running = get_running(conv_id)
    if running is not None:
        return running
    job = ChatJob(str(conv_id))
    _jobs[job.conv_id] = job
    job._task = asyncio.create_task(_pump(job, agen, session))
    job._task.add_done_callback(job._mark_finished)
    return job


def _sweep() -> None:
    now = time.time()
    for key, job in list(_jobs.items()):
        if (job.done or job._pump_dead()) and now - (job.done_at or job.started_at) > _TTL_S:
            _jobs.pop(key, None)


def _accumulate(job: ChatJob, frame: bytes) -> None:
    """Track the assistant reply text out of SSE frames (never raises)."""
    try:
        for line in frame.decode("utf-8", "ignore").splitlines():
            if not line.startswith("data:"):
                continue
            data = json.loads(line[5:].strip())
            fl = data.get("nova")
            if isinstance(fl, dict):
                ev = fl.get("event")
                if ev == "replace_content":
                    job.reply = str(fl.get("content") or "")
                elif ev in ("research_answer", "agent_message"):
                    job.reply = str(
                        fl.get("answer") or fl.get("content") or job.reply)
                elif ev == "connected":
                    job.model = str(fl.get("model") or job.model)
                continue
            for ch in data.get("choices") or []:
                piece = (ch.get("delta") or {}).get("content") or ""
                if piece and len(job.reply) < _MAX_REPLY:
                    job.reply += piece
    except Exception:
        pass


async def _pump(job: ChatJob, agen: AsyncIterator[bytes],
                session: AsyncSession | None) -> None:
    try:
        async for frame in agen:
            _accumulate(job, frame)
            async with job._cond:
                job.frames.append(frame)
                job._cond.notify_all()
    except asyncio.CancelledError:
        job.error = "cancelled"
        raise
    except Exception as e:
        job.error = str(e)[:300]
        payload = {"nova": {"event": "error", "text": f"generation failed: {job.error}"}}
        frame = f"data: {json.dumps(payload)}\n\n".encode()
        async with job._cond:
            job.frames.append(frame)
            job._cond.notify_all()
    finally:
        # Persist BEFORE marking done: ``done`` means fully settled —
        # subscribers and status readers see the reply already stored.
        if job.reply:
            await _persist_reply(job.conv_id, job.reply, job.model)
        if session is not None:
            with contextlib.suppress(Exception):
                await session.close()
        job.done = True
        job.done_at = time.time()
        async with job._cond:
            job._cond.notify_all()


async def _persist_reply(conv_id: str, text: str, model: str) -> None:
    """Append the finished reply to the Conversation row (idempotent).

    Uses its own short-lived session; any failure is swallowed —
    persistence is observability, not the job itself.
    """
    from app.db import get_session_factory
    from app.models import Conversation
    try:
        async with get_session_factory()() as s:
            conv = (
                await s.execute(
                    select(Conversation).where(Conversation.client_id == conv_id)
                )
            ).scalar_one_or_none()
            if conv is None:
                conv = Conversation(client_id=conv_id, name="Chat")
                s.add(conv)
            msgs = list(conv.messages or [])
            last = msgs[-1] if msgs else {}
            prev = str(last.get("text") or last.get("content") or "")
            if last.get("role") == "assistant" and prev[:80] == text[:80]:
                return
            msgs.append({"role": "assistant", "text": text,
                         "t": int(time.time() * 1000), "model": model or ""})
            conv.messages = msgs
            conv.updated_at = datetime.now(UTC)
            await s.commit()
    except Exception:
        pass


def status(job: ChatJob | None) -> dict[str, Any]:
    if job is None:
        return {"running": False, "done": False}
    dead = job._pump_dead()
    out: dict[str, Any] = {
        "running": not job.done and not dead,
        "done": job.done or dead,
        "model": job.model,
        "error": job.error,
        "elapsed_s": round(
            (job.done_at or time.time()) - job.started_at, 1),
    }
    if job.done and job.reply:
        out["reply"] = job.reply
    return out
