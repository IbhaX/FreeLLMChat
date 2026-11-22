"""/v1/agents and /admin/agents endpoints."""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from app.gateway import Gateway

from app.api.deps import (
    optional_user, require_admin, require_user,
    require_user_or_admin, subject_for)
from app.db import get_session
from app.models import AgentConfig
from app.schemas import AgentChatRequest, AgentCreate, AgentOut, AgentUpdate
from app.services.agents import run_agent, stream_agent

# Public chat router (gateway-key protected)
chat_router = APIRouter(
    prefix="/v1/agents",
    dependencies=[Depends(require_user)])
# Admin CRUD router
admin_router = APIRouter(prefix="/admin/agents", dependencies=[Depends(require_admin)])
# Read-only listing — signed-in users browse agents; only writes need admin.
admin_read_router = APIRouter(
    prefix="/admin/agents", dependencies=[Depends(require_user_or_admin)])


def _gateway(request: Request) -> Gateway:
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

@admin_read_router.get("", response_model=list[AgentOut])
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


@admin_read_router.get("/{agent_id}", response_model=AgentOut)
async def get_agent(
    agent_id: int, session: AsyncSession = Depends(get_session)
) -> AgentOut:
    return AgentOut.model_validate(await _get_agent(session, agent_id))


# Fields ``seed_experts`` rewrites on boot for platform-managed agents.
_SEEDED_FIELDS = frozenset(
    {"description", "system_prompt", "enable_tools", "output_format", "config"}
)


@admin_router.patch("/{agent_id}", response_model=AgentOut)
async def update_agent(
    agent_id: int,
    body: AgentUpdate,
    session: AsyncSession = Depends(get_session),
) -> AgentOut:
    a = await _get_agent(session, agent_id)
    data = body.model_dump(exclude_none=True)
    cfg = data.pop("config", None)
    if cfg is not None:
        a.config = {**(a.config or {}), **cfg}
    for k, v in data.items():
        setattr(a, k, v)
    if (a.config or {}).get("builtin") and (
        _SEEDED_FIELDS & data.keys() or cfg is not None
    ):
        # Editing a seeded field detaches the row from boot-time refresh
        # so the change isn't reverted on restart; model/enabled edits
        # already persist without the flag.
        a.config = {**(a.config or {}), "customized": True}
    await session.commit()
    return AgentOut.model_validate(a)


