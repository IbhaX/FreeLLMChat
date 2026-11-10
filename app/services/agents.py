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
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

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
        if "response_format" in payload and "```" in text:
            import re as _re

            m = _re.search(r"```[^\n`]*\n?([\s\S]*?)```", text)
            if m:
                text = m.group(1).strip()
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
                    c = part.content
                    content += "".join(str(x) for x in c) if isinstance(c, list) else str(c)
        elif hasattr(m, "content"):
            c = m.content
            content = "".join(str(x) for x in c) if isinstance(c, list) else str(c)
        if content:
            out.append({"role": role, "content": content})
    return out


def _to_model_history(history: list[dict[str, Any]]) -> list[ModelMessage]:
    """Convert API history into Pydantic AI's validated message history.

    Only plain user/assistant text turns are accepted. Tool messages and other
    roles are intentionally excluded because the direct agent path does not
    replay tool-call state.
    """
    messages: list[ModelMessage] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content") or item.get("text", "")
        if not isinstance(content, str) or not content:
            continue
        if role == "user":
            messages.append(ModelRequest(parts=[UserPromptPart(content=content)]))
        elif role == "assistant":
            messages.append(ModelResponse(parts=[TextPart(content=content)]))
    return messages


def _bounded_history(history: list[dict[str, Any]], *, max_messages: int = 40, max_chars: int = 48000) -> list[dict[str, Any]]:
    """Keep the newest valid turns within a conservative character budget."""
    valid: list[dict[str, Any]] = []
    for item in history or []:
        if not isinstance(item, dict) or item.get("role") not in ("user", "assistant"):
            continue
        content = item.get("content") or item.get("text", "")
        if not isinstance(content, str) or not content.strip():
            continue
        valid.append({"role": item["role"], "content": content.strip()})
    selected: list[dict[str, Any]] = []
    used = 0
    for item in reversed(valid[-max_messages:]):
        content = item["content"]
        remaining = max_chars - used
        if remaining <= 0:
            break
        if len(content) > remaining:
            content = content[-remaining:]
        selected.append({"role": item["role"], "content": content})
        used += len(content)
    return list(reversed(selected))


def _memory_relevance(content: str, query: str) -> int:
    """Small dependency-free lexical relevance score for SQL memory snippets."""
    import re
    terms = {t for t in re.findall(r"[a-z0-9_]{3,}", query.lower())}
    if not terms:
        return 0
    words = set(re.findall(r"[a-z0-9_]{3,}", content.lower()))
    return len(terms & words)


