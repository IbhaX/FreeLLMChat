"""Agent runner using Pydantic AI, backed by the gateway's internal API.

Instead of making an HTTP call to our own /v1 endpoint (which causes
DB session contention and deadlocks), we use the Gateway class directly
to resolve the model, pick a key, and call the provider — then feed
the result to Pydantic AI as a custom model implementation.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from app.gateway import Gateway
from app.models import AgentConfig
from app.response_protocol import (
    ResponseEnvelope,
    envelope_to_fence,
    validate_envelope,
)


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


def _bounded_history(
    history: list[dict[str, Any]], *, max_messages: int = 40, max_chars: int = 48000
) -> list[dict[str, Any]]:
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
    cfg = _cfg(agent_cfg)
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
    caps = _caps(cfg)
    if caps.get("tools_enabled"):
        parts.append("You may use tool/function calls when helpful.")
    skill_names = _skill_names(agent_cfg)
    if skill_names:
        from app.services import skills as skills_mod
        prompt = "\n\n".join(parts) if parts else "You are a helpful assistant."
        return skills_mod.attach_instructions(prompt, skill_names)
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


def _cfg(agent_cfg: AgentConfig) -> dict[str, Any]:
    """Agent ``config`` column, always a dict (JSON column can hold anything)."""
    return agent_cfg.config if isinstance(agent_cfg.config, dict) else {}


def _caps(cfg: dict[str, Any]) -> dict[str, Any]:
    """``capabilities`` may be a dict (wizard) or a plain list of strings
    (builtin seed agents) — only the dict shape carries tool metadata."""
    caps = cfg.get("capabilities")
    return caps if isinstance(caps, dict) else {}


def _skill_names(agent_cfg: AgentConfig) -> list[str]:
    """Skill names attached to the agent (wizard: capabilities.skills;
    seed agents: config.skills)."""
    cfg = _cfg(agent_cfg)
    names = _caps(cfg).get("skills") or cfg.get("skills") or []
    return [n for n in names if isinstance(n, str)]


def _tool_names(agent_cfg: AgentConfig) -> list[str]:
    cfg = _cfg(agent_cfg)
    caps = _caps(cfg)
    explicit = caps.get("tools") or cfg.get("tools")
    if caps.get("tools_enabled") or cfg.get("tools_enabled") \
            or agent_cfg.enable_tools:
        tools = list(explicit) if isinstance(explicit, list) \
            and explicit else ["*"]
    else:
        tools = list(explicit) if isinstance(explicit, list) else []
    # Skills may require tools the agent didn't explicitly enable —
    # union them so the skill actually works.
    from app.services import skills as skills_mod
    for t in skills_mod.required_tools(_skill_names(agent_cfg)):
        if tools != ["*"] and t not in tools:
            tools.append(t)
    return tools


async def get_memory_text(query: str = "", conversation_id: str | None = None,
                          user: "Any | None" = None) -> str:
    """Fetch persistent + vector memories for system-prompt injection.

    Tenant-scoped: an authenticated user sees only their own persistent
    memories and their own vector memories; the anonymous tenant (auth-off
    single-tenant mode) sees only unowned rows. Never the union."""
    from sqlalchemy import select

    from app.db import get_session_factory
    from app.models import Memory
    from app.services import astra_memory
    tenant = astra_memory.tenant_key(user)
    parts: list[str] = []
    try:
        async with get_session_factory()() as s:
            stmt = select(Memory).order_by(Memory.id.desc()).limit(100)
            if user is not None:
                stmt = stmt.where(Memory.owner_id == user.id)
            else:
                stmt = stmt.where(Memory.owner_id.is_(None))
            rows = (await s.execute(stmt)).scalars().all()
        ranked = sorted(
            rows,
            key=lambda m: (_memory_relevance(m.content, query), m.id),
            reverse=True,
        )
        selected = [m for m in ranked if _memory_relevance(m.content, query) > 0][:8]
        if selected:
            lines = "\n".join(f"- {m.content}" for m in selected)
            parts.append("## Relevant Long-term Memory\n" + lines)
    except Exception:
        pass
    try:
        # astrapy calls are synchronous HTTP — run off the event loop so a
        # slow Astra round-trip cannot stall concurrent requests.
        astra = await asyncio.to_thread(
            astra_memory.recall_text, query, tenant, conversation_id)
        if astra:
            parts.append(astra)
    except Exception:
        pass
    return "\n\n".join(parts)


_TOOLISH_KEYS = {
    "query", "queries", "q", "search", "max_results", "tool", "tool_name",
    "tool_calls", "function_call", "arguments", "args", "input", "url",
    "expression", "code",
}


def _looks_like_tool_blob(text: str) -> bool:
    """True when the reply is a tool call leaked as JSON text — weak models
    emit the call instead of invoking the tool. Shapes seen in the wild:
    ``{"query": ..., "max_results": 5}``, ``{"tool_calls": [{...}]}``, and
    the same nested inside a fake component's ``data``. Real response
    envelopes and component objects are legit JSON and are excluded."""
    t = (text or "").strip()
    if not (t.startswith("{") and t.endswith("}")):
        return False
    try:
        obj = json.loads(t)
    except Exception:
        return False
    if not isinstance(obj, dict) or not obj or len(obj) > 6:
        return False
    # A nested {"tool_calls": [...]} anywhere one level down is a leaked
    # call no matter the wrapper.
    for v in obj.values():
        if isinstance(v, dict) and "tool_calls" in v:
            return True
        if isinstance(v, list) and v and isinstance(v[0], dict) and (
                "name" in v[0] and ("parameters" in v[0] or "arguments" in v[0]
                                    or "query" in v[0])):
            return True
    if "response_type" in obj or ("type" in obj and "data" in obj):
        return False
    return bool(set(obj) & _TOOLISH_KEYS)


_TOOL_BLOB_RETRY = (
    "Your previous reply was a raw tool-call JSON object, not an answer. "
    "Write the actual answer for the user now as normal prose/components — "
    "never output a bare JSON object."
)
_TOOL_BLOB_FALLBACK = (
    "*(could not produce an answer — the model emitted a tool call instead "
    "of text)*"
)


def _usage_limits(max_tool_calls: int) -> Any:
    """Cap tool-call volume per run via pydantic-ai's native limits."""
    from pydantic_ai.usage import UsageLimits

    return UsageLimits(tool_calls_limit=max(1, max_tool_calls))


