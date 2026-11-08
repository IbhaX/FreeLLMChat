"""OpenAI-compatible /v1 endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_gateway_key
from app.db import get_session
from app.gateway import Gateway, GatewayExhausted
from app.logging_config import new_request_id
from app.response_protocol import protocol_descriptor, structured_system_prompt
from app.schemas import ChatCompletionRequest

router = APIRouter(prefix="/v1", dependencies=[Depends(require_gateway_key)])


def _gateway(request: Request) -> Gateway:
    return request.app.state.gateway


@router.get("/models")
async def list_models(
    request: Request, session: AsyncSession = Depends(get_session)
) -> dict:
    gateway = _gateway(request)
    catalog = await gateway.provider_catalog(session)
    data = [
        {"id": f"{pname}/{m}", "object": "model", "created": 0,
         "owned_by": pname}
        for pname, (_, models) in catalog.items()
        for m in models
    ]
    data += [
        {"id": alias, "object": "model", "created": 0, "owned_by": "gateway"}
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
    mid = model_id.lstrip("/")
    if mid in ("auto", "auto:coding", "auto:fast"):
        return {"id": mid, "object": "model", "created": 0, "owned_by": "gateway"}
    for pname, (_, models) in catalog.items():
        for m in models:
            if f"{pname}/{m}" == mid or m == mid:
                return {"id": f"{pname}/{m}", "object": "model", "created": 0,
                        "owned_by": pname}
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


@router.post("/chat/completions")
async def chat_completions(
    body: ChatCompletionRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    new_request_id()
    gateway = _gateway(request)
    payload = body.model_dump(exclude_none=True)
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
            payload["messages"][sys_idx] = dict(
                payload["messages"][sys_idx],
                content=f"{payload['messages'][sys_idx].get('content', '')}\n\n{struct_prompt}",
            )
        else:
            payload["messages"].insert(0, {"role": "system", "content": struct_prompt})
    try:
        from app.services.agents import get_memory_text

        mem = await get_memory_text()
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
    if body.stream:
        import asyncio
        import json as _json

        events: asyncio.Queue[dict] = asyncio.Queue()

        async def _open():
            try:
                return await gateway.chat_stream(
                    session, payload, on_event=events.put_nowait
                )
            except Exception as e:  # surfaced as an SSE error below
                return e

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
            async for chunk in stream:
                yield chunk

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )
    try:
        result, provider, key, model = await gateway.chat(session, payload)
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
