"""Group chat (multi-agent) and conversation sharing endpoints."""

from __future__ import annotations

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


class GroupChatRequest(BaseModel):
    message: str
    history: list[dict[str, Any]] = Field(default_factory=list)
    agent_ids: list[int]
    rounds: int = 2  # autonomous back-and-forth rounds after the user speaks


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
    """Run a group chat turn: each member agent replies in order, then the
    group keeps discussing autonomously for `rounds` extra rounds (capped 5).

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
    rounds = max(1, min(int(body.rounds or 1), 5))

    async def gen():
        try:
            transcript: list[dict[str, Any]] = list(body.history or [])
            transcript.append({"role": "user", "name": "You", "content": body.message})
            names = ", ".join(a.name for a in agents)
            yield _meta({"event": "group_start",
                         "text": f"Group chat: {names}",
                         "agents": [a.name for a in agents]})
            for rnd in range(rounds):
                for a in agents:
                    yield _meta({"event": "agent_start",
                                 "agent": a.name,
                                 "text": f"{a.name} is thinking…"})
                    others = ", ".join(x.name for x in agents if x.id != a.id) or "the user"
                    # run_agent ignores `history` for tool-less agents, so the
                    # transcript is inlined in the prompt itself.
                    transcript_txt = "\n".join(
                        f"{h.get('name') or ('You' if h.get('role') == 'user' else 'Agent')}: {h.get('content', '')}"
                        for h in transcript[-30:]
                    )
                    prompt = (
                        f"You are {a.name}, one participant in a group chat with "
                        f"{others} and the user.\n\n"
                        f"Transcript so far:\n{transcript_txt}\n\n"
                        f"Speak next as {a.name}: respond to the latest points, "
                        f"be concise (1-4 sentences), and don't repeat what "
                        f"others already said."
                    )
                    try:
                        text = await run_agent(gw, a, prompt, [])
                    except Exception as e:
                        text = f"*(error: {e})*"
                    transcript.append(
                        {"role": "assistant", "name": a.name, "content": text}
                    )
                    yield _meta({"event": "agent_message",
                                 "agent": a.name, "content": text})
            yield _meta({"event": "group_done", "text": "Group round complete"})
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
