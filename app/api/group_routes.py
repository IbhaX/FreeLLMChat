"""Group chat (multi-agent) and conversation sharing endpoints."""

from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import AsyncIterator
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
    # Multi-agent orchestration modes:
    #   "parallel"   — agents answer independently, Orchestrator merges.
    #   "pipeline"   — sequential chain in agent_ids order; each stage gets
    #                  the previous agent's output; last output is final.
    #   "debate"     — parallel opening statements, one sequential rebuttal
    #                  round, Orchestrator delivers the verdict.
    #   "roundtable" — agents speak once in agent_ids order seeing the
    #                  running discussion; Orchestrator wraps up.
    #   "brainstorm" — divergent ideas in parallel; Orchestrator clusters
    #                  and ranks them into a board.
    mode: str = "parallel"


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
    conv_id = str(body.conversation_id) if body.conversation_id else None

    # Detached (refresh-resilient) execution: the run is registered as a
    # conversation chat-job, identical to /v1/research/stream and the chat
    # endpoint. Re-attach instead of duplicating work when the client
    # resubmits for an in-flight run.
    if conv_id:
        from app.services import chat_jobs

        running = chat_jobs.get_running(conv_id)
        if running is not None:
            return StreamingResponse(
                running.stream(), media_type="text/event-stream",
                headers={"Cache-Control": "no-cache"})
        # The job outlives the HTTP request → it needs its own session;
        # the pump closes it in `finally`.
        from app.db import get_session_factory

        session = get_session_factory()()

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
    from app.services.settings_store import get_custom_instructions

    custom = await get_custom_instructions(session)
    # astrapy calls are synchronous HTTP — never block the loop on them.
    asyncio.ensure_future(asyncio.to_thread(astra_memory.ensure_collections))
    if conv_id:
        asyncio.ensure_future(asyncio.to_thread(
            astra_memory.store_short, conv_id, "user", body.message))

    transcript: list[dict[str, Any]] = list(body.history or [])
    transcript.append({"role": "user", "name": "You", "content": body.message})
    # run_agent ignores `history` for tool-less agents, so context is
    # inlined into the prompt itself.
    transcript_txt = "\n".join(
        f"{h.get('name') or ('You' if h.get('role') == 'user' else 'Agent')}: {h.get('content', '')}"
        for h in transcript[-30:]
    )

    async def solo(a: AgentConfig, prompt: str | None = None) -> tuple[AgentConfig, str]:
        if prompt is None:
            prompt = (
                f"Conversation so far:\n{transcript_txt}\n\n"
                f"Answer the latest user message as {a.name}. Be thorough and well-structured: answer fully, cover the relevant substance, and use the component format for anything with more than one fact."
            )
        try:
            return a, await run_agent(gw, a, prompt, [], custom_instructions=custom,
                                  emit=emit)
        except Exception as e:
            return a, f"*(error: {e})*"

    async def orchestrate(prompt: str) -> str:
        try:
            return await run_agent(gw, orc, prompt, [], custom_instructions=custom,
                             emit=emit)
        except Exception as e:
            return f"*(orchestrator error: {e})*"

    evq: asyncio.Queue = asyncio.Queue()
    emit = evq.put_nowait

    async def gen():
        async def _live(task: asyncio.Task[Any]) -> AsyncIterator[bytes]:
            while not task.done():
                while not evq.empty():
                    yield _meta(evq.get_nowait())
                await asyncio.sleep(0.25)
            while not evq.empty():
                yield _meta(evq.get_nowait())

        try:
            names = ", ".join(a.name for a in agents)
            if body.mode == "pipeline":
                # Sequential chain: each agent's output becomes the next
                # agent's input; the last agent's output is the final answer
                # (no orchestrator — that is the difference from "parallel").
                yield _meta({"event": "group_start",
                             "text": f"Pipeline: {names}",
                             "agents": [a.name for a in agents]})
                prev_name, prev_out = "", ""
                for a in agents:
                    yield _meta({"event": "agent_start",
                                 "agent": a.name,
                                 "text": f"{a.name} is working on the previous stage's output…"})
                    if not prev_out:
                        prompt = (
                            f"Conversation so far:\n{transcript_txt}\n\n"
                            f"Answer the latest user message as {a.name}. "
                            "Be thorough and well-structured: answer fully, "
                            "cover the relevant substance, and use the "
                            "component format for anything with more than one "
                            "fact."
                        )
                    else:
                        prompt = (
                            f"The user's original request was:\n"
                            f"{body.message}\n\n"
                            f"The previous stage of the pipeline (agent "
                            f"\"{prev_name}\") produced this output:\n\n"
                            f"{prev_out}\n\n"
                            f"Take that output as your input and produce the "
                            f"next stage of the answer as {a.name}. "
                            "Keep what is correct, fix what is wrong, extend "
                            "it with your expertise. Use the component format "
                            "for anything with more than one fact."
                        )
                    try:
                        _t = asyncio.ensure_future(run_agent(
                            gw, a, prompt, [], custom_instructions=custom,
                            emit=emit))
                        async for _f in _live(_t):
                            yield _f
                        text = _t.result()
                    except Exception as e:
                        text = f"*(error: {e})*"
                    transcript.append(
                        {"role": "assistant", "name": a.name, "content": text})
                    yield _meta({"event": "agent_message",
                                 "agent": a.name, "content": text})
                    prev_name, prev_out = a.name, text
                if conv_id:
                    asyncio.ensure_future(asyncio.to_thread(
                        astra_memory.store_short, conv_id, "assistant", prev_out))
                    asyncio.ensure_future(
                        astra_memory.extract_and_store(gw, body.message, prev_out)
                    )
                yield _meta({"event": "group_done", "text": "Done"})
                yield b"data: [DONE]\n\n"
                return

            if body.mode == "debate":
                # Debate: parallel opening statements -> one rebuttal round
                # (each debater sees all prior turns) -> Orchestrator judges.
                yield _meta({"event": "group_start",
                             "text": f"Debate: {names} · judge {ORCHESTRATOR_NAME}",
                             "agents": [a.name for a in agents] + [ORCHESTRATOR_NAME]})
                for a in agents:
                    yield _meta({"event": "agent_start", "agent": a.name,
                                 "text": f"{a.name} is preparing an opening statement…"})
                open_prompts = {
                    a.id: (
                        f"Debate topic (the user's message):\n{body.message}\n\n"
                        f"Conversation so far:\n{transcript_txt}\n\n"
                        f"Give your opening statement as {a.name}: take a clear, "
                        "defensible position, argue it with reasoning and "
                        "evidence, and pre-empt the strongest objections. Use "
                        "the component format for anything with more than one "
                        "fact."
                    ) for a in agents}
                pending = {
                    asyncio.ensure_future(solo(a, open_prompts[a.id])): a
                    for a in agents}
                while pending:
                    done, _ = await asyncio.wait(
                        set(pending), return_when=asyncio.FIRST_COMPLETED,
                        timeout=0.5)
                    while not evq.empty():
                        yield _meta(evq.get_nowait())
                    for t in done:
                        pending.pop(t)
                        a, text = t.result()
                        transcript.append(
                            {"role": "assistant", "name": a.name,
                             "content": f"[opening] {text}"})
                        yield _meta({"event": "agent_message",
                                     "agent": a.name, "content": text})
                # Rebuttal round — sequential so each debater can answer the
                # latest arguments, not just the openings.
                for a in agents:
                    yield _meta({"event": "agent_start", "agent": a.name,
                                 "text": f"{a.name} is rebutting…"})
                    discussion = "\n".join(
                        f"{h.get('name') or h.get('role')}: {h.get('content', '')}"
                        for h in transcript[-30:])
                    _t = asyncio.ensure_future(solo(a, (
                        f"Debate topic (the user's message):\n{body.message}\n\n"
                        f"Debate so far (openings and rebuttals):\n{discussion}\n\n"
                        f"As {a.name}, defend your position and rebut the "
                        "strongest opposing arguments. Concede points you "
                        "cannot defend. Use the component format for anything "
                        "with more than one fact.")))
                    async for _f in _live(_t):
                        yield _f
                    _, text = _t.result()
                    transcript.append(
                        {"role": "assistant", "name": a.name,
                         "content": f"[rebuttal] {text}"})
                    yield _meta({"event": "agent_message",
                                 "agent": a.name, "content": text})
                yield _meta({"event": "agent_start", "agent": ORCHESTRATOR_NAME,
                             "text": f"{ORCHESTRATOR_NAME} is judging the debate…"})
                discussion = "\n".join(
                    f"{h.get('name') or h.get('role')}: {h.get('content', '')}"
                    for h in transcript[-30:])
                _t = asyncio.ensure_future(orchestrate(
                    f"You are judging a debate. Topic (the user's message):\n"
                    f"{body.message}\n\nDebate transcript:\n{discussion}\n\n"
                    "Deliver the verdict: which positions were strongest and "
                    "why, where the debaters agreed, what remained unresolved, "
                    "and a balanced conclusion answering the user's message. "
                    "Reply ONLY with the nova-response envelope defined in "
                    "your instructions."))
                async for _f in _live(_t):
                    yield _f
                final = _t.result()
                transcript.append({"role": "assistant", "name": ORCHESTRATOR_NAME,
                                   "content": final})
                if conv_id:
                    asyncio.ensure_future(asyncio.to_thread(
                        astra_memory.store_short, conv_id, "assistant", final))
                    asyncio.ensure_future(
                        astra_memory.extract_and_store(gw, body.message, final))
                yield _meta({"event": "agent_message",
                             "agent": ORCHESTRATOR_NAME, "content": final})
                yield _meta({"event": "group_done", "text": "Done"})
                yield b"data: [DONE]\n\n"
                return

            if body.mode == "roundtable":
                # Roundtable: agents speak one at a time in pick order, each
                # sees the running discussion; Orchestrator moderates a wrap-up.
                yield _meta({"event": "group_start",
                             "text": f"Roundtable: {names} · moderated by {ORCHESTRATOR_NAME}",
                             "agents": [a.name for a in agents] + [ORCHESTRATOR_NAME]})
                for a in agents:
                    yield _meta({"event": "agent_start", "agent": a.name,
                                 "text": f"{a.name} is speaking…"})
                    discussion = "\n".join(
                        f"{h.get('name') or h.get('role')}: {h.get('content', '')}"
                        for h in transcript[-30:])
                    _t = asyncio.ensure_future(solo(a, (
                        f"Roundtable discussion on the user's message:\n"
                        f"{body.message}\n\n"
                        f"Discussion so far:\n{discussion}\n\n"
                        f"Speak next as {a.name}: give your take on the topic "
                        "and respond to your colleagues' points — agree, push "
                        "back, or add what they missed. Use the component "
                        "format for anything with more than one fact.")))
                    async for _f in _live(_t):
                        yield _f
                    _, text = _t.result()
                    transcript.append(
                        {"role": "assistant", "name": a.name, "content": text})
                    yield _meta({"event": "agent_message",
                                 "agent": a.name, "content": text})
                yield _meta({"event": "agent_start", "agent": ORCHESTRATOR_NAME,
                             "text": f"{ORCHESTRATOR_NAME} is wrapping up…"})
                discussion = "\n".join(
                    f"{h.get('name') or h.get('role')}: {h.get('content', '')}"
                    for h in transcript[-30:])
                _t = asyncio.ensure_future(orchestrate(
                    f"You moderated a roundtable on the user's message:\n"
                    f"{body.message}\n\nDiscussion transcript:\n{discussion}\n\n"
                    "Wrap up the roundtable: consensus points, remaining "
                    "disagreements, and the concrete takeaways that answer the "
                    "user's message. Reply ONLY with the nova-response "
                    "envelope defined in your instructions."))
                async for _f in _live(_t):
                    yield _f
                final = _t.result()
                transcript.append({"role": "assistant", "name": ORCHESTRATOR_NAME,
                                   "content": final})
                if conv_id:
                    asyncio.ensure_future(asyncio.to_thread(
                        astra_memory.store_short, conv_id, "assistant", final))
                    asyncio.ensure_future(
                        astra_memory.extract_and_store(gw, body.message, final))
                yield _meta({"event": "agent_message",
                             "agent": ORCHESTRATOR_NAME, "content": final})
                yield _meta({"event": "group_done", "text": "Done"})
                yield b"data: [DONE]\n\n"
                return

            if body.mode == "brainstorm":
                # Brainstorm: divergent idea generation in parallel, then the
                # Orchestrator clusters/ranks everything into an idea board.
                yield _meta({"event": "group_start",
                             "text": f"Brainstorm: {names} · curated by {ORCHESTRATOR_NAME}",
                             "agents": [a.name for a in agents] + [ORCHESTRATOR_NAME]})
                for a in agents:
                    yield _meta({"event": "agent_start", "agent": a.name,
                                 "text": f"{a.name} is brainstorming…"})
                bs_prompts = {
                    a.id: (
                        f"Brainstorm on the user's message:\n{body.message}\n\n"
                        f"Conversation so far:\n{transcript_txt}\n\n"
                        f"As {a.name}, generate your boldest and most varied "
                        "ideas from your expertise — quantity and novelty over "
                        "polish; no idea is too wild at this stage. Use the "
                        "component format for anything with more than one "
                        "fact."
                    ) for a in agents}
                pending = {
                    asyncio.ensure_future(solo(a, bs_prompts[a.id])): a
                    for a in agents}
                while pending:
                    done, _ = await asyncio.wait(
                        set(pending), return_when=asyncio.FIRST_COMPLETED,
                        timeout=0.5)
                    while not evq.empty():
                        yield _meta(evq.get_nowait())
                    for t in done:
                        pending.pop(t)
                        a, text = t.result()
                        transcript.append(
                            {"role": "assistant", "name": a.name, "content": text})
                        yield _meta({"event": "agent_message",
                                     "agent": a.name, "content": text})
                yield _meta({"event": "agent_start", "agent": ORCHESTRATOR_NAME,
                             "text": f"{ORCHESTRATOR_NAME} is organizing the ideas…"})
                ideas = "\n\n".join(
                    f"--- {h['name']} ---\n{h['content']}"
                    for h in transcript
                    if h.get("role") == "assistant" and h.get("name"))
                _t = asyncio.ensure_future(orchestrate(
                    f"Brainstorm on the user's message:\n{body.message}\n\n"
                    f"Raw ideas from every participant:\n\n{ideas}\n\n"
                    "Curate the board: cluster related ideas, deduplicate, "
                    "rank by impact/feasibility, and mark the top picks. "
                    "Reply ONLY with the nova-response envelope defined in "
                    "your instructions."))
                async for _f in _live(_t):
                    yield _f
                final = _t.result()
                transcript.append({"role": "assistant", "name": ORCHESTRATOR_NAME,
                                   "content": final})
                if conv_id:
                    asyncio.ensure_future(asyncio.to_thread(
                        astra_memory.store_short, conv_id, "assistant", final))
                    asyncio.ensure_future(
                        astra_memory.extract_and_store(gw, body.message, final))
                yield _meta({"event": "agent_message",
                             "agent": ORCHESTRATOR_NAME, "content": final})
                yield _meta({"event": "group_done", "text": "Done"})
                yield b"data: [DONE]\n\n"
                return

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
                    set(pending), return_when=asyncio.FIRST_COMPLETED,
                    timeout=0.5
                )
                while not evq.empty():
                    yield _meta(evq.get_nowait())
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
            mem = await asyncio.to_thread(
                astra_memory.recall_text, body.message, conv_id)
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
                _t = asyncio.ensure_future(run_agent(
                    gw, orc, synth, [], custom_instructions=custom, emit=emit))
                async for _f in _live(_t):
                    yield _f
                final = _t.result()
            except Exception as e:
                final = f"*(orchestrator error: {e})*"
            transcript.append(
                {"role": "assistant", "name": ORCHESTRATOR_NAME, "content": final}
            )
            if conv_id:
                asyncio.ensure_future(asyncio.to_thread(
                        astra_memory.store_short, conv_id, "assistant", final))
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

    if conv_id:
        from app.services import chat_jobs

        # Detached job owns `session` — the pump closes it in `finally`.
        job = chat_jobs.start(conv_id, gen(), session)
        return StreamingResponse(
            job.stream(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"})
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