@admin_router.post("/{agent_id}/reset", response_model=AgentOut)
async def reset_agent(
    agent_id: int, session: AsyncSession = Depends(get_session)
) -> AgentOut:
    """Restore a built-in agent to its registry definition."""
    a = await _get_agent(session, agent_id)
    if not (a.config or {}).get("builtin"):
        raise HTTPException(400, "only built-in agents can be reset")
    from app.services.experts import get_expert

    expert = get_expert(a.name)
    if expert is None:
        raise HTTPException(404, "no registry definition for this agent")
    d = expert.to_agent_dict()
    a.description = d["description"]
    a.system_prompt = d["system_prompt"]
    a.enable_tools = d["enable_tools"]
    a.output_format = d["output_format"]
    a.config = d["config"]  # fresh dict clears the customized flag too
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
    _user = await optional_user(request)
    _usage_ev_id = None
    if _user is not None:
        from app.services import usage
        ok, reason = await usage.check_quota(
            session, subject_for(_user), "chat", _user.plan)
        if not ok:
            raise HTTPException(429, reason)
        _uev = await usage.record(session, subject_for(_user), "chat",
                                  model=body.model or a.model or "")
        _usage_ev_id = _uev.id if _uev is not None else None
        # Commit now: the flush holds SQLite's single write lock and this
        # session outlives the request; the run's own sessions below would
        # otherwise hit "database is locked".
        try:
            await session.commit()
        except Exception:
            await session.rollback()
    _owner = _user.id if _user else None
    from app.db import get_session_factory
    from app.services import agent_log
    from app.services import artifacts as _artifacts

    _artifacts.set_context(
        source=f"agent:{a.name}", owner_id=_owner,
        conversation_id=body.conversation_id)
    from app.services.settings_store import get_custom_instructions
    custom = await get_custom_instructions(session)
    factory = get_session_factory()
    async with factory() as ls:
        arun = await agent_log.start_run(ls, "agent", body.message,
                                         meta={"agent_id": a.id, "agent": a.name})
        await ls.commit()
    _t0 = time.monotonic()
    evq: asyncio.Queue = asyncio.Queue()
    # Announce the delegated agent + its capabilities in the activity panel.
    try:
        from app.services import agents as _agents

        _tools = _agents._tool_names(a)
        _skills = _agents._skill_names(a)
        evq.put_nowait({
            "event": "route", "kind": "decide", "status": "done",
            "level": "normal", "actor": a.name,
            "title": f"Delegating to @{a.name} — model "
                     f"{body.model or a.model or 'auto'} · "
                     f"tools: {'all' if _tools == ['*'] else ', '.join(_tools) or 'none'} · "
                     f"skills: {', '.join(_skills) or 'none'}"})
    except Exception:
        pass

    def _delta_text(raw: bytes) -> str:
        """Pull delta.content text out of an SSE chunk for capture."""
        try:
            out = ""
            for line in raw.decode("utf-8", "replace").split("data:"):
                line = line.strip()
                if not line or line == "[DONE]":
                    continue
                payload = json.loads(line)
                for choice in payload.get("choices") or []:
                    out += ((choice.get("delta") or {}).get("content")) or ""
            return out
        except Exception:
            return ""

    async def _wrap_stream():
        _tok = {"in": 0, "out": 0}

        def _drain() -> bytes:
            ev = evq.get_nowait()
            if isinstance(ev, dict) and ev.get("event") == "usage":
                _tok["in"] += int(ev.get("tokens_in") or 0)
                _tok["out"] += int(ev.get("tokens_out") or 0)
            return f"data: {json.dumps({'nova': ev})}\n\n".encode()

        parts: list[str] = []
        try:
            async for chunk in stream_agent(gw, a, body.message, body.history,
                                            custom_instructions=custom,
                                            conversation_id=body.conversation_id,
                                            model_override=body.model,
                                            emit=evq.put_nowait, user=_user):
                while not evq.empty():
                    yield _drain()
                parts.append(_delta_text(chunk))
                yield chunk
            while not evq.empty():
                yield _drain()
            from app.services import knowledge_capture

            knowledge_capture.schedule(
                _owner, "agent", body.message, "".join(parts),
                title=f"Agent · {a.name}")
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
        finally:
            from app.services import usage as _usage

            await _usage.settle_tokens(
                _usage_ev_id, _tok["in"], _tok["out"])

    if body.stream:
        return StreamingResponse(
            _wrap_stream(),
            media_type="text/event-stream",
            headers={"X-Agent-Name": a.name, "Cache-Control": "no-cache"},
        )
    try:
        result = await run_agent(gw, a, body.message, body.history,
                                 custom_instructions=custom, conversation_id=body.conversation_id,
                                 model_override=body.model, user=_user)
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
    from app.services import knowledge_capture

    knowledge_capture.schedule(
        _owner, "agent", body.message, result, title=f"Agent · {a.name}")
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
    """Full trace of one run — normalized activity events for replay."""
    from app.models import AgentRun
    from app.services import agent_log
    r = await session.get(AgentRun, run_id)
    if not r:
        raise HTTPException(404, "run not found")
    return {
        "id": r.id, "kind": r.kind, "question": r.question,
        "subject": r.subject, "model": r.model, "status": r.status,
        "meta": r.meta, "started_at": r.started_at.isoformat() if r.started_at else None,
        "duration_ms": r.duration_ms,
        "events": await agent_log.list_events(session, run_id),
    }
