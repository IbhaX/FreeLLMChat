"""Agent runner using Pydantic AI, backed by the gateway's internal API.

Instead of making an HTTP call to our own /v1 endpoint (which causes
DB session contention and deadlocks), we use the Gateway class directly
to resolve the model, pick a key, and call the provider — then feed
the result to Pydantic AI as a custom model implementation.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, AsyncIterator

from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart

from app.db import get_session_factory
from app.gateway import Gateway
from app.models import AgentConfig
from app.response_protocol import (
    RESPONSE_FENCE_TAG,
    ResponseEnvelope,
    envelope_to_fence,
    structured_system_prompt,
    validate_envelope,
)


class GatewayModel(Model):
    """A Pydantic AI Model that delegates to the FreeLLM Gateway's internal API."""

    def __init__(self, model_name: str, gateway: Gateway) -> None:
        self._model_name = model_name
        self._gateway = gateway

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def system(self) -> str:
        return "freeLLM-gateway"

    async def request(
        self, messages: list[ModelMessage], model_settings, params: ModelRequestParameters
    ) -> ModelResponse:
        """Non-streaming request through the gateway.

        Forwards safe request parameters (temperature, response_format, etc.)
        from pydantic-ai model_settings onto the gateway payload so structured
        output can leverage provider-level JSON mode when available.
        """
        openai_messages = _convert_messages(messages)
        payload: dict[str, Any] = {
            "model": self._model_name,
            "messages": openai_messages,
            "stream": False,
        }
        forward = {
            "temperature", "top_p", "max_tokens", "stop", "seed",
            "response_format", "presence_penalty", "frequency_penalty",
        }
        for k, v in (model_settings or {}).items():
            if k in forward and v is not None:
                payload[k] = v
        async with get_session_factory()() as session:
            result, provider, key, model = await self._gateway.chat(
                session, payload
            )
        text = ""
        for choice in result.get("choices", []):
            msg = choice.get("message", {})
            text += msg.get("content", "")
        return ModelResponse(parts=[TextPart(content=text)])

    async def request_stream(
        self, messages: list[ModelMessage], params: ModelRequestParameters
    ) -> StreamedResponse:
        raise NotImplementedError("Use stream_agent() for streaming")


def _convert_messages(messages: list[ModelMessage]) -> list[dict[str, Any]]:
    """Convert Pydantic AI messages to OpenAI format."""
    out: list[dict[str, Any]] = []
    for m in messages:
        role = "user"
        content = ""
        if hasattr(m, "role"):
            role = m.role
        if hasattr(m, "parts"):
            for part in m.parts:
                if hasattr(part, "content"):
                    content += part.content
        elif hasattr(m, "content"):
            content = m.content
        if content:
            out.append({"role": role, "content": content})
    return out


def _compile_system_prompt(agent_cfg: AgentConfig) -> str:
    """Compile a rich system prompt from the structured config + raw prompt."""
    cfg = agent_cfg.config or {}
    parts: list[str] = []

    # Base identity
    if agent_cfg.system_prompt:
        parts.append(agent_cfg.system_prompt)

    # Step 1: Basic info
    basic = cfg.get("basic", {})
    if basic.get("detailed_description"):
        parts.append(f"## Your Purpose\n{basic['detailed_description']}")
    if basic.get("primary_objective"):
        parts.append(f"## Primary Objective\n{basic['primary_objective']}")
    if basic.get("problems"):
        parts.append(f"## Problems You Solve\n{basic['problems']}")
    if basic.get("category"):
        parts.append(f"## Category\nYou are a {basic['category']} agent.")
    if basic.get("target_audience"):
        parts.append(f"## Target Audience\n{basic['target_audience']}")
    if basic.get("domain"):
        parts.append(f"## Domain\n{basic['domain']}")

    # Step 2: Personality
    personality = cfg.get("personality", {})
    traits: list[str] = []
    if personality.get("personality_type"):
        traits.append(f"Personality: {personality['personality_type']}")
    if personality.get("communication_style"):
        traits.append(f"Communication style: {personality['communication_style']}")
    if personality.get("tone"):
        traits.append(f"Tone: {personality['tone']}")
    if personality.get("response_length"):
        traits.append(f"Response length: {personality['response_length']}")
    if personality.get("language"):
        traits.append(f"Language: {personality['language']}")
    if personality.get("technical_detail"):
        traits.append(f"Technical detail level: {personality['technical_detail']}")
    for k, label in [
        ("creativity", "Creativity"), ("formality", "Formality"),
        ("assertiveness", "Assertiveness"), ("empathy", "Empathy")]:
        v = personality.get(k)
        if v is not None:
            traits.append(f"{label}: {v}/10")
    if traits:
        parts.append("## Personality & Behavior\n" + "\n".join(f"- {t}" for t in traits))
    flags: list[str] = []
    if personality.get("ask_clarifying"):
        flags.append("Ask clarifying questions when the request is ambiguous.")
    if personality.get("explain_reasoning"):
        flags.append("Explain your reasoning step by step.")
    if personality.get("provide_examples"):
        flags.append("Provide examples to illustrate your points.")
    if personality.get("cite_sources"):
        flags.append("Cite sources when making factual claims.")
    if personality.get("admit_uncertainty"):
        flags.append("Admit uncertainty instead of guessing.")
    if flags:
        parts.append("## Behavioral Rules\n" + "\n".join(f"- {f}" for f in flags))

    # Step 3: Instructions (extra rules)
    instructions = cfg.get("instructions", {})
    if instructions.get("custom_rules"):
        parts.append(f"## Custom Rules\n{instructions['custom_rules']}")
    if instructions.get("constraints"):
        parts.append(f"## Constraints\n{instructions['constraints']}")
    if instructions.get("knowledge"):
        parts.append(f"## Knowledge Base\n{instructions['knowledge']}")

    # Step 4: Execution
    execution = cfg.get("execution", {})
    if execution.get("decision_making"):
        parts.append(f"## Decision Making\n{execution['decision_making']}")
    if execution.get("error_handling"):
        parts.append(f"## Error Handling\n{execution['error_handling']}")
    if execution.get("max_retries"):
        parts.append(f"Max retries: {execution['max_retries']}")

    # Capabilities
    caps = cfg.get("capabilities", {})
    if caps.get("tools_enabled"):
        parts.append("You may use tool/function calls when helpful.")

    if agent_cfg.output_format == "json":
        parts.append("Always respond with valid JSON only — no prose, no markdown fences.")
    if agent_cfg.output_format == "structured":
        from app.response_protocol import component_schemas

        parts.append(
            "## Structured Output\n"
            "Respond using the structured response envelope. Put conversational "
            "text in `content` (markdown allowed) and rich data in `components`. "
            "Available component types: "
            + ", ".join(sorted(component_schemas().keys()))
            + ". Choose the component type that best fits the data; do not invent "
            "missing values."
        )

    return "\n\n".join(parts) if parts else "You are a helpful assistant."


def _build_agent(agent_cfg: AgentConfig, gateway: Gateway) -> Agent:
    from pydantic_ai import Agent
    from pydantic_ai.output import PromptedOutput

    model = GatewayModel(agent_cfg.model, gateway)
    # Always append the structured-output protocol so agent responses are
    # rendered as rich components, not plain text.
    system_prompt = _compile_system_prompt(agent_cfg) + "\n\n" + structured_system_prompt()
    settings: dict[str, Any] = {}
    if agent_cfg.temperature is not None:
        settings["temperature"] = agent_cfg.temperature
    if agent_cfg.max_tokens is not None:
        settings["max_tokens"] = agent_cfg.max_tokens

    # Structured output: pydantic-ai injects the ResponseEnvelope JSON schema
    # into the prompt, parses the model's JSON text, and validates it against
    # the envelope — with automatic retries on invalid output. We also request
    # provider-level JSON mode so providers that support it emit clean JSON.
    if agent_cfg.output_format == "structured":
        settings["response_format"] = {"type": "json_object"}
        return Agent(
            model,
            output_type=PromptedOutput(ResponseEnvelope),
            system_prompt=system_prompt,
            model_settings=settings,
        )

    return Agent(
        model,
        system_prompt=system_prompt,
        model_settings=settings or None,
    )


def _tool_names(agent_cfg: AgentConfig) -> list[str]:
    cfg = agent_cfg.config or {}
    caps = cfg.get("capabilities", {})
    if not (caps.get("tools_enabled") or agent_cfg.enable_tools):
        return []
    tools = caps.get("tools")
    return list(tools) if isinstance(tools, list) and tools else ["*"]


async def get_memory_text(query: str = "", conversation_id: str | None = None) -> str:
    """Fetch persistent + vector memories for system-prompt injection."""
    from sqlalchemy import select

    from app.db import get_session_factory
    from app.models import Memory
    from app.services import astra_memory

    parts: list[str] = []
    try:
        async with get_session_factory()() as s:
            rows = (
                (await s.execute(select(Memory).order_by(Memory.id).limit(50)))
                .scalars().all()
            )
        if rows:
            parts.append("## Long-term Memory\n"
                         + "\n".join(f"- {m.content}" for m in rows))
    except Exception:
        pass
    try:
        astra = astra_memory.recall_text(query, conversation_id)
        if astra:
            parts.append(astra)
    except Exception:
        pass
    return "\n\n".join(parts)


def _messages(agent_cfg: AgentConfig, message: Any, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    msgs = [{"role": "user", "content": message}]
    for h in reversed(history):
        role = h.get("role", "user")
        content = h.get("content") or h.get("text", "")
        if role in ("user", "assistant") and content:
            msgs.insert(0, {"role": role, "content": content})
    system_prompt = _compile_system_prompt(agent_cfg)
    # Always inject the structured-output protocol so agent responses are
    # rendered as rich components, not plain text.
    system_prompt = system_prompt + "\n\n" + structured_system_prompt()
    msgs.insert(0, {"role": "system", "content": system_prompt})
    return msgs


async def _chat_with_tools(
    gateway: Gateway, agent_cfg: AgentConfig, messages: list[dict[str, Any]]
) -> dict[str, Any]:
    """Non-streaming chat with a tool-call execution loop (max 5 rounds)."""
    from app.db import get_session_factory
    from app.services.tools import run_tool, specs_for

    tool_names = _tool_names(agent_cfg)
    specs = specs_for(tool_names)
    query = next(
        (m.get("content", "") for m in reversed(messages)
         if m.get("role") == "user" and isinstance(m.get("content"), str)),
        "",
    )
    mem = await get_memory_text(query)
    if mem:
        messages.insert(1 if messages and messages[0].get("role") == "system" else 0,
                        {"role": "system", "content": mem})
    payload: dict[str, Any] = {"model": agent_cfg.model, "messages": messages, "stream": False}
    if agent_cfg.temperature is not None:
        payload["temperature"] = agent_cfg.temperature
    if agent_cfg.max_tokens is not None:
        payload["max_tokens"] = agent_cfg.max_tokens
    if agent_cfg.output_format in ("json", "structured"):
        payload["response_format"] = {"type": "json_object"}
    execution = (agent_cfg.config or {}).get("execution", {})
    timeout_s = execution.get("timeout")
    max_rounds = min(int(execution.get("max_retries") or 5), 10)
    if specs:
        payload["tools"] = specs
        payload["tool_choice"] = "auto"

    async with get_session_factory()() as session:
        for _round in range(max_rounds):
            try:
                result, _p, _k, _m = await asyncio.wait_for(
                    gateway.chat(session, payload), timeout=timeout_s or None
                )
            except asyncio.TimeoutError:
                raise TimeoutError(f"agent timed out after {timeout_s}s")
            choice = (result.get("choices") or [{}])[0]
            msg = choice.get("message", {})
            calls = msg.get("tool_calls") or []
            if not specs or not calls:
                return result
            # append assistant tool-call message + each tool result, then re-call
            messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
            for tc in calls:
                fn = tc.get("function", {})
                import json as _json
                try:
                    args = _json.loads(fn.get("arguments") or "{}")
                except Exception:
                    args = {}
                out = await run_tool(fn.get("name", ""), args)
                messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": out})
            payload["tools"] = specs  # keep tools offered so model can chain
        return result


async def run_agent(
    gateway: Gateway, agent_cfg: AgentConfig, message: str, history: list[dict[str, Any]]
) -> str:
    """Run an agent non-streaming; uses the tool loop when tools are enabled.

    When ``output_format == "structured"``, pydantic-ai validates the model
    output into a :class:`ResponseEnvelope`, which we serialize into the
    ``nova-response`` fenced block the frontend classifier renders.
    """
    if _tool_names(agent_cfg):
        messages = _messages(agent_cfg, message, history)
        result = await _chat_with_tools(gateway, agent_cfg, messages)
        choice = (result.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content") or ""
        return _maybe_structured(agent_cfg, content)
    agent = _build_agent(agent_cfg, gateway)
    result = await agent.run(message)
    out = result.output
    if isinstance(out, ResponseEnvelope):
        return envelope_to_fence(out)
    return out


def _maybe_structured(agent_cfg: AgentConfig, content: str) -> str:
    """Validate raw model content as a ResponseEnvelope when structured mode
    is on; embed it as a ``nova-response`` fence. Falls back to the raw text
    if the content isn't a valid envelope (graceful degradation)."""
    if agent_cfg.output_format != "structured" or not content:
        return content
    candidates = [content.strip()]
    # Model may wrap JSON in a ```json fence — strip and retry once.
    import re

    fence_match = re.search(r"```(?:json)?\s*\n?([\s\S]*?)```", content.strip())
    if fence_match:
        candidates.append(fence_match.group(1).strip())
    for candidate in candidates:
        try:
            return envelope_to_fence(validate_envelope(json.loads(candidate)))
        except Exception:
            continue
    return content


async def stream_agent(
    gateway: Gateway, agent_cfg: AgentConfig, message: str, history: list[dict[str, Any]]
) -> AsyncIterator[bytes]:
    """Run an agent with streaming via the gateway's internal streaming API.

    Yields SSE bytes compatible with the chat UI's stream parser.
    """
    openai_messages = _messages(agent_cfg, message, history)
    mem = await get_memory_text(message)
    if mem:
        openai_messages.insert(
            1 if openai_messages and openai_messages[0].get("role") == "system" else 0,
            {"role": "system", "content": mem},
        )

    # Structured output can't be streamed incrementally (partial JSON is not a
    # valid envelope), so run the agent non-streaming and emit the validated
    # nova-response fence as a single SSE chunk — the frontend classifier
    # renders it into structured components. Same pattern as the tool loop.
    if agent_cfg.output_format == "structured":
        try:
            text = await run_agent(gateway, agent_cfg, message, history)
            chunk = {"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]}
            yield f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()
        except Exception as e:
            err = {"error": {"message": f"agent error: {e}", "type": "agent_error"}}
            yield f"data: {json.dumps(err)}\n\ndata: [DONE]\n\n".encode()
        return

    # When tools are enabled, run the tool loop non-streaming (tool calls can't
    # be executed mid-stream), then emit the final answer as SSE.
    if _tool_names(agent_cfg):
        try:
            result = await _chat_with_tools(gateway, agent_cfg, openai_messages)
            choice = (result.get("choices") or [{}])[0]
            text = (choice.get("message") or {}).get("content") or ""
            chunk = {"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]}
            yield f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()
        except Exception as e:
            err = {"error": {"message": f"agent error: {e}", "type": "agent_error"}}
            yield f"data: {json.dumps(err)}\n\ndata: [DONE]\n\n".encode()
        return

    payload = {
        "model": agent_cfg.model,
        "messages": openai_messages,
        "stream": True,
    }
    if agent_cfg.temperature is not None:
        payload["temperature"] = agent_cfg.temperature
    if agent_cfg.max_tokens is not None:
        payload["max_tokens"] = agent_cfg.max_tokens

    try:
        async with get_session_factory()() as session:
            stream, provider, key, model = await gateway.chat_stream(session, payload)
            async for chunk in stream:
                yield chunk
    except Exception as e:
        err = {"error": {"message": f"agent error: {e}", "type": "agent_error"}}
        yield f"data: {json.dumps(err)}\n\ndata: [DONE]\n\n".encode()
