"""Deep-research endpoints: run the pipeline, stream stage events, and
list/get saved research runs."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (entitled, optional_user, require_user,
                          subject_for)
from app.db import get_session
from app.models import ResearchRun
from app.services import agent_log
from app.services.research import run_research

router = APIRouter(
    prefix="/v1",
    dependencies=[Depends(require_user)])

Depth = Literal["quick", "standard", "deep"]


class ResearchRequest(BaseModel):
    message: str
    model: str = "auto"
    depth: Depth | None = None
    history: list[dict[str, Any]] = Field(default_factory=list)
    conversation_id: str | None = None
    save: bool = True


def _gateway(request: Request):
    return request.app.state.gateway


def _messages(body: ResearchRequest) -> list[dict[str, Any]]:
    # History gives the planner/follow-up classifier context; the pipeline
    # reads the last user message as the question.
    msgs = [dict(m) for m in (body.history or [])[-10:]
            if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
    msgs.append({"role": "user", "content": body.message})
    return msgs


def _meta(ev: dict) -> bytes:
    return f"data: {json.dumps({'nova': ev})}\n\n".encode()


def _state_payload(state) -> tuple[list, list, list]:
    findings = [{
        "claim": f.claim, "evidence": f.evidence, "source_id": f.source_id,
        "confidence": f.confidence, "verdict": f.verdict,
        "sub_question": f.sub_question,
        "corroborated_by": list(getattr(f, "corroborated_by", [])),
        "evidence_class": getattr(f, "evidence_class", "") or "",
    } for f in state.findings]
    sources = [{
        "id": s.id, "title": s.title, "url": s.url, "publisher": s.publisher,
        "date": s.date, "type": s.type, "quality": s.quality, "score": s.score,
        "authors": getattr(s, "authors", "") or "",
        "doi": getattr(s, "doi", "") or "",
        "access": getattr(s, "access", "") or "",
        "fetched": getattr(s, "fetched", "") or "",
    } for s in sorted(state.sources.values(), key=lambda x: -x.score)]
    return state.plan or [], findings, sources


async def _persist(
    session: AsyncSession, body: ResearchRequest, state, answer: str,
) -> int | None:
    if not body.save or state is None:
        return None
    plan, findings, sources = _state_payload(state)
    meta = {k: v for k, v in (getattr(state, "meta", None) or {}).items()}
    run = ResearchRun(
        question=body.message[:8000],
        conversation_id=(str(body.conversation_id)[:64] if body.conversation_id else None),
        depth=meta.get("depth", body.depth or "standard"),
        answer=answer[:50000], plan=plan, findings=findings, sources=sources, meta=meta,
    )
    session.add(run)
    try:
        await session.commit()
    except Exception:
        # Persistence is best-effort: never fail a finished research run.
        await session.rollback()
        return None
    return run.id


@router.post("/research")
async def research(
    body: ResearchRequest, request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Non-streaming research run. Returns the answer plus run metadata."""
    gw = _gateway(request)
    _user = await optional_user(request)
    if body.depth == "deep" and not entitled(_user, "deep_research"):
        from app.services import entitlements
        raise HTTPException(403, entitlements.upgrade_error("deep_research"))
    if _user is not None:
        from app.services import usage
        ok, reason = await usage.check_quota(
            session, subject_for(_user), "research", _user.plan)
        if not ok:
            raise HTTPException(429, reason)
        await usage.record(session, subject_for(_user), "research",
                           model=body.model or "")
        # Release the write lock before run_research's long network work.
        try:
            await session.commit()
        except Exception:
            await session.rollback()
    from app.services import artifacts as _artifacts

    _artifacts.set_context(
        source="research", owner_id=_user.id if _user else None)
    events: list[dict] = []

    def emit(ev: dict) -> None:
        events.append(ev)

    state = None
    try:
        state = await run_research(
            gw, session, _messages(body), model=body.model, depth=body.depth,
            conversation_id=body.conversation_id, on_event=emit,
        )
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"error": {"message": str(e)[:300], "type": "research_failed"}},
        )
    answer = state.answer if state is not None else ""
    done = next((e for e in reversed(events) if e.get("event") == "research_done"), {})
    meta = {k: v for k, v in done.items() if k not in ("event", "text")}
    rid = await _persist(session, body, state, answer)
    _user = await optional_user(request)
    from app.services import knowledge_capture

    knowledge_capture.schedule(
        _user.id if _user else None, "research", body.message, answer,
        title=f"Research · {meta.get('depth', body.depth or 'standard')}")
    return {"answer": answer, "meta": meta, "run_id": rid, "events": events[-50:]}


