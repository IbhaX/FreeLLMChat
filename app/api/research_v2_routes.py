"""Nova v2 research endpoints (spec §13, Phase 1 surface).

Runs are durable jobs: POST creates the run and returns immediately;
progress is streamed from ``run_events`` over SSE with ``Last-Event-ID``
resume. The v1 pipeline currently executes inside the run — the adaptive
v2 engine slots in behind the same contract in Phase 2.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (entitled, optional_user, require_user,
                          subject_for)
from app.db import get_session, get_session_factory
from app.models import (
    Claim,
    Report,
    ResearchRunV2,
    Source,
)
from app.services import evidence_store as es
from app.services import research_v2 as rv2
from app.services.research import classify_depth

router = APIRouter(prefix="/v1/research2",
                   dependencies=[Depends(require_user)])

Depth = Literal["quick", "standard", "deep"]


# ------------------------------------------------------------------ bodies

class RunCreate(BaseModel):
    question: str = Field(min_length=1, max_length=8000)
    depth: Optional[Depth] = None
    options: dict[str, Any] = Field(default_factory=dict)
    budget_override: dict[str, Any] = Field(default_factory=dict)
    conversation_id: Optional[str] = None


class SteerBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


# ------------------------------------------------------------------ helpers

def _err(status: int, code: str, message: str,
         details: Optional[dict[str, Any]] = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={
        "error": {"code": code, "message": message,
                  "details": details or {}}})


def _run_view(run: ResearchRunV2) -> dict[str, Any]:
    return {
        "id": run.id, "question": run.question, "depth": run.depth,
        "state": run.state, "degraded": run.degraded,
        "budget": run.budget, "spent": run.spent,
        "plan_version": run.plan_version, "error": run.error,
        "result_ref": run.result_ref,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "events_url": f"/v1/research2/runs/{run.id}/events",
    }


async def _get_run(session: AsyncSession, run_id: str) -> Optional[ResearchRunV2]:
    return await session.get(ResearchRunV2, run_id)


# ------------------------------------------------------------------ routes

@router.post("/runs", status_code=201)
async def create_run(
    body: RunCreate, request: Request,
    session: AsyncSession = Depends(get_session),
    user=Depends(optional_user),
):
    idem = request.headers.get("idempotency-key")
    depth = body.depth or classify_depth(body.question)
    if depth == "deep" and not entitled(user, "deep_research"):
        from app.services import entitlements
        raise HTTPException(403, entitlements.upgrade_error("deep_research"))
    if user is not None:
        from app.services import usage
        ok, reason = await usage.check_quota(
            session, subject_for(user), "research", user.plan)
        if not ok:
            return _err(429, "quota_exceeded", reason)
        await usage.record(session, subject_for(user), "research",
                           model="auto")
    run, created = await rv2.create_run(
        session, question=body.question, depth=depth,
        options=body.options, budget_override=body.budget_override,
        owner_id=user.id if user else None,
        conversation_id=body.conversation_id,
        idempotency_key=idem)
    await session.commit()
    payload = _run_view(run)
    payload["created"] = created
    return JSONResponse(status_code=201 if created else 200, content=payload)


@router.get("/runs")
async def list_runs(limit: int = 20,
                    session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(
        select(ResearchRunV2)
        .order_by(desc(ResearchRunV2.created_at)).limit(min(limit, 100))
    )).scalars().all()
    return [_run_view(r) for r in rows]


@router.get("/runs/{run_id}")
async def get_run(run_id: str, session: AsyncSession = Depends(get_session)):
    run = await _get_run(session, run_id)
    if run is None:
        return _err(404, "not_found", "unknown run")
    return _run_view(run)


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, request: Request,
                     session: AsyncSession = Depends(get_session)):
    """SSE stream of run events. Reconnect with ``Last-Event-ID: <seq>`` to
    replay everything after that sequence number, then follow live."""
    run = await _get_run(session, run_id)
    if run is None:
        return _err(404, "not_found", "unknown run")
    factory = get_session_factory()
    last_id = request.headers.get("last-event-id")
    cursor = int(last_id) if last_id and last_id.isdigit() else 0

    async def gen():
        nonlocal cursor
        while True:
            async with factory() as s:
                events = await rv2.list_events(s, run_id, after_seq=cursor)
                run_now = await s.get(ResearchRunV2, run_id)
            for ev in events:
                cursor = max(cursor, ev.seq)
                yield (f"id: {ev.seq}\nevent: {ev.type}\n"
                       f"data: {json.dumps(ev.payload, default=str)}\n\n")
            state = run_now.state if run_now else "done"
            if state in rv2.TERMINAL_STATES and cursor >= (
                    events[-1].seq if events else cursor):
                yield "data: [DONE]\n\n"
                return
            await asyncio.sleep(1.0)
            if await request.is_disconnected():
                return

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, session: AsyncSession = Depends(get_session)):
    run = await _get_run(session, run_id)
    if run is None:
        return _err(404, "not_found", "unknown run")
    await rv2.cancel_run(session, run)
    await session.commit()
    return {"state": run.state}


@router.post("/runs/{run_id}/resume")
async def resume_run(run_id: str, session: AsyncSession = Depends(get_session)):
    run = await _get_run(session, run_id)
    if run is None:
        return _err(404, "not_found", "unknown run")
    if run.state not in ("failed", "cancelled", "degraded"):
        return _err(409, "invalid_state",
                    f"cannot resume a run in state '{run.state}'")
    job = await rv2.resume_run(session, run)
    await session.commit()
    return {"state": run.state, "job_id": job.id}


@router.post("/runs/{run_id}/steer", status_code=202)
async def steer_run(run_id: str, body: SteerBody,
                    session: AsyncSession = Depends(get_session)):
    run = await _get_run(session, run_id)
    if run is None:
        return _err(404, "not_found", "unknown run")
    if run.state in rv2.TERMINAL_STATES:
        return _err(409, "invalid_state", "run already finished")
    msg = await rv2.steer(session, run, body.text)
    await session.commit()
    return {"steer_id": msg.id, "applies_at": "next_round"}


@router.get("/runs/{run_id}/evidence")
async def run_evidence(run_id: str,
                       question_id: Optional[str] = None,
                       session: AsyncSession = Depends(get_session)):
    run = await _get_run(session, run_id)
    if run is None:
        return _err(404, "not_found", "unknown run")
    evs = await es.list_evidence(session, run_id, question_id=question_id)
    src_ids = {e.source_id for e in evs}
    srcs = {}
    for sid in src_ids:
        src = await session.get(Source, sid)
        if src:
            srcs[sid] = src
    return {
        "evidence": [{
            "id": e.id, "question_id": e.question_id,
            "source_id": e.source_id, "quote": e.quote,
            "summary": e.summary, "context": e.context,
            "extracted_by": e.extracted_by,
            "source": {
                "url": srcs[e.source_id].url,
                "domain": srcs[e.source_id].domain,
                "title": srcs[e.source_id].title,
                "score": srcs[e.source_id].score,
            } if e.source_id in srcs else None,
        } for e in evs],
        "claims": [{
            "id": c.id, "text": c.text, "verdict": c.verdict,
            "is_key": c.is_key, "confidence": c.confidence,
            "independent_sources": c.independent_sources,
        } for c in (await session.execute(
            select(Claim).where(Claim.run_id == run_id))).scalars().all()],
    }


@router.get("/runs/{run_id}/report")
async def run_report(run_id: str, session: AsyncSession = Depends(get_session)):
    run = await _get_run(session, run_id)
    if run is None:
        return _err(404, "not_found", "unknown run")
    report = None
    if run.result_ref:
        report = await session.get(Report, run.result_ref)
    if report is None:
        report = (await session.execute(
            select(Report).where(Report.run_id == run_id)
            .order_by(desc(Report.version)).limit(1)
        )).scalar_one_or_none()
    if report is None:
        return _err(404, "not_found", "no report yet",
                    {"state": run.state})
    return {
        "report_id": report.id, "version": report.version,
        "markdown": report.markdown, "components": report.components,
        "quality": report.quality,
        "created_at": report.created_at.isoformat() if report.created_at
        else None,
    }
