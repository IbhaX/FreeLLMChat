"""Dedicated component-renderer agent (native pydantic-ai).

One job: take arbitrary output — a plain-markdown answer, research
findings, tool results, another model's prose — and produce a validated
``nova-response`` envelope the frontend can render. Content agents own the
*answer*; this agent owns *presentation*.

It replaces the raw gateway call inside ``services.renderer`` while keeping
the same contract: try the configured cheap model, then each fallback in
``stage_models``, returning the fenced envelope on the first valid result.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.agents.deps import AgentDeps
from app.agents.factory import build_simple_agent
from app.response_protocol import validate_envelope
from app.services.renderer import (
    CONVERT_INSTRUCTION,
    RENDERER_SYSTEM,
    renderer_model,
)

log = logging.getLogger("nova.renderer")


def _prompt(content: str, history: list[dict[str, Any]] | None) -> str:
    """Prompt = prior context (compact) + the answer to convert."""
    ctx = ""
    turns = [
        m for m in (history or [])[-8:]
        if isinstance(m, dict) and m.get("role") in ("user", "assistant")
    ]
    if turns:
        ctx = "Conversation so far:\n" + "\n".join(
            f"{m['role']}: {str(m.get('content') or '')[:500]}"
            for m in turns) + "\n\n"
    return (ctx + "Answer to convert (verbatim — preserve every fact, name "
            "and number):\n\n" + content + "\n\n" + CONVERT_INSTRUCTION)


async def render_envelope(
    gateway: Any,
    content: str,
    *,
    model: str | None = None,
    history: list[dict[str, Any]] | None = None,
    emit: Any = None,
    attempt_timeout: float | None = None,
) -> str | None:
    """Render ``content`` into a ``nova-response`` fence via the renderer
    agent, iterating the stage-model fallbacks. The model emits the fenced
    envelope verbatim (its system prompt teaches the wire format); the
    agent then validates it against ``ResponseEnvelope`` — the model's raw
    text is returned so nothing is lost to re-serialization. Returns
    ``None`` when no model produced a valid envelope — callers fall back
    deterministically.
    """
    if not isinstance(content, str) or not content.strip():
        return None
    from app.services.component_enforce import extract_envelope
    from app.services.model_policy import stage_models

    for cand in stage_models(model or renderer_model(), role="fast"):
        try:
            deps = AgentDeps(gateway, emit=emit)
            agent = build_simple_agent(
                cand,
                RENDERER_SYSTEM,
                deps,
                tools=[],
                temperature=0,
                retries=2,
            )
            run = agent.run(_prompt(content, history), deps=deps)
            res = await (asyncio.wait_for(run, attempt_timeout)
                         if attempt_timeout else run)
            text = res.output if isinstance(res.output, str) else ""
            env = extract_envelope(text)
            if env and env.get("components"):
                validate_envelope(env)  # raises on schema violations
                return text.strip()
            log.info("renderer produced no components (model=%s)", cand)
        except Exception as exc:
            log.debug("renderer agent failed (model=%s): %s", cand, exc)
    return None