def _agent_deps(
    gateway: Gateway, agent_cfg: AgentConfig, *,
    conversation_id: str | None,
    emit: Callable[[dict[str, Any]], None] | None,
    model_override: str | None,
) -> Any:
    """Typed RunContext dependencies for one agent execution."""
    from app.agents.deps import AgentDeps

    execution = _cfg(agent_cfg).get("execution", {})
    return AgentDeps(
        gateway,
        emit=emit,
        conversation_id=conversation_id,
        model_override=model_override,
        max_tool_calls=min(int(execution.get("max_retries") or 5), 10) * 4,
    )


async def _plain_text_fallback(
    agent_cfg: AgentConfig, deps: Any, message: str,
    history: list[dict[str, Any]], custom: str,
    timeout_s: float, limits: Any,
) -> str:
    """Re-run once with ``output_format='text'`` when structured output
    fails validation or comes back empty — never show a raw envelope."""
    import copy

    from app.agents.factory import build_agent

    cfg2 = copy.copy(agent_cfg)
    cfg2.output_format = "text"
    fallback = build_agent(cfg2, deps, custom_instructions=custom)
    try:
        result = await asyncio.wait_for(
            fallback.run(message, deps=deps,
                         message_history=_to_model_history(history),
                         usage_limits=limits),
            timeout=timeout_s)
        out = result.output
    except Exception:
        out = ""
    return out if isinstance(out, str) and out.strip() else _TOOL_BLOB_FALLBACK


