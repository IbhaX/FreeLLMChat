"""Deep-research endpoints: run the pipeline, stream stage events, and
list/get saved research runs."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_gateway_key
from app.db import get_session, get_session_factory
from app.models import ResearchRun
from app.services.research import run_research

router = APIRouter(prefix="/v1", dependencies=[Depends(require_gateway_key)])

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
    return f"data: {json.dumps({'freellm': ev})}\n\n".encode()


def _state_payload(state) -> tuple[list, list, list]:
    findings = [{
        "claim": f.claim, "evidence": f.evidence, "source_id": f.source_id,
        "confidence": f.confidence, "verdict": f.verdict,
        "sub_question": f.sub_question,
    } for f in state.findings]
    sources = [{
        "id": s.id, "title": s.title, "url": s.url, "publisher": s.publisher,
        "date": s.date, "type": s.type, "quality": s.quality, "score": s.score,
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
    if answer:
        from app.services.component_enforce import enforce_components
        fixed, _metrics = await enforce_components(
            gw, session, {"messages": _messages(body), "model": body.model},
            {"choices": [{"message": {"role": "assistant", "content": answer}}]},
            model=body.model,
        )
        answer = ((fixed.get("choices") or [{}])[0].get("message") or {}).get("content") or answer
        state.answer = answer
    done = next((e for e in reversed(events) if e.get("event") == "research_done"), {})
    meta = {k: v for k, v in done.items() if k not in ("event", "text")}
    rid = await _persist(session, body, state, answer)
    return {"answer": answer, "meta": meta, "run_id": rid, "events": events[-50:]}


@router.post("/research/stream")
async def research_stream(
    body: ResearchRequest, request: Request,
    session: AsyncSession = Depends(get_session),
):
    """SSE stream of stage events; one terminal chunk carries the answer."""
    gw = _gateway(request)
    queue: asyncio.Queue[dict] = asyncio.Queue()
    state_box: dict[str, Any] = {}

    async def run() -> None:
        try:
            state = await run_research(
                gw, session, _messages(body), model=body.model, depth=body.depth,
                conversation_id=body.conversation_id, on_event=queue.put_nowait,
            )
            if state.answer:
                from app.services.component_enforce import enforce_components
                async with get_session_factory()() as component_session:
                    fixed, _metrics = await enforce_components(
                        gw, component_session,
                        {"messages": _messages(body), "model": body.model},
                        {"choices": [{"message": {"role": "assistant", "content": state.answer}}]},
                        model=body.model,
                    )
                state.answer = ((fixed.get("choices") or [{}])[0].get("message") or {}).get("content") or state.answer
            state_box["state"] = state
            rid = await _persist(session, body, state, state.answer)
            queue.put_nowait({"event": "research_answer", "answer": state.answer,
                              "run_id": rid, **(state.meta or {})})
        except Exception as e:
            queue.put_nowait({"event": "error",
                              "text": f"research failed: {str(e)[:200]}"})

    task = asyncio.ensure_future(run())

    async def gen():
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

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
    return {
        "id": r.id, "question": r.question, "depth": r.depth, "answer": r.answer,
        "plan": r.plan, "findings": r.findings, "sources": r.sources, "meta": r.meta,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }
