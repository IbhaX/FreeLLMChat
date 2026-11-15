"""/v1/agents and /admin/agents endpoints."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin, require_gateway_key
from app.db import get_session
from app.models import AgentConfig
from app.schemas import AgentChatRequest, AgentCreate, AgentOut, AgentUpdate
from app.services.agents import run_agent, stream_agent

# Public chat router (gateway-key protected)
chat_router = APIRouter(prefix="/v1/agents", dependencies=[Depends(require_gateway_key)])
# Admin CRUD router
admin_router = APIRouter(prefix="/admin/agents", dependencies=[Depends(require_admin)])


def _gateway(request: Request) -> "Gateway":
    return request.app.state.gateway


async def _get_agent(session: AsyncSession, agent_id: int) -> AgentConfig:
    a = (
        await session.execute(
            select(AgentConfig).where(AgentConfig.id == agent_id)
        )
    ).scalar_one_or_none()
    if a is None:
        raise HTTPException(404, "agent not found")
    return a


async def _get_agent_by_name(session: AsyncSession, name: str) -> AgentConfig:
    a = (
        await session.execute(
            select(AgentConfig).where(AgentConfig.name == name)
        )
    ).scalar_one_or_none()
    if a is None:
        raise HTTPException(404, f"agent '{name}' not found")
    return a


# ---------- Admin CRUD ----------

@admin_router.get("", response_model=list[AgentOut])
async def list_agents(session: AsyncSession = Depends(get_session)) -> list[AgentOut]:
    agents = (await session.execute(select(AgentConfig))).scalars().all()
    return [AgentOut.model_validate(a) for a in agents]


@admin_router.post("", response_model=AgentOut, status_code=201)
async def create_agent(
    body: AgentCreate, session: AsyncSession = Depends(get_session)
) -> AgentOut:
    exists = (
        await session.execute(select(AgentConfig).where(AgentConfig.name == body.name))
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(409, "agent already exists")
    a = AgentConfig(**body.model_dump())
    session.add(a)
    await session.commit()
    return AgentOut.model_validate(a)


@admin_router.get("/{agent_id}", response_model=AgentOut)
async def get_agent(
    agent_id: int, session: AsyncSession = Depends(get_session)
) -> AgentOut:
    return AgentOut.model_validate(await _get_agent(session, agent_id))


@admin_router.patch("/{agent_id}", response_model=AgentOut)
async def update_agent(
    agent_id: int,
    body: AgentUpdate,
    session: AsyncSession = Depends(get_session),
) -> AgentOut:
    a = await _get_agent(session, agent_id)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(a, k, v)
    await session.commit()
    return AgentOut.model_validate(a)


@admin_router.delete("/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: int, session: AsyncSession = Depends(get_session)
) -> None:
    a = await _get_agent(session, agent_id)
    await session.delete(a)
    await session.commit()


# ---------- Public chat ----------

@chat_router.post("/{agent_id}/chat")
async def agent_chat(
    agent_id: int,
    body: AgentChatRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    a = await _get_agent(session, agent_id)
    if not a.enabled:
        raise HTTPException(403, "agent is disabled")
    gw = _gateway(request)
    from app.services.settings_store import get_custom_instructions
    from app.services import agent_log
    from app.db import get_session_factory
    custom = await get_custom_instructions(session)
    factory = get_session_factory()
    async with factory() as ls:
        arun = await agent_log.start_run(ls, "agent", body.message,
                                         meta={"agent_id": a.id, "agent": a.name})
        await ls.commit()
    _t0 = time.monotonic()

    async def _wrap_stream():
        try:
            async for chunk in stream_agent(gw, a, body.message, body.history,
                                            custom_instructions=custom,
                                            conversation_id=body.conversation_id):
                yield chunk
            async with factory() as ls:
                await agent_log.finish_run(ls, arun, "done",
                    meta={"agent": a.name, "stream": True},
                    duration_ms=int((time.monotonic()-_t0)*1000))
                await ls.commit()
        except Exception as e:
            async with factory() as ls:
                await agent_log.finish_run(ls, arun, "failed",
                    meta={"error": str(e)[:200]},
                    duration_ms=int((time.monotonic()-_t0)*1000))
                await ls.commit()
            raise

    if body.stream:
        return StreamingResponse(
            _wrap_stream(),
            media_type="text/event-stream",
            headers={"X-Agent-Name": a.name, "Cache-Control": "no-cache"},
        )
    try:
        result = await run_agent(gw, a, body.message, body.history,
                                 custom_instructions=custom, conversation_id=body.conversation_id)
    except Exception as e:
        async with factory() as ls:
            await agent_log.finish_run(ls, arun, "failed",
                meta={"error": str(e)[:200]},
                duration_ms=int((time.monotonic()-_t0)*1000))
            await ls.commit()
        return JSONResponse(
            status_code=502,
            content={"error": {"message": str(e), "type": "agent_error"}},
        )
    async with factory() as ls:
        await agent_log.finish_run(ls, arun, "done", meta={"agent": a.name},
            duration_ms=int((time.monotonic()-_t0)*1000))
        await ls.commit()
    return JSONResponse(
        content={"role": "assistant", "content": result},
        headers={"X-Agent-Name": a.name},
    )


@chat_router.get("")
async def list_public_agents(
    session: AsyncSession = Depends(get_session),
) -> dict:
    agents = (
        (await session.execute(select(AgentConfig).where(AgentConfig.enabled.is_(True))))
        .scalars().all()
    )
    return {
        "object": "list",
        "data": [
            {"id": a.id, "name": a.name, "description": a.description, "model": a.model}
            for a in agents
        ],
    }


# ---------- Agent run ledger (observability) ----------

@chat_router.get("/runs")
async def list_agent_runs(
    limit: int = 30, kind: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Durable history of every agentic run (orchestrate/research/agent)."""
    from app.services import agent_log
    runs = await agent_log.list_runs(session, limit=limit, kind=kind)
    return {"runs": [{
        "id": r.id, "kind": r.kind, "question": (r.question or "")[:160],
        "subject": r.subject, "model": r.model, "status": r.status,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "duration_ms": r.duration_ms,
        "meta": {k: v for k, v in (r.meta or {}).items()
                 if k in ("intent", "strategy", "depth", "agent", "steps")},
    } for r in runs]}


@chat_router.get("/runs/{run_id}")
async def get_agent_run(run_id: int,
                        session: AsyncSession = Depends(get_session)) -> dict:
    """Full trace of one run — every recorded step with actor/status/duration."""
    from app.models import AgentRun
    r = await session.get(AgentRun, run_id)
    if not r:
        raise HTTPException(404, "run not found")
    return {
        "id": r.id, "kind": r.kind, "question": r.question,
        "subject": r.subject, "model": r.model, "status": r.status,
        "meta": r.meta, "started_at": r.started_at.isoformat() if r.started_at else None,
        "duration_ms": r.duration_ms,
        "events": [{
            "seq": e.seq, "stage": e.stage, "actor": e.actor,
            "event": e.event, "detail": e.detail, "status": e.status,
            "duration_ms": e.duration_ms,
            "ts": e.ts.isoformat() if e.ts else None,
        } for e in sorted(r.events, key=lambda x: x.seq)],
    }