@router.post("/research/stream")
async def research_stream(
    body: ResearchRequest, request: Request,
    session: AsyncSession = Depends(get_session),
):
    """SSE stream of stage events; one terminal chunk carries the answer."""
    gw = _gateway(request)
    if body.depth == "deep" and not entitled(
            await optional_user(request), "deep_research"):
        from app.services import entitlements
        raise HTTPException(403, entitlements.upgrade_error("deep_research"))
    conv_key = str(body.conversation_id) if body.conversation_id else None
    # Refresh-resilient: if this conversation already has a detached job
    # running, replay its buffered frames instead of starting over.
    if conv_key:
        from app.services import chat_jobs

        running = chat_jobs.get_running(conv_key)
        if running is not None:
            return StreamingResponse(
                running.stream(), media_type="text/event-stream",
                headers={"Cache-Control": "no-cache"})
    # Fresh runs meter; replays of a detached job are free.
    _u0 = await optional_user(request)
    if _u0 is not None:
        from app.services import usage
        ok, reason = await usage.check_quota(
            session, subject_for(_u0), "research", _u0.plan)
        if not ok:
            raise HTTPException(429, reason)
        await usage.record(session, subject_for(_u0), "research",
                           model=body.model or "")
        # Commit now: the flush holds SQLite's single write lock, and this
        # request's session stays open for the whole stream. The ledger
        # session below (and every other writer) would deadlock on
        # "database is locked" past busy_timeout.
        try:
            await session.commit()
        except Exception:
            await session.rollback()
    from app.services import artifacts as _artifacts

    _artifacts.set_context(
        source="research", owner_id=_u0.id if _u0 else None)
    queue: asyncio.Queue[dict] = asyncio.Queue()
    state_box: dict[str, Any] = {}
    # Durable agent ledger for this research run.
    from app.db import get_session_factory
    factory = get_session_factory()
    async with factory() as ls:
        arun = await agent_log.start_run(
            ls, "research", body.message, model=body.model or "auto",
            meta={"depth": body.depth or "auto"})
        await ls.commit()
    _t0 = time.monotonic()

    _user = await optional_user(request)
    _owner = _user.id if _user else None

    async def run() -> None:
        # The run must outlive the HTTP request: it gets its OWN sessions
        # (the request-scoped one is closed as soon as this handler returns
        # the streaming response — using it here is why refreshed research
        # silently died).
        try:
            async with factory() as rs:
                state = await run_research(
                    gw, rs, _messages(body), model=body.model, depth=body.depth,
                    conversation_id=body.conversation_id,
                    on_event=agent_log.tap_emit(arun, queue.put_nowait),
                )
            state_box["state"] = state
            async with factory() as ps:
                rid = await _persist(ps, body, state, state.answer)
            from app.services import knowledge_capture

            knowledge_capture.schedule(
                _owner, "research", body.message, state.answer,
                title=f"Research · {getattr(state, 'depth', body.depth or 'standard')}")
            queue.put_nowait({"event": "research_answer", "answer": state.answer,
                              "run_id": rid, **(state.meta or {})})
            async with factory() as ls:
                await agent_log.finish_run(
                    ls, arun, "done",
                    meta={"research_run_id": rid, "depth": state.depth},
                    duration_ms=int((time.monotonic() - _t0) * 1000))
                await ls.commit()
        except asyncio.CancelledError:
            queue.put_nowait({"event": "error", "text": "research stopped"})
            async with factory() as ls:
                await agent_log.finish_run(
                    ls, arun, "cancelled",
                    duration_ms=int((time.monotonic() - _t0) * 1000))
                await ls.commit()
            raise
        except Exception as e:
            queue.put_nowait({"event": "error",
                              "text": f"research failed: {str(e)[:200]}"})
            async with factory() as ls:
                await agent_log.finish_run(ls, arun, "failed",
                                           duration_ms=int((time.monotonic() - _t0) * 1000))
                await ls.commit()

    task = asyncio.ensure_future(run())

    async def gen():
        try:
            while not (task.done() and queue.empty()):
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=0.2)
                except (asyncio.TimeoutError, TimeoutError):
                    continue
                if ev.get("event") == "research_answer":
                    content = ev.pop("answer")
                    chunk = {
                        "id": f"research-{uuid.uuid4().hex[:8]}",
                        "object": "chat.completion.chunk",
                        "choices": [{"index": 0, "delta": {"content": content},
                                     "finish_reason": "stop"}],
                    }
                    yield f"data: {json.dumps(chunk)}\n\n"
                    yield _meta({k: v for k, v in ev.items() if k != "event"})
                    yield "data: [DONE]\n\n"
                    return
                yield _meta(ev)
            yield "data: [DONE]\n\n"
        finally:
            # Job-wrapped streams only tear down early on an explicit
            # job.cancel() (a viewer refresh just re-attaches to the
            # pump), so tearing down here = a real stop request: kill the
            # detached research task instead of letting it burn provider
            # calls nobody will read.
            if conv_key and not task.done():
                task.cancel()

    if conv_key:
        from app.services import chat_jobs

        job = chat_jobs.start(conv_key, gen())
        job.linked.append(task)
        return StreamingResponse(
            job.stream(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/research/live/{conv_id}")
async def research_live_status(conv_id: str) -> dict[str, Any]:
    """Job status for a research run key — lets a refreshed page decide
    whether to re-attach or show the run as interrupted."""
    from app.services import chat_jobs

    return chat_jobs.status(chat_jobs.get(conv_id))


@router.get("/research/live/{conv_id}/stream")
async def research_live_stream(conv_id: str) -> StreamingResponse:
    """Re-attach to a detached research run: replays buffered SSE frames
    then tails live, ending with [DONE]."""
    from app.services import chat_jobs

    job = chat_jobs.get(conv_id)

    async def _empty() -> Any:
        yield b"data: [DONE]\n\n"

    return StreamingResponse(
        job.stream() if job else _empty(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@router.post("/research/stop")
async def research_stop(body: dict[str, Any]):
    """Cancel the running research job for a conversation key.

    Cancelling the job's pump tears the frame generator down, which cancels
    the detached research task (see ``gen``'s ``finally``).
    """
    conv_key = str((body or {}).get("conversation_id") or "")
    if not conv_key:
        return {"stopped": False}
    from app.services import chat_jobs

    job = chat_jobs.get_running(conv_key) or chat_jobs.get(conv_key)
    if job is None:
        return {"stopped": False}
    job.cancel()
    return {"stopped": True}


# ---------- saved runs (admin view) ----------


@router.get("/research/runs")
async def list_runs(limit: int = 20, session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(
        select(ResearchRun).order_by(desc(ResearchRun.created_at)).limit(min(limit, 100))
    )).scalars().all()
    return [{
        "id": r.id, "question": (r.question or "")[:200], "depth": r.depth,
        "answer_preview": (r.answer or "")[:200],
        "sources": len(r.sources or []),
        "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows]


@router.get("/research/runs/{run_id}")
async def get_run(run_id: int, session: AsyncSession = Depends(get_session)):
    r = await session.get(ResearchRun, run_id)
    if r is None:
        return JSONResponse(status_code=404, content={"error": {"message": "not found"}})
    meta = dict(r.meta or {})
    meta.pop("document", None)   # cached textbook doc is served via /document
    from app.services.research import _strip_group_markers
    return {
        "id": r.id, "question": r.question, "depth": r.depth,
        "answer": _strip_group_markers(r.answer),
        "plan": r.plan, "findings": r.findings, "sources": r.sources, "meta": meta,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


# ---------- textbook document + PDF export ----------


async def _document_for_run(
    session: AsyncSession, request: Request, run_id: int,
) -> tuple[ResearchRun | None, Any]:
    """Load the composed ResearchDocument for a run, composing + caching it
    (in ``run.meta["document"]``) on first request. The composer agent is
    the primary path; the deterministic fallback keeps the book view alive
    when no candidate model produces a valid document."""
    from app.services.research_composer import (
        compose_document, markdown_to_document)
    from app.services.research_doc import validate_document
    from app.services.research import (_strip_group_markers,
        _strip_meta_fences)

    r = await session.get(ResearchRun, run_id)
    if r is None:
        return None, None
    meta = dict(r.meta or {})
    cached = meta.get("document")
    if isinstance(cached, dict):
        try:
            return r, validate_document(cached)
        except Exception:
            pass
    answer = _strip_meta_fences(_strip_group_markers(r.answer or ""))
    doc = await compose_document(
        _gateway(request), r.question, answer,
        sources=r.sources or [], run_id=r.id)
    if doc is None:
        doc = markdown_to_document(
            r.question, answer, sources=r.sources or [], run_id=r.id)
    if doc is None:
        return r, None
    meta["document"] = doc.model_dump(mode="json")
    r.meta = meta
    await session.commit()
    return r, doc


@router.get("/research/runs/{run_id}/document")
async def research_document(
    run_id: int, request: Request,
    session: AsyncSession = Depends(get_session),
):
    """The structured textbook document for a saved run (composes lazily)."""
    r, doc = await _document_for_run(session, request, run_id)
    if r is None:
        return JSONResponse(status_code=404,
                            content={"error": {"message": "not found"}})
    if doc is None:
        return JSONResponse(
            status_code=422,
            content={"error": {"message": "run has no answer to compose"}})
    return doc.model_dump(mode="json")


# One compose task per run: page refreshes and extra viewers attach to the
# in-flight job instead of restarting it. Populated/cleared by _compose_task.
_COMPOSE: dict[int, dict[str, Any]] = {}


async def _compose_task(run_id: int, gateway: Any) -> None:
    """Background composition for one run. Broadcasts real progress +
    per-section markdown to every attached subscriber queue, then caches the
    finished document under run.meta["document"] with its own session so it
    survives the request that started it."""
    from app.db import get_session_factory
    from app.services.research_composer import (
        compose_document, markdown_to_document)
    from app.services.research import (_strip_group_markers,
        _strip_meta_fences)

    entry = _COMPOSE[run_id]

    def broadcast(ev: dict[str, Any]) -> None:
        for q in list(entry["subs"]):
            q.put_nowait(ev)

    try:
        async with get_session_factory()() as s:
            r = await s.get(ResearchRun, run_id)
            if r is None:
                entry["err"] = "not found"
                return
            answer = _strip_meta_fences(_strip_group_markers(r.answer or ""))

            def prog(done: int, total: int, label: str,
                     payload: Any = None) -> None:
                entry["done"], entry["total"] = done, total
                broadcast({"event": "compose_progress", "done": done,
                           "total": total, "label": label})
                if isinstance(payload, dict):
                    idx = payload.get("index")
                    ch = {"index": idx, "title": payload.get("title") or label,
                          "intro": payload.get("intro") or "",
                          "blocks": payload.get("blocks") or []}
                    entry["chapters"][idx] = ch
                    broadcast({"event": "chapter", **ch})

            doc = await compose_document(
                gateway, r.question, answer,
                sources=r.sources or [], run_id=r.id, on_progress=prog)
            if doc is None:
                doc = markdown_to_document(
                    r.question, answer, sources=r.sources or [], run_id=r.id)
            if doc is not None:
                meta = dict(r.meta or {})
                meta["document"] = doc.model_dump(mode="json")
                r.meta = meta
                await s.commit()
            entry["doc"] = doc
    except Exception as exc:
        entry["err"] = str(exc) or "compose failed"
    finally:
        broadcast({"event": "_final"})
        # Only pop if this task's entry is still the registered one — a
        # forced regenerate may have replaced it already.
        if _COMPOSE.get(run_id) is entry:
            _COMPOSE.pop(run_id, None)


@router.get("/research/runs/{run_id}/document/stream")
async def research_document_stream(
    run_id: int, request: Request, force: bool = False,
    session: AsyncSession = Depends(get_session),
):
    """SSE stream of real composition progress + finished chapters, then the
    composed document. Attaching to an in-flight compose replays the progress
    snapshot — a page refresh resumes watching instead of restarting.
    ``?force=1`` discards the cached document and any in-flight compose so the
    book is regenerated from the stored answer."""
    from app.services.research_doc import validate_document

    r = await session.get(ResearchRun, run_id)
    if r is None:
        return JSONResponse(status_code=404,
                            content={"error": {"message": "not found"}})
    if force:
        old = _COMPOSE.pop(run_id, None)
        if old is not None:
            old["superseded"] = True
            for q in list(old["subs"]):
                q.put_nowait({"event": "_final"})
            task = old.get("task")
            if task is not None:
                task.cancel()
        meta = dict(r.meta or {})
        if "document" in meta:
            del meta["document"]
            r.meta = meta
            await session.commit()
    else:
        cached = (r.meta or {}).get("document")
        if isinstance(cached, dict):
            try:
                doc = validate_document(cached)
                async def cached_gen():
                    yield "data: " + json.dumps(
                        {"event": "document",
                         "document": doc.model_dump(mode="json")}) + "\n\n"
                return StreamingResponse(cached_gen(),
                                         media_type="text/event-stream")
            except Exception:
                pass

    entry = _COMPOSE.get(run_id)
    if entry is None:
        entry = {"subs": set(), "done": 0, "total": 0,
                 "chapters": {}, "doc": None, "err": None, "task": None}
        _COMPOSE[run_id] = entry
        entry["task"] = asyncio.create_task(
            _compose_task(run_id, _gateway(request)))

    async def gen():
        def send(ev: dict[str, Any]) -> str:
            return "data: " + json.dumps(ev) + "\n\n"

        q: asyncio.Queue = asyncio.Queue()
        entry["subs"].add(q)
        try:
            # Replay state so a mid-flight attach catches up instantly.
            if entry["total"]:
                yield send({"event": "compose_progress",
                            "done": entry["done"], "total": entry["total"],
                            "label": "resumed"})
            for i, ch in sorted(entry["chapters"].items()):
                yield send({"event": "chapter", **ch})
            while True:
                ev = await q.get()
                if ev["event"] == "_final":
                    break
                yield send(ev)
            if entry.get("superseded"):
                yield send({"event": "superseded"})
                return
            if entry["doc"] is not None:
                yield send({"event": "document",
                            "document": entry["doc"].model_dump(mode="json")})
            else:
                yield send({"event": "error",
                            "message": entry["err"]
                                       or "run has no answer to compose"})
        finally:
            entry["subs"].discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/research/runs/{run_id}/pdf")
async def research_pdf(
    run_id: int, request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Export the textbook as a print-faithful PDF. The headless browser
    loads the same ``/textbook`` page users see — one renderer for screen
    and paper — then prints it with backgrounds."""
    r, doc = await _document_for_run(session, request, run_id)
    if r is None:
        return JSONResponse(status_code=404,
                            content={"error": {"message": "not found"}})
    if doc is None:
        return JSONResponse(
            status_code=422,
            content={"error": {"message": "run has no answer to compose"}})
    base = str(request.base_url).rstrip("/")
    # Forward the caller's credentials so the headless page's document
    # fetch authenticates exactly like the on-screen one.
    headers: dict[str, str] = {}
    if request.headers.get("x-user-token"):
        headers["x-user-token"] = request.headers["x-user-token"]
    if request.headers.get("authorization"):
        headers["authorization"] = request.headers["authorization"]
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return JSONResponse(
            status_code=501,
            content={"error": {"message": "PDF export requires playwright"}})
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await browser.new_page()
            if headers:
                await page.set_extra_http_headers(headers)
            await page.goto(f"{base}/textbook/{run_id}?print=1",
                            wait_until="networkidle", timeout=90000)
            await page.wait_for_selector(".tb-cover, .tb-error",
                                         timeout=45000)
            pdf = await page.pdf(format="A4", print_background=True,
                                 prefer_css_page_size=True)
        finally:
            await browser.close()
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition":
                 f'attachment; filename="nova-research-{run_id}.pdf"'})
