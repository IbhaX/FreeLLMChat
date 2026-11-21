"""PydanticAI ``Model`` adapter over the Nova gateway.

Every pydantic-ai agent call flows through ``gateway.chat`` /
``gateway.chat_stream``, so provider routing, key rotation, cooldowns and
the auto-failover policy stay in exactly one place — the adapter gets all
of it for free.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelResponseStreamEvent,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

from app.db import get_session_factory
from app.services.model_policy import (
    SSEDeltaParser,
    chat_or_auto,
    chat_stream_or_auto,
)


def _openai_messages(messages: list[ModelMessage]) -> list[dict[str, Any]]:
    """PydanticAI history → OpenAI message dicts understood by providers."""
    out: list[dict[str, Any]] = []
    for m in messages:
        if isinstance(m, ModelRequest):
            for part in m.parts:
                if part.part_kind == "system-prompt":
                    out.append({"role": "system", "content": part.content})
                elif part.part_kind == "user-prompt":
                    out.append({"role": "user", "content": part.content})
                elif part.part_kind == "tool-return":
                    out.append({
                        "role": "tool",
                        "tool_call_id": part.tool_call_id,
                        "content": str(part.content),
                    })
                elif part.part_kind == "retry-prompt":
                    out.append({"role": "user",
                                "content": part.model_response()})
        elif isinstance(m, ModelResponse):
            content, tool_calls = "", []
            for part in m.parts:
                if part.part_kind == "text":
                    content += part.content
                elif part.part_kind == "tool-call":
                    tool_calls.append({
                        "id": part.tool_call_id,
                        "type": "function",
                        "function": {
                            "name": part.tool_name,
                            "arguments": part.args_as_json_str(),
                        },
                    })
            msg: dict[str, Any] = {"role": "assistant", "content": content}
            if tool_calls:
                msg["tool_calls"] = tool_calls
            out.append(msg)
    return out


def _openai_tools(params: ModelRequestParameters) -> list[dict[str, Any]]:
    """PydanticAI function tools → OpenAI tool specs."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": t.parameters_json_schema,
            },
        }
        for t in params.function_tools
    ]


class GatewayStreamedResponse(StreamedResponse):
    """SSE byte stream → pydantic-ai text-delta events."""

    _stream: AsyncIterator[bytes]
    _model_name: str
    _provider_name: str | None

    def __init__(
        self,
        model_request_parameters: ModelRequestParameters,
        *,
        stream: AsyncIterator[bytes],
        model_name: str,
        provider_name: str | None,
    ) -> None:
        super().__init__(model_request_parameters)
        self._stream = stream
        self._model_name = model_name
        self._provider_name = provider_name
        self._timestamp = datetime.now(timezone.utc)

    async def _get_event_iterator(
        self,
    ) -> AsyncIterator[ModelResponseStreamEvent]:
        # Deltas must flow through ``_parts_manager`` — it registers
        # parts and returns the PartStart/PartDelta events to yield.
        # pydantic-ai's stream wrapper emits the closing PartEndEvent
        # for still-open parts itself.
        parser = SSEDeltaParser()
        async for chunk in self._stream:
            for piece in parser.feed(chunk):
                for event in self._parts_manager.handle_text_delta(
                        vendor_part_id=None, content=piece):
                    yield event
        for piece in parser.flush():
            for event in self._parts_manager.handle_text_delta(
                    vendor_part_id=None, content=piece):
                yield event

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def provider_name(self) -> str | None:
        return self._provider_name

    @property
    def provider_url(self) -> str | None:
        return None

    @property
    def timestamp(self) -> datetime:
        return self._timestamp


class GatewayChatModel(Model):
    """Routes every pydantic-ai request through the app's gateway.

    Provider selection, cooldowns, billing hints and the pinned-model →
    ``auto`` failover are all handled downstream in ``chat_or_auto`` /
    ``chat_stream_or_auto``; the adapter only translates payloads.
    """

    def __init__(
        self,
        model_name: str,
        gateway: Any,
        emit: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        super().__init__()
        self._model_name = model_name or "auto"
        self._gateway = gateway
        self._emit = emit

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def system(self) -> str:
        return "nova-gateway"

    def _payload(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        params: ModelRequestParameters,
        *,
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model_name,
            "messages": _openai_messages(messages),
            "stream": stream,
        }
        for k in ("temperature", "top_p", "max_tokens", "stop", "seed",
                  "presence_penalty", "frequency_penalty"):
            if model_settings is None:
                continue
            # ModelSettings is a TypedDict — it arrives as a plain dict at
            # runtime, so getattr() would silently drop every setting.
            v = (model_settings.get(k)
                 if isinstance(model_settings, Mapping)
                 else getattr(model_settings, k, None))
            if v is not None:
                payload[k] = v
        tools = _openai_tools(params)
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if params.output_mode == "prompted":
            payload["response_format"] = {"type": "json_object"}
        return payload

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        payload = self._payload(
            messages, model_settings, model_request_parameters, stream=False)
        async with get_session_factory()() as session:
            result, provider, _key, model = await chat_or_auto(
                self._gateway, session, payload, self._emit)
        parts: list[Any] = []
        msg = ((result.get("choices") or [{}])[0]).get("message") or {}
        text = msg.get("content")
        if not isinstance(text, str):
            parts_of = text if isinstance(text, list) else []
            text = "".join(str(p.get("text") or "") for p in parts_of
                           if isinstance(p, dict))
        if text:
            parts.append(TextPart(content=text))
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            if fn.get("name"):
                parts.append(ToolCallPart(
                    tool_name=fn["name"],
                    args=fn.get("arguments") or "{}",
                    tool_call_id=tc.get("id") or "",
                ))
        usage = result.get("usage") or {}
        return ModelResponse(
            parts=parts or [TextPart(content="")],
            model_name=model,
            usage=RequestUsage(
                input_tokens=int(usage.get("prompt_tokens") or 0),
                output_tokens=int(usage.get("completion_tokens") or 0),
            ),
        )

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: Any | None = None,
    ) -> AsyncIterator[StreamedResponse]:
        payload = self._payload(
            messages, model_settings, model_request_parameters, stream=True)
        async with get_session_factory()() as session:
            stream, provider, _key, model = await chat_stream_or_auto(
                self._gateway, session, payload, self._emit)
            yield GatewayStreamedResponse(
                model_request_parameters,
                stream=stream,
                model_name=model,
                provider_name=getattr(provider, "name", None),
            )