def _compile_system_prompt(agent_cfg: AgentConfig) -> str:
    """Compile a rich system prompt from the structured config + raw prompt."""
    cfg = agent_cfg.config or {}
    parts: list[str] = []
    if agent_cfg.system_prompt:
        parts.append(agent_cfg.system_prompt)
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
    for k, label in [("creativity", "Creativity"), ("formality", "Formality"),
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
    instructions = cfg.get("instructions", {})
    if instructions.get("custom_rules"):
        parts.append(f"## Custom Rules\n{instructions['custom_rules']}")
    if instructions.get("constraints"):
        parts.append(f"## Constraints\n{instructions['constraints']}")
    if instructions.get("knowledge"):
        parts.append(f"## Knowledge Base\n{instructions['knowledge']}")
    execution = cfg.get("execution", {})
    if execution.get("decision_making"):
        parts.append(f"## Decision Making\n{execution['decision_making']}")
    if execution.get("error_handling"):
        parts.append(f"## Error Handling\n{execution['error_handling']}")
    if execution.get("max_retries"):
        parts.append(f"Max retries: {execution['max_retries']}")
    caps = cfg.get("capabilities", {})
    if caps.get("tools_enabled"):
        parts.append("You may use tool/function calls when helpful.")
    if agent_cfg.output_format == "json":
        parts.append("Always respond with valid JSON only — no prose, no markdown fences.")
    if agent_cfg.output_format == "structured":
        from app.response_protocol import component_schemas
        parts.append(
            "## Structured Output\nRespond using the structured response envelope. "
            "Put conversational text in `content` (markdown allowed) and rich data "
            "in `components`. Available component types: "
            + ", ".join(sorted(component_schemas().keys()))
            + ". Choose the component type that best fits the data; do not invent missing values."
        )
    return "\n\n".join(parts) if parts else "You are a helpful assistant."


def _build_agent(agent_cfg: AgentConfig, gateway: Gateway, custom: str = "") -> Agent:
    from pydantic_ai import Agent
    from pydantic_ai.output import PromptedOutput
    model = GatewayModel(agent_cfg.model, gateway)
    system_prompt = _compile_system_prompt(agent_cfg)
    system_prompt = structured_system_prompt() + "\n\n" + system_prompt
    if custom:
        from app.services.settings_store import custom_instructions_block
        system_prompt += "\n\n" + custom_instructions_block(custom)
    settings: dict[str, Any] = {}
    if agent_cfg.temperature is not None:
        settings["temperature"] = agent_cfg.temperature
    if agent_cfg.max_tokens is not None:
        settings["max_tokens"] = agent_cfg.max_tokens
    settings["response_format"] = {"type": "json_object"}
    return Agent(model, output_type=PromptedOutput(ResponseEnvelope),
                 system_prompt=system_prompt, model_settings=settings)


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
            rows = (await s.execute(select(Memory).order_by(Memory.id.desc()).limit(100))).scalars().all()
        ranked = sorted(rows, key=lambda m: (_memory_relevance(m.content, query), m.id), reverse=True)
        selected = [m for m in ranked if _memory_relevance(m.content, query) > 0][:8]
        if selected:
            parts.append("## Relevant Long-term Memory\n" + "\n".join(f"- {m.content}" for m in selected))
    except Exception:
        pass
    try:
        astra = astra_memory.recall_text(query, conversation_id)
        if astra:
            parts.append(astra)
    except Exception:
        pass
    return "\n\n".join(parts)


def _messages(agent_cfg: AgentConfig, message: Any, history: list[dict[str, Any]], custom: str = "") -> list[dict[str, Any]]:
    msgs = _bounded_history(history) + [{"role": "user", "content": message}]
    system_prompt = _compile_system_prompt(agent_cfg)
    system_prompt = structured_system_prompt() + "\n\n" + system_prompt
    if custom:
        from app.services.settings_store import custom_instructions_block
        system_prompt += "\n\n" + custom_instructions_block(custom)
    msgs.insert(0, {"role": "system", "content": system_prompt})
    return msgs


async def _chat_with_tools(
    gateway: Gateway, agent_cfg: AgentConfig, messages: list[dict[str, Any]],
    conversation_id: str | None = None,
) -> dict[str, Any]:
    from app.db import get_session_factory
    from app.services.tools import run_tool_loop
    tool_names = _tool_names(agent_cfg)
    query = next((m.get("content", "") for m in reversed(messages)
                  if m.get("role") == "user" and isinstance(m.get("content"), str)), "")
    mem = await get_memory_text(query, conversation_id)
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
    async with get_session_factory()() as session:
        return await run_tool_loop(gateway, session, payload, tool_names,
                                   max_rounds=max_rounds, call_timeout=timeout_s)


async def _ensure_agent_components(
    gateway: Gateway, message: str, history: list[dict[str, Any]], content: str,
) -> str:
    """Apply the same rich-response repair/fallback used by normal chat."""
    from app.services.component_enforce import enforce_components

    transcript = _bounded_history(history) + [{"role": "user", "content": message}]
    result = {"choices": [{"message": {"role": "assistant", "content": content}}]}
    async with get_session_factory()() as session:
        result, _metrics = await enforce_components(
            gateway, session, {"messages": transcript}, result,
        )
    return ((result.get("choices") or [{}])[0].get("message") or {}).get("content") or content


async def run_agent(
    gateway: Gateway, agent_cfg: AgentConfig, message: str, history: list[dict[str, Any]],
    *, custom_instructions: str = "", conversation_id: str | None = None,
) -> str:
    """Run an agent with bounded context, relevant memory and execution limits."""
    history = _bounded_history(history)
    execution = (agent_cfg.config or {}).get("execution", {})
    timeout_s = max(1.0, min(float(execution.get("timeout", 120)), 600.0))
    if _tool_names(agent_cfg):
        messages = _messages(agent_cfg, message, history, custom_instructions)
        result = await asyncio.wait_for(
            _chat_with_tools(gateway, agent_cfg, messages, conversation_id), timeout=timeout_s
        )
        choice = (result.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content") or ""
        content = _maybe_structured(agent_cfg, content)
        return await _ensure_agent_components(gateway, message, history, content)
    memory = await get_memory_text(str(message), conversation_id)
    agent = _build_agent(agent_cfg, gateway, custom_instructions + ("\n\n" + memory if memory else ""))
    result = await asyncio.wait_for(
        agent.run(message, message_history=_to_model_history(history)), timeout=timeout_s
    )
    out = result.output
    if isinstance(out, ResponseEnvelope):
        content = envelope_to_fence(out)
    else:
        content = str(out or "")
    return await _ensure_agent_components(gateway, message, history, content)


def _maybe_structured(agent_cfg: AgentConfig, content: str) -> str:
    if not content:
        return content
    candidates = [content.strip()]
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
    gateway: Gateway, agent_cfg: AgentConfig, message: str, history: list[dict[str, Any]],
    *, custom_instructions: str = "", conversation_id: str | None = None,
) -> AsyncIterator[bytes]:
    """Run an agent with streaming via the gateway's internal streaming API."""
    history = _bounded_history(history)
    openai_messages = _messages(agent_cfg, message, history, custom_instructions)
    if not _tool_names(agent_cfg):
        mem = await get_memory_text(str(message), conversation_id)
        if mem:
            openai_messages.insert(1, {"role": "system", "content": mem})
    if True:  # All agent replies use the rich response envelope, regardless of legacy output_format.
        try:
            text = await run_agent(gateway, agent_cfg, message, history,
                                   custom_instructions=custom_instructions, conversation_id=conversation_id)
            chunk = {"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]}
            yield f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()
        except Exception as e:
            err = {"error": {"message": f"agent error: {e}", "type": "agent_error"}}
            yield f"data: {json.dumps(err)}\n\ndata: [DONE]\n\n".encode()
        return
    if _tool_names(agent_cfg):
        try:
            result = await _chat_with_tools(gateway, agent_cfg, openai_messages, conversation_id)
            choice = (result.get("choices") or [{}])[0]
            text = (choice.get("message") or {}).get("content") or ""
            chunk = {"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]}
            yield f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()
        except Exception as e:
            err = {"error": {"message": f"agent error: {e}", "type": "agent_error"}}
            yield f"data: {json.dumps(err)}\n\ndata: [DONE]\n\n".encode()
        return
    payload = {"model": agent_cfg.model, "messages": openai_messages, "stream": True}
    if agent_cfg.temperature is not None:
        payload["temperature"] = agent_cfg.temperature
    if agent_cfg.max_tokens is not None:
        payload["max_tokens"] = agent_cfg.max_tokens
    execution = (agent_cfg.config or {}).get("execution", {})
    timeout_s = max(1.0, min(float(execution.get("timeout", 120)), 600.0))
    try:
        async with asyncio.timeout(timeout_s):
            async with get_session_factory()() as session:
                stream, provider, key, model = await gateway.chat_stream(session, payload)
                async for chunk in stream:
                    yield chunk
    except Exception as e:
        err = {"error": {"message": f"agent error: {e}", "type": "agent_error"}}
        yield f"data: {json.dumps(err)}\n\ndata: [DONE]\n\n".encode()
