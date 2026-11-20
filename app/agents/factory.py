"""Agent factory — turns an ``AgentConfig`` row into a native pydantic-ai
``Agent`` with the gateway model, toolset, output contract and settings.

This is the single construction point every surface (chat, agents,
roleplay, research, group) migrates to.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic_ai import Agent
from pydantic_ai.output import PromptedOutput

from app.agents.deps import AgentDeps
from app.agents.model import GatewayChatModel
from app.agents.tools import build_toolset
from app.response_protocol import ResponseEnvelope, structured_system_prompt

if TYPE_CHECKING:
    from app.models import AgentConfig


def compose_system_prompt(
    agent_cfg: AgentConfig, custom_instructions: str = ""
) -> str:
    """Compiled system prompt for one agent config (pure, testable)."""
    from app.services.agents import _compile_system_prompt

    prompt = _compile_system_prompt(agent_cfg)
    if agent_cfg.output_format == "structured":
        prompt = structured_system_prompt() + "\n\n" + prompt
    if custom_instructions:
        from app.services.settings_store import custom_instructions_block
        prompt += "\n\n" + custom_instructions_block(custom_instructions)
    return prompt


def build_simple_agent(
    model_name: str | None,
    system_prompt: str,
    deps: AgentDeps,
    *,
    tools: list[str] | None = None,
    toolset: Any = None,
    temperature: float | None = None,
    output_type: Any = str,
    retries: int = 1,
) -> Agent[AgentDeps, Any]:
    """Native pydantic-ai agent for ad-hoc prompts (roleplay turns,
    research steps) — no ``AgentConfig`` row required."""
    model = GatewayChatModel(model_name or "auto", deps.gateway, deps.emit)
    settings = {"temperature": temperature} if temperature is not None else None
    if toolset is None:
        toolset = build_toolset(tools)
    kwargs: dict[str, Any] = {
        "system_prompt": system_prompt,
        "model_settings": settings,
        "deps_type": AgentDeps,
        "retries": retries,
    }
    if toolset is not None:
        kwargs["toolsets"] = [toolset]
    if output_type is not str:
        kwargs["output_type"] = output_type
    return Agent(model, **kwargs)


def build_agent(
    agent_cfg: AgentConfig,
    deps: AgentDeps,
    *,
    system_prompt: str | None = None,
    custom_instructions: str = "",
) -> Agent[AgentDeps, Any]:
    """Native pydantic-ai agent for one agent configuration."""
    # The agent-definition helpers still live in services.agents during the
    # migration; they are pure (config → names/prompts), no orchestration.
    from app.services.agents import _tool_names

    model_name = deps.model_override or agent_cfg.model or "auto"
    model = GatewayChatModel(model_name, deps.gateway, deps.emit)

    prompt = system_prompt or compose_system_prompt(
        agent_cfg, custom_instructions)

    settings: dict[str, Any] = {}
    if agent_cfg.temperature is not None:
        settings["temperature"] = agent_cfg.temperature
    if agent_cfg.max_tokens is not None:
        settings["max_tokens"] = agent_cfg.max_tokens

    toolset = build_toolset(_tool_names(agent_cfg))
    kwargs: dict[str, Any] = {
        "system_prompt": prompt,
        "model_settings": settings or None,
        "deps_type": AgentDeps,
    }
    if toolset is not None:
        kwargs["toolsets"] = [toolset]
    if agent_cfg.output_format == "structured":
        # PromptedOutput enforces the nova-response envelope; ``retries``
        # gives the model native output-validation retries before the
        # caller's plain-text fallback engages.
        kwargs["output_type"] = PromptedOutput(ResponseEnvelope)
        kwargs["retries"] = 3

    return Agent(model, **kwargs)
