"""Group chat (multi-agent) and conversation sharing endpoints."""

from __future__ import annotations

import asyncio
import json
import secrets
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_gateway_key
from app.db import get_session
from app.models import AgentConfig, SharedConversation
from app.services.agents import run_agent

router = APIRouter(prefix="/v1", dependencies=[Depends(require_gateway_key)])

ORCHESTRATOR_NAME = "Orchestrator"

ORCHESTRATOR_PROMPT = """You are the Orchestrator of a multi-agent chat.
Several specialist agents each answered the user's message independently.
Read all their answers, reconcile disagreements, deduplicate, and produce
ONE final best answer for the user.

Always reply with exactly one fenced code block tagged `nova-response`
containing valid JSON — and nothing else:

```nova-response
{"response_type": "complex",
 "content": "<markdown summary of the final answer>",
 "components": [{"type": "<component>", "data": {}}],
 "suggested_prompts": ["<short follow-up>", "..."]}
```

Available component types (use only when they genuinely help; an empty
components list is fine):
  table {title, columns:[...], rows:[[...]]}
  keyvalue {title, pairs:[{key, value}]}
  callout {variant: info|warn|success|error, title, message}
  code {language, code, title}
  stat_grid {stats:[{label, value, delta}]}
  comparison {title, options:[{name, pros:[], cons:[]}]}
  chart {chart_type: bar|line|pie|doughnut, title, labels:[], datasets:[{label, data:[]}]}
  timeline {items:[{label, text}]}
  math {expression}
  instructions {steps:[{title, detail}]}
  json_viewer {data}

Never invent agents' names, never mention "specialists" or that you are
an orchestrator — speak as the single voice of the answer."""


class GroupChatRequest(BaseModel):
    message: str
    history: list[dict[str, Any]] = Field(default_factory=list)
    agent_ids: list[int]
    conversation_id: str | None = None  # scopes short-term vector memory


class ShareRequest(BaseModel):
    title: str = ""
    messages: list[dict[str, Any]]


def _gateway(request: Request):
    return request.app.state.gateway


def _meta(ev: dict) -> bytes:
    return f"data: {json.dumps({'freellm': ev})}\n\n".encode()



