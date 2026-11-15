"""OpenAI-compatible /v1 endpoints."""

from __future__ import annotations

from typing import Any
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_gateway_key
from app.db import get_session
from app.gateway import Gateway, GatewayExhausted
from app.logging_config import new_request_id
from app.models import ApiKey
from app.response_protocol import protocol_descriptor, structured_system_prompt
from app.schemas import ChatCompletionRequest

router = APIRouter(prefix="/v1", dependencies=[Depends(require_gateway_key)])


def _gateway(request: Request) -> Gateway:
    return request.app.state.gateway


def _model_pricing(provider: Any, model: str) -> dict[str, Any] | None:
    """Per-model pricing: provider.pricing base + optional model_overrides."""
    base = dict(provider.pricing or {})
    overrides = base.pop("model_overrides", None) or {}
    base.update(overrides.get(model) or {})
    return base or None


async def _keyed_provider_ids(session: AsyncSession) -> set[int]:
    """Providers with ≥1 enabled key."""
    rows = (
        await session.execute(
            select(ApiKey.provider_id).where(ApiKey.enabled.is_(True)).distinct()
        )
    ).scalars().all()
    return set(rows)


async def _servable_provider_ids(session: AsyncSession) -> set[int]:
    """Providers with ≥1 enabled key that is not currently on cooldown —
    i.e. can actually serve a request right now."""
    now = datetime.now(timezone.utc)
    rows = (
        await session.execute(
            select(ApiKey.provider_id, ApiKey.cooldown_until).where(
                ApiKey.enabled.is_(True)
            )
        )
    ).all()
    out: set[int] = set()
    for provider_id, cooldown in rows:
        if cooldown is None:
            out.add(provider_id)
            continue
        cd = cooldown.replace(tzinfo=cooldown.tzinfo or timezone.utc)
        if cd <= now:
            out.add(provider_id)
    return out


def _truthy(v: Any) -> bool:
    return str(v or "").lower() in ("1", "true", "yes", "on")


@router.get("/models")
async def list_models(
    request: Request, session: AsyncSession = Depends(get_session),
    usable: str | None = None,
) -> dict:
    """Model catalog. ``usable=1`` keeps only models whose provider can serve
    right now (≥1 enabled key, not on cooldown), plus the gateway aliases."""
    gateway = _gateway(request)
    catalog = await gateway.provider_catalog(session)
    keyed_ids = await _keyed_provider_ids(session)
    servable_ids = await _servable_provider_ids(session)
    only_usable = _truthy(usable)
    data = []
    for pname, (p, models) in catalog.items():
        servable = p.id in servable_ids
        if only_usable and not servable:
            continue
        for m in models:
            data.append({
                "id": f"{pname}/{m}", "object": "model", "created": 0,
                "owned_by": pname, "usable": servable,
                "keyed": p.id in keyed_ids,
                "pricing": _model_pricing(p, m),
            })
    data += [
        {"id": alias, "object": "model", "created": 0, "owned_by": "gateway",
         "usable": True, "keyed": True, "pricing": None}
        for alias in ("auto", "auto:coding", "auto:fast")
    ]
    return {"object": "list", "data": data}


@router.get("/render-protocol")
async def render_protocol() -> dict:
    """Self-describing structured response protocol contract."""

    return protocol_descriptor()


@router.get("/render-protocol/prompt")
async def render_protocol_prompt() -> dict:
    """The structured-output system prompt the frontend injects by default.

    Exposed so the chat UI can fetch it once at load and append it to every
    request, ensuring responses are always structured envelopes.
    """

    return {"prompt": structured_system_prompt()}


@router.get("/models/{model_id:path}")
async def get_model(
    model_id: str, request: Request, session: AsyncSession = Depends(get_session)
) -> dict:
    gateway = _gateway(request)
    catalog = await gateway.provider_catalog(session)
    keyed_ids = await _keyed_provider_ids(session)
    servable_ids = await _servable_provider_ids(session)
    mid = model_id.lstrip("/")
    if mid in ("auto", "auto:coding", "auto:fast"):
        return {"id": mid, "object": "model", "created": 0, "owned_by": "gateway",
                "usable": True, "keyed": True, "pricing": None}
    for pname, (p, models) in catalog.items():
        for m in models:
            if f"{pname}/{m}" == mid or m == mid:
                return {"id": f"{pname}/{m}", "object": "model", "created": 0,
                        "owned_by": pname, "usable": p.id in servable_ids,
                        "keyed": p.id in keyed_ids,
                        "pricing": _model_pricing(p, m)}
    return JSONResponse(
        status_code=404,
        content={"error": {"message": f"model '{mid}' not found", "type": "not_found"}},
    )