async def run_agent(
    gateway: Gateway, agent_cfg: AgentConfig, message: str, history: list[dict[str, Any]],
    *, custom_instructions: str = "", conversation_id: str | None = None,
    emit: Callable[[dict[str, Any]], None] | None = None,
    model_override: str | None = None,
    user: "Any | None" = None,
) -> str:
    """Run an agent natively through pydantic-ai.

    Tool rounds, output-validation retries and provider failover all
    happen inside the pydantic-ai run — no bespoke orchestration loops.
    user is the verified caller — memory recall is scoped to it.
    """
    from app.agents.factory import build_agent

    history = _bounded_history(history)
    execution = _cfg(agent_cfg).get("execution", {})
    timeout_s = max(1.0, min(float(execution.get("timeout") or 120), 600.0))
    memory = await get_memory_text(str(message), conversation_id, user=user)
    custom = custom_instructions + ("\n\n" + memory if memory else "")
    deps = _agent_deps(gateway, agent_cfg, conversation_id=conversation_id,
                       emit=emit, model_override=model_override)
    limits = _usage_limits(deps.max_tool_calls)
    agent = build_agent(agent_cfg, deps, custom_instructions=custom)
    try:
        result = await asyncio.wait_for(
            agent.run(message, deps=deps,
                      message_history=_to_model_history(history),
                      usage_limits=limits),
            timeout=timeout_s)
    except Exception as e:
        # Output-validation retries exhausted on a structured agent —
        # retry once as plain text instead of returning the raw error.
        if agent_cfg.output_format != "structured" or (
                "retries" not in str(e).lower()
                and "output" not in type(e).__name__.lower()):
            raise
        return await _plain_text_fallback(
            agent_cfg, deps, message, history, custom, timeout_s, limits)
    out = result.output
    if isinstance(out, ResponseEnvelope):
        if out.content or out.components:
            return envelope_to_fence(out)
        return await _plain_text_fallback(
            agent_cfg, deps, message, history, custom, timeout_s, limits)
    text = out if isinstance(out, str) else ""
    if _looks_like_tool_blob(text):
        # Weak models can leak the call as text — retry once with the
        # leaked blob visible in history, then give up honestly.
        try:
            retry = await asyncio.wait_for(
                agent.run(_TOOL_BLOB_RETRY, deps=deps,
                          message_history=result.all_messages(),
                          usage_limits=limits),
                timeout=timeout_s)
            text = retry.output if isinstance(retry.output, str) else ""
        except Exception:
            text = ""
        if not text.strip() or _looks_like_tool_blob(text):
            return _TOOL_BLOB_FALLBACK
    return _maybe_structured(agent_cfg, text)


def _maybe_structured(agent_cfg: AgentConfig, content: str) -> str:
    structured = agent_cfg.output_format == "structured"
    if agent_cfg.output_format == "json" or not content:
        return content
    candidates = [content.strip()]
    import re
    fence_match = re.search(r"```(?:json)?\s*\n?([\s\S]*?)```", content.strip())
    if fence_match:
        candidates.append(fence_match.group(1).strip())
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
            if not structured and not (
                    isinstance(payload, dict) and "response_type" in payload):
                continue
            env = validate_envelope(payload)
        except Exception:
            continue
        if env.content or env.components:
            return envelope_to_fence(env)
        # Valid shape but empty envelope — treat as a failed answer.
        return _TOOL_BLOB_FALLBACK if structured else content
    return content


async def stream_agent(
    gateway: Gateway, agent_cfg: AgentConfig, message: str, history: list[dict[str, Any]],
    *, custom_instructions: str = "", conversation_id: str | None = None,
    emit: Callable[[dict[str, Any]], None] | None = None,
    model_override: str | None = None,
    user: "Any | None" = None,
) -> AsyncIterator[bytes]:
    """Stream an agent reply via pydantic-ai's native streaming.

    Structured-output and tool-enabled agents run buffered (native tool
    rounds + output validation), then emit one SSE chunk — the same
    downstream contract as before.
    """
    from app.agents.factory import build_agent

    history = _bounded_history(history)
    execution = _cfg(agent_cfg).get("execution", {})
    timeout_s = max(1.0, min(float(execution.get("timeout") or 120), 600.0))
    if agent_cfg.output_format == "structured" or _tool_names(agent_cfg):
        try:
            text = await run_agent(gateway, agent_cfg, message, history,
                                   custom_instructions=custom_instructions,
                                   conversation_id=conversation_id, emit=emit,
                                   model_override=model_override, user=user)
            chunk = {"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]}
            yield f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()
        except Exception as e:
            err = {"error": {"message": f"agent error: {e}", "type": "agent_error"}}
            yield f"data: {json.dumps(err)}\n\ndata: [DONE]\n\n".encode()
        return
    memory = await get_memory_text(str(message), conversation_id, user=user)
    deps = _agent_deps(gateway, agent_cfg, conversation_id=conversation_id,
                       emit=emit, model_override=model_override)
    agent = build_agent(
        agent_cfg, deps,
        custom_instructions=custom_instructions + ("\n\n" + memory if memory else ""))
    try:
        async with asyncio.timeout(timeout_s):
            async with agent.run_stream(
                    message, deps=deps,
                    message_history=_to_model_history(history),
                    usage_limits=_usage_limits(deps.max_tool_calls)) as result:
                async for delta in result.stream_text(delta=True):
                    if not delta:
                        continue
                    chunk = {"choices": [{"index": 0,
                                          "delta": {"content": delta},
                                          "finish_reason": None}]}
                    yield f"data: {json.dumps(chunk)}\n\n".encode()
            yield b"data: [DONE]\n\n"
    except Exception as e:
        err = {"error": {"message": f"agent error: {e}", "type": "agent_error"}}
        yield f"data: {json.dumps(err)}\n\ndata: [DONE]\n\n".encode()