@router.post("/groupchat/stream")
async def group_stream(
    body: GroupChatRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Run a group chat turn: each selected agent answers solo in parallel,
    then the built-in Orchestrator synthesizes one structured reply.

    SSE events are `{"freellm": {...}}` meta chunks:
    agent_start / agent_message / group_done — plus a terminal error event.
    """
    agents = (
        (await session.execute(
            select(AgentConfig)
            .where(AgentConfig.id.in_(body.agent_ids), AgentConfig.enabled.is_(True))
        ))
        .scalars()
        .all()
    )
    # preserve the order the user picked them in
    order = {aid: i for i, aid in enumerate(body.agent_ids)}
    agents.sort(key=lambda a: order.get(a.id, 999))
    if not agents:
        return JSONResponse(
            status_code=404,
            content={"error": {"message": "no enabled agents selected",
                               "type": "not_found"}},
        )
    gw = _gateway(request)

    # Orchestrator is built-in (not a user agent): wipe any stale row so it
    # never shows up in the agents list/dropdown, then use an in-memory config.
    stale = (
        await session.execute(
            select(AgentConfig).where(AgentConfig.name == ORCHESTRATOR_NAME)
        )
    ).scalar_one_or_none()
    if stale is not None:
        await session.delete(stale)
        await session.commit()
    orc = AgentConfig(
        id=-1,
        name=ORCHESTRATOR_NAME,
        description="Built-in: combines solo agent answers into one structured reply",
        system_prompt=ORCHESTRATOR_PROMPT,
        model="auto",
        output_format="structured",
        enabled=True,
    )

    from app.services import astra_memory

    conv_id = str(body.conversation_id) if body.conversation_id else None
    astra_memory.ensure_collections()
    if conv_id:
        astra_memory.store_short(conv_id, "user", body.message)

    transcript: list[dict[str, Any]] = list(body.history or [])
    transcript.append({"role": "user", "name": "You", "content": body.message})
    # run_agent ignores `history` for tool-less agents, so context is
    # inlined into the prompt itself.
    transcript_txt = "\n".join(
        f"{h.get('name') or ('You' if h.get('role') == 'user' else 'Agent')}: {h.get('content', '')}"
        for h in transcript[-30:]
    )

    async def solo(a: AgentConfig) -> tuple[AgentConfig, str]:
        prompt = (
            f"Conversation so far:\n{transcript_txt}\n\n"
            f"Answer the latest user message as {a.name}. Be thorough and well-structured: answer fully, cover the relevant substance, and use the component format for anything with more than one fact."
        )
        try:
            return a, await run_agent(gw, a, prompt, [])
        except Exception as e:
            return a, f"*(error: {e})*"

    async def gen():
        try:
            names = ", ".join(a.name for a in agents)
            yield _meta({"event": "group_start",
                         "text": f"Orchestrating: {names}",
                         "agents": [a.name for a in agents] + [ORCHESTRATOR_NAME]})
            # Phase 1 — every agent answers solo, in parallel.
            for a in agents:
                yield _meta({"event": "agent_start",
                             "agent": a.name,
                             "text": f"{a.name} is thinking…"})
            pending = {asyncio.ensure_future(solo(a)): a for a in agents}
            answers: list[tuple[AgentConfig, str]] = []
            while pending:
                done, _ = await asyncio.wait(
                    set(pending), return_when=asyncio.FIRST_COMPLETED
                )
                for t in done:
                    a = pending.pop(t)
                    a, text = t.result()
                    answers.append((a, text))
                    transcript.append(
                        {"role": "assistant", "name": a.name, "content": text}
                    )
                    yield _meta({"event": "agent_message",
                                 "agent": a.name, "content": text})
            # Phase 2 — orchestrator combines everything and decides the
            # rendered components itself via a nova-response envelope.
            yield _meta({"event": "agent_start",
                         "agent": ORCHESTRATOR_NAME,
                         "text": f"{ORCHESTRATOR_NAME} is combining answers…"})
            answers_txt = "\n\n".join(
                f"--- {a.name} ---\n{text}" for a, text in answers
            )
            mem = astra_memory.recall_text(body.message, conv_id)
            synth = (
                f"The user asked:\n{body.message}\n\n"
                + (f"{mem}\n\n" if mem else "")
                + f"Recent context:\n{transcript_txt}\n\n"
                f"Each specialist answered independently:\n\n{answers_txt}\n\n"
                "Synthesize ONE final answer for the user: reconcile conflicts, "
                "deduplicate, keep the strongest points. Reply ONLY with the "
                "nova-response envelope defined in your instructions."
            )
            try:
                final = await run_agent(gw, orc, synth, [])
            except Exception as e:
                final = f"*(orchestrator error: {e})*"
            transcript.append(
                {"role": "assistant", "name": ORCHESTRATOR_NAME, "content": final}
            )
            if conv_id:
                astra_memory.store_short(conv_id, "assistant", final)
                asyncio.ensure_future(
                    astra_memory.extract_and_store(gw, body.message, final)
                )
            yield _meta({"event": "agent_message",
                         "agent": ORCHESTRATOR_NAME, "content": final})
            yield _meta({"event": "group_done", "text": "Done"})
            yield b"data: [DONE]\n\n"
        except Exception as e:
            yield _meta({"event": "error", "text": f"group error: {e}"})
            yield b"data: [DONE]\n\n"

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


# ---------- sharing ----------


@router.post("/share", status_code=201)
async def create_share(
    body: ShareRequest, session: AsyncSession = Depends(get_session)
) -> dict:
    conv = SharedConversation(
        id=secrets.token_urlsafe(9),
        title=(body.title or "Shared conversation")[:200],
        messages=body.messages,
    )
    session.add(conv)
    await session.commit()
    return {"id": conv.id, "url": f"/share/{conv.id}"}


# Public, no gateway key: the link IS the capability.
public_router = APIRouter(prefix="/v1")


@public_router.get("/share/{share_id}")
async def get_share(share_id: str, session: AsyncSession = Depends(get_session)):
    conv = (
        await session.execute(
            select(SharedConversation).where(SharedConversation.id == share_id)
        )
    ).scalar_one_or_none()
    if conv is None:
        return JSONResponse(
            status_code=404,
            content={"error": {"message": "shared conversation not found",
                               "type": "not_found"}},
        )
    return {"id": conv.id, "title": conv.title, "messages": conv.messages,
            "created_at": conv.created_at.isoformat() if conv.created_at else None}