@router.post("/embeddings")
async def embeddings(
    body: dict, request: Request, session: AsyncSession = Depends(get_session)
):
    """Passthrough to a provider that serves the embedding model."""
    from app.crypto import decrypt_key
    from app.providers.registry import build_adapter

    gateway = _gateway(request)
    model = body.get("model", "")
    candidates = await gateway._attempts(session, model)
    for provider, pmodel in candidates:
        key = await gateway.rotator.pick(session, provider)
        if key is None:
            continue
        adapter = build_adapter(provider)
        try:
            raw = decrypt_key(key.encrypted_key)
            payload = dict(body)
            payload["model"] = pmodel
            result = await adapter.post_json(
                gateway.client, raw, "/embeddings", payload
            )
            return JSONResponse(
                content=result,
                headers={"X-Gateway-Provider": provider.name},
            )
        except Exception:
            continue
    return JSONResponse(
        status_code=503,
        content={"error": {"message": "no provider serves this embedding model",
                           "type": "gateway_exhausted"}},
    )


@router.get("/experts")
async def list_experts():
    """Expert agent registry — the specialists the orchestrator delegates to."""
    from app.services.experts import all_experts

    return {
        "experts": [
            {
                "name": e.name,
                "title": e.title,
                "domain": e.domain,
                "summary": e.summary,
                "capabilities": list(e.capabilities),
                "tools": list(e.tools),
                "skills": list(e.skills),
                "model_hint": e.model_hint,
            }
            for e in all_experts()
        ]
    }


@router.get("/skills")
async def list_skills():
    """Skill registry — reusable capability modules agents can attach."""
    from app.services.skills import all_skills

    return {
        "skills": [
            {
                "name": s.name,
                "version": s.version,
                "description": s.description,
                "requires_tools": list(s.requires_tools),
                "domains": list(s.domains),
            }
            for s in all_skills()
        ]
    }


class OrchestrateRequest(BaseModel):
    message: str
    history: list[dict[str, Any]] | None = None
    model: str = "auto"


@router.post("/orchestrate")
async def orchestrate_endpoint(
    body: OrchestrateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Run the multi-agent orchestrator for a message. Non-streaming.

    Returns the final answer plus the execution trace, step results and
    degradation flags — always a controlled terminal state, never 5xx
    for pipeline failures.
    """
    from app.services.orchestrator import run_orchestrated

    gateway = _gateway(request)
    events: list[dict] = []
    res = await run_orchestrated(
        gateway, session, body.message,
        history=body.history or [],
        emit=events.append,
    )
    return {
        "answer": res.answer,
        "intent": res.intent,
        "strategy": res.strategy,
        "degraded": res.degraded,
        "model": res.model,
        "steps": [
            {"step_id": s.step_id, "expert": s.expert, "ok": s.ok,
             "duration_ms": s.duration_ms, "attempts": s.attempts,
             "error": s.error}
            for s in res.steps
        ],
        "trace": res.trace,
        "meta": res.meta,
    }


_REPLAY_DELAY_S = 0.012
_REPLAY_MAX_PIECES = 160


def _stream_pieces(text: str) -> list[str]:
    """Split a finished reply into ~token-sized deltas, capped so a long
    answer replays in about two seconds."""
    if not text:
        return []
    size = max(8, -(-len(text) // _REPLAY_MAX_PIECES))
    return [text[i:i + size] for i in range(0, len(text), size)]


def _remember(gateway, conv_id: str | None, user_text: str, reply: str) -> None:
    """Fire-and-forget: store the turn in short-term memory and extract facts."""
    if not conv_id:
        return
    import asyncio

    from app.services import astra_memory

    from app.services.tools import _sanitize_final_text

    reply = _sanitize_final_text(reply) or ""

    async def _bg() -> None:
        astra_memory.store_short(str(conv_id), "assistant", reply)
        await astra_memory.extract_and_store(gateway, user_text, reply)

    asyncio.create_task(_bg())


@router.post("/chat/completions")
async def chat_completions(
    body: ChatCompletionRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    new_request_id()
    gateway = _gateway(request)
    payload = body.model_dump(exclude_none=True)
    # Cap conversation history: keep all system messages plus the most
    # recent turns — unbounded histories grow input tokens every call.
    msgs = payload.get("messages")
    if isinstance(msgs, list):
        system_msgs = [m for m in msgs if m.get("role") == "system"]
        turns = [m for m in msgs if m.get("role") != "system"]
        if len(turns) > 20:
            payload["messages"] = system_msgs + turns[-20:]
    # Inject the structured-output system prompt so responses are always
    # structured envelopes (unless the caller explicitly opts out via the
    # `nova_structured: false` extra field).
    if payload.get("nova_structured", True) and isinstance(payload.get("messages"), list):
        from app.response_protocol import structured_system_prompt

        sys_idx = next(
            (i for i, m in enumerate(payload["messages"])
             if m.get("role") == "system"),
            None,
        )
        struct_prompt = structured_system_prompt()
        if sys_idx is not None:
            # Component contract goes FIRST so provider-agnostic output rules
            # are not diluted by persona/agent instructions.
            payload["messages"][sys_idx] = dict(
                payload["messages"][sys_idx],
                content=f"{struct_prompt}\n\n{payload['messages'][sys_idx].get('content', '')}",
            )
        else:
            payload["messages"].insert(0, {"role": "system", "content": struct_prompt})
    last_user = next(
        (m.get("content", "") for m in reversed(payload.get("messages") or [])
         if m.get("role") == "user" and isinstance(m.get("content"), str)),
        "",
    )
    conv_id = payload.pop("conversation_id", None)
    # A detached job may already be answering this conversation (the
    # client refreshed or resent) — re-attach instead of duplicating work.
    if body.stream and conv_id:
        from app.services import chat_jobs

        running = chat_jobs.get_running(str(conv_id))
        if running is not None:
            return StreamingResponse(
                running.stream(), media_type="text/event-stream",
                headers={"Cache-Control": "no-cache"})
    # Usage metering + plan quota (single-tenant mode => 'anon'/free).
    from app.api.deps import optional_user, subject_for
    from app.services import usage as _usage
    _user = await optional_user(request)
    ok, reason = await _usage.check_quota(
        session, subject_for(_user), "chat", _user.plan if _user else "free")
    if not ok:
        raise HTTPException(status_code=429, detail=reason)
    await _usage.record(session, subject_for(_user), "chat",
                        model=str(payload.get("model") or ""))
    try:
        from app.services import astra_memory
        from app.services.agents import get_memory_text

        astra_memory.ensure_collections()
        if last_user and conv_id:
            astra_memory.store_short(str(conv_id), "user", last_user)
        mem = await get_memory_text(last_user, conv_id and str(conv_id))
        if mem and isinstance(payload.get("messages"), list):
            # Append memory alongside any existing system prompt; never replace it.
            sys_idx = next(
                (i for i, m in enumerate(payload["messages"])
                 if m.get("role") == "system"),
                None,
            )
            if sys_idx is not None:
                payload["messages"][sys_idx] = dict(
                    payload["messages"][sys_idx],
                    content=f"{payload['messages'][sys_idx].get('content', '')}\n\n{mem}",
                )
            else:
                payload["messages"].insert(0, {"role": "system", "content": mem})
    except Exception:
        pass
    # User-defined custom instructions (Settings page) — appended to the
    # system prompt so they shape every chat answer. Never replaces the
    # component contract; failure to load leaves chat unaffected.
    try:
        from app.models import Setting

        row = (
            await session.execute(
                select(Setting).where(Setting.key == "custom_instructions")
            )
        ).scalar_one_or_none()
        custom = (row.value if row else "").strip()
        if custom and isinstance(payload.get("messages"), list):
            sys_idx = next(
                (i for i, m in enumerate(payload["messages"])
                 if m.get("role") == "system"),
                None,
            )
            block = f"## USER CUSTOM INSTRUCTIONS\n{custom}"
            if sys_idx is not None:
                payload["messages"][sys_idx] = dict(
                    payload["messages"][sys_idx],
                    content=f"{payload['messages'][sys_idx].get('content', '')}\n\n{block}",
                )
            else:
                payload["messages"].insert(0, {"role": "system", "content": block})
    except Exception:
        pass
    # One-line reminder as the LAST message for weaker models (the full
    # contract is at the start of the system prompt; this keeps components
    # salient at the point of generation).
    if payload.get("nova_structured", True) and isinstance(payload.get("messages"), list):
        from app.services.component_enforce import COMPONENT_REMINDER

        payload["messages"] = list(payload["messages"]) + [
            {"role": "system", "content": COMPONENT_REMINDER}
        ]
    if body.stream:
        import asyncio
        import json as _json

        # Conversation-scoped requests run detached from this HTTP
        # connection (refresh-resilient): they need their own session —
        # the request-scoped one is closed when the response ends.
        if conv_id:
            from app.db import get_session_factory

            session = get_session_factory()()

        events: asyncio.Queue[dict] = asyncio.Queue()
        # `builtin_tools` (extra field): run the server-side tool-call loop
        # over the gateway's built-in tools instead of plain streaming.
        builtin = payload.pop("builtin_tools", None)
        # Orchestrator: one routing step decides direct / tool-loop /
        # deep-research. Greetings skip tools; deep multi-source questions
        # fan out to the research pipeline.
        route = "direct"
        if builtin:
            try:
                from app.services.orchestrator import route_message

                route = await route_message(
                    gateway, session,
                    {**payload, "builtin_tools": True},
                    last_user=last_user,
                    history=payload.get("messages") or [],
                )
            except Exception:
                route = "tools"

        async def _open():
            try:
                return await gateway.chat_stream(
                    session, payload, on_event=events.put_nowait
                )
            except Exception as e:  # surfaced as an SSE error below
                return e

        async def _tool_run():
            from app.services.tools import run_tool_loop

            try:
                return await run_tool_loop(
                    gateway, session, payload, builtin,
                    emit=events.put_nowait, tool_policy=True,
                )
            except Exception as e:
                return e

        async def _research_run():
            from app.services.research import run_research

            try:
                return await run_research(
                    gateway, session, payload.get("messages") or [],
                    model=str(payload.get("model") or "auto"),
                    conversation_id=conv_id,
                    on_event=events.put_nowait,
                )
            except Exception as e:
                return e

        async def _orch_run():
            from app.services.orchestrator import run_orchestrated

            try:
                return await run_orchestrated(
                    gateway, session, last_user or "",
                    payload=payload,
                    history=payload.get("messages") or [],
                    emit=events.put_nowait,
                )
            except Exception as e:
                return e

        if route == "research":
            task = asyncio.create_task(_research_run())
        elif route == "orchestrate":
            task = asyncio.create_task(_orch_run())
        elif route == "tools":
            task = asyncio.create_task(_tool_run())
        else:
            task = asyncio.create_task(_open())

        def _meta(ev: dict) -> bytes:
            chunk = {
                "id": "freellm-activity",
                "object": "chat.completion.chunk",
                "choices": [],
                "freellm": ev,
            }
            return f"data: {_json.dumps(chunk)}\n\n".encode()

        async def gen():
            while not (task.done() and events.empty()):
                try:
                    ev = await asyncio.wait_for(events.get(), timeout=0.1)
                except (asyncio.TimeoutError, TimeoutError):
                    continue
                yield _meta(ev)
            res = task.result()
            if route == "orchestrate":
                from app.services.reliable import last_resort_answer

                if isinstance(res, Exception) or res is None:
                    answer = last_resort_answer(
                        last_user, failures=[str(res)[:200]])
                    trace: list[dict] = []
                else:
                    answer = getattr(res, "answer", "") or last_resort_answer(
                        last_user,
                        failures=[str(f) for f in
                                  getattr(res, "meta", {}).get("failures", [])])
                    trace = getattr(res, "trace", [])
                yield _meta({"event": "orchestration_done",
                             "text": f"Orchestration complete "
                                     f"({getattr(res, 'intent', '?')})",
                             "trace": trace,
                             "steps": [{"expert": s.expert, "ok": s.ok}
                                       for s in getattr(res, "steps", [])]})
                if last_user:
                    _remember(gateway, conv_id, last_user, answer)
                for piece in _stream_pieces(answer):
                    chunk = {"choices": [{"index": 0, "delta": {"content": piece}}]}
                    yield f"data: {_json.dumps(chunk)}\n\n".encode()
                    await asyncio.sleep(_REPLAY_DELAY_S)
                chunk = {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
                yield f"data: {_json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()
                return
            if route == "research":
                if isinstance(res, Exception) or res is None:
                    from app.services.reliable import last_resort_answer

                    answer = last_resort_answer(
                        last_user, failures=[str(res)[:200]])
                    yield _meta({"event": "research_done",
                                 "text": "Research failed — partial answer"})
                    for piece in _stream_pieces(answer):
                        chunk = {"choices": [{"index": 0, "delta": {"content": piece}}]}
                        yield f"data: {_json.dumps(chunk)}\n\n".encode()
                        await asyncio.sleep(_REPLAY_DELAY_S)
                    chunk = {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
                    yield f"data: {_json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()
                    return
                answer = getattr(res, "answer", None) or ""
                if payload.get("nova_structured", True) and answer:
                    from app.services.component_enforce import enforce_components
                    from app.services.tools import _sanitize_final_text

                    clean = _sanitize_final_text(answer) or answer
                    fake = {"choices": [{"message": {"role": "assistant", "content": clean}}]}
                    fake, _m = await enforce_components(
                        gateway, session, payload, fake,
                        model=str(getattr(res, "model", "") or ""),
                    )
                    answer = fake["choices"][0]["message"]["content"]
                if last_user:
                    _remember(gateway, conv_id, last_user, answer)
                yield _meta({"event": "research_done",
                             "text": f"Research complete ({getattr(res, 'depth', '?')})"})
                for piece in _stream_pieces(answer):
                    chunk = {"choices": [{"index": 0, "delta": {"content": piece}}]}
                    yield f"data: {_json.dumps(chunk)}\n\n".encode()
                    await asyncio.sleep(_REPLAY_DELAY_S)
                chunk = {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
                yield f"data: {_json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()
                return
            if route == "tools":
                if isinstance(res, Exception) or not isinstance(res, dict):
                    from app.services.reliable import last_resort_answer

                    answer = last_resort_answer(
                        last_user, failures=[str(res)[:200]])
                    yield _meta({"event": "warn",
                                 "text": "Tool run failed — partial answer"})
                    for piece in _stream_pieces(answer):
                        chunk = {"choices": [{"index": 0, "delta": {"content": piece}}]}
                        yield f"data: {_json.dumps(chunk)}\n\n".encode()
                        await asyncio.sleep(_REPLAY_DELAY_S)
                    chunk = {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
                    yield f"data: {_json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()
                    return
                if payload.get("nova_structured", True):
                    from app.services.component_enforce import enforce_components

                    res, _comp_metrics = await enforce_components(
                        gateway, session, payload, res,
                        model=str(res.get("model") or ""),
                    )
                text = ""
                for c in res.get("choices", []):
                    text += (c.get("message") or {}).get("content") or ""
                if last_user:
                    _remember(gateway, conv_id, last_user, text)
                # The tool loop returns the answer in one piece; replay it in
                # small deltas so the UI can render progressively.
                for piece in _stream_pieces(text):
                    chunk = {"choices": [{"index": 0, "delta": {"content": piece}}]}
                    yield f"data: {_json.dumps(chunk)}\n\n".encode()
                    await asyncio.sleep(_REPLAY_DELAY_S)
                chunk = {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
                yield f"data: {_json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()
                return
            if not isinstance(res, tuple):
                err = {"error": {"message": str(res), "type": "gateway_exhausted", "code": 503}}
                yield f"data: {_json.dumps(err)}\n\ndata: [DONE]\n\n".encode()
                return
            stream, provider, key, model = res
            yield _meta({
                "event": "connected",
                "text": f"Connected via {provider.name} · {key.label} · {model}",
                "provider": provider.name,
                "key_label": key.label,
                "model": model,
            })
            reply = ""
            structured = payload.get("nova_structured", True)
            async for chunk in stream:
                try:
                    for part in chunk.split(b"data: "):
                        part = part.strip()
                        if not part or part == b"[DONE]":
                            continue
                        for ch in _json.loads(part).get("choices", []):
                            reply += (ch.get("delta") or {}).get("content") or ""
                except Exception:
                    pass
                if structured:
                    # Hold [DONE] back: enforcement may replace the reply.
                    chunk = chunk.replace(b"data: [DONE]\n\n", b"").replace(b"data: [DONE]", b"")
                    if not chunk.strip():
                        continue
                yield chunk
                # Meta events (e.g. per-call usage) emitted while streaming —
                # the main drain loop above already exited once `task` was done.
                while not events.empty():
                    yield _meta(events.get_nowait())
            while not events.empty():
                yield _meta(events.get_nowait())
            if structured:
                # Tokens were already shown, so enforcement here swaps the
                # finished message via a replace_content event.
                from app.services.component_enforce import enforce_components
                from app.services.tools import _sanitize_final_text

                clean = _sanitize_final_text(reply) or reply
                fake = {"choices": [{"message": {"role": "assistant", "content": clean}}]}
                fake, _m = await enforce_components(
                    gateway, session, payload, fake,
                    provider=provider.name, model=model,
                )
                fixed = fake["choices"][0]["message"]["content"]
                if fixed != reply:
                    reply = fixed
                    yield _meta({"event": "replace_content", "content": fixed})
                yield b"data: [DONE]\n\n"
            if last_user:
                _remember(gateway, conv_id, last_user, reply)

        if conv_id:
            from app.services import chat_jobs

            job = chat_jobs.start(str(conv_id), gen(), session)
            return StreamingResponse(
                job.stream(), media_type="text/event-stream",
                headers={"Cache-Control": "no-cache"})
        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )
    try:
        result, provider, key, model = await gateway.chat(session, payload)
        if payload.get("nova_structured", True):
            from app.services.component_enforce import enforce_components

            result, _comp_metrics = await enforce_components(
                gateway, session, payload, result,
                provider=provider.name, model=model,
            )
        if last_user:
            reply = ""
            for c in (result or {}).get("choices", []):
                reply += ((c.get("message") or {}).get("content")) or ""
            _remember(gateway, conv_id, last_user, reply)
    except GatewayExhausted as e:
        return JSONResponse(
            status_code=e.last_status or 503,
            content={"error": {"message": str(e),
                               "type": "gateway_exhausted",
                               "code": e.last_status or 503}},
        )
    return JSONResponse(
        content=result,
        headers={
            "X-Gateway-Provider": provider.name,
            "X-Gateway-Key-Label": key.label,
            "X-Gateway-Model": model,
        },
    )


# ---------- detached generation (refresh-resilient chat) ----------

@router.get("/conversations/{client_id}/live")
async def conversation_live(client_id: str) -> dict[str, Any]:
    """Job status for a conversation's detached generation."""
    from app.services import chat_jobs

    return chat_jobs.status(chat_jobs.get(client_id))


@router.get("/conversations/{client_id}/live/stream")
async def conversation_live_stream(client_id: str) -> StreamingResponse:
    """Re-attach to a detached generation: replays buffered SSE frames
    then tails live, ending with [DONE]."""
    from app.services import chat_jobs

    job = chat_jobs.get(client_id)

    async def _empty() -> Any:
        yield b"data: [DONE]\n\n"

    return StreamingResponse(
        job.stream() if job else _empty(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@router.delete("/conversations/{client_id}/live")
async def conversation_live_cancel(client_id: str) -> dict[str, bool]:
    """Cancel the running detached job (Stop button / user abort)."""
    from app.services import chat_jobs

    job = chat_jobs.get(client_id)
    if job is not None:
        job.cancel()
    return {"ok": True}
