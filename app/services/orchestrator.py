"""Orchestrator — routing + the multi-agent coordination entry point.

Two responsibilities:

1. ``route_message`` — the single routing step for chat requests:
   ``direct`` | ``tools`` | ``research`` | ``orchestrate``. Intent-aware
   (``intent.classify``) and fail-open — routing errors fall back to the
   safest pipeline.

2. ``run_orchestrated`` — the multi-agent path for complex requests:
   plan → delegate to experts → execute with dependencies → synthesize.
   Implemented in ``execution.py``; this module is the interface.

Simple tasks never pay the orchestration cost: a single-tool answerable
question stays on the tool loop, and only explicit/multi-domain/complex
requests reach ``orchestrate``.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.services.intent import Complexity, Intent, IntentResult, classify

log = logging.getLogger("freellm.orchestrator")


async def classify_request(
    gateway: Any,
    session: Any,
    payload: dict[str, Any],
    *,
    last_user: str = "",
    history: list[dict[str, Any]] | None = None,
) -> IntentResult:
    """Full intent understanding for a request. Never raises."""
    try:
        wants: bool | None = None
        if payload.get("builtin_tools"):
            try:
                from app.services.tools import wants_tools
                wants = await wants_tools(gateway, session, payload)
            except Exception:
                wants = True  # fail-open: tools were explicitly requested
        return classify(last_user, history, wants_tools=wants)
    except Exception as exc:
        log.debug("intent classification failed, using safe default: %s", exc)
        return IntentResult(Intent.CHAT, Complexity.MODERATE, 0.0,
                            bool(payload.get("builtin_tools")), False)


async def route_message(
    gateway: Any,
    session: Any,
    payload: dict[str, Any],
    *,
    last_user: str = "",
    history: list[dict[str, Any]] | None = None,
) -> str:
    """Classify a chat request into a pipeline name. Never raises.

    Returns ``direct`` | ``tools`` | ``research`` | ``orchestrate``.
    """
    try:
        builtin = bool(payload.get("builtin_tools"))
        ir = await classify_request(
            gateway, session, payload,
            last_user=last_user, history=history)

        if not builtin:
            return "direct"
        if ir.intent is Intent.CHAT and ir.complexity is Complexity.SIMPLE \
                and not ir.needs_tools:
            return "direct"
        # Orchestration only for genuinely complex work.
        if ir.complexity is Complexity.COMPLEX and ir.intent in (
            Intent.MIXED, Intent.RESEARCH
        ) and not ir.needs_research:
            # MIXED+complex without explicit research intent — the
            # multi-agent path is still heavier than tool loop; use it
            # only when multiple expert domains are actually involved.
            if len(set(ir.domains)) > 1:
                return "orchestrate"
        if ir.needs_research:
            return "research"
        return "tools"
    except Exception as exc:  # routing must never break chat
        log.debug("orchestrator routing failed, using safe default: %s", exc)
        return "tools" if payload.get("builtin_tools") else "direct"


async def run_orchestrated(
    gateway: Any,
    session: Any,
    question: str,
    *,
    payload: dict[str, Any] | None = None,
    history: list[dict[str, Any]] | None = None,
    emit: Any | None = None,
) -> Any:
    """Run the multi-agent orchestrated pipeline for ``question``.

    Returns an ``OrchestrationResult`` — a controlled terminal state,
    never an exception.
    """
    from app.services.execution import orchestrate

    intent = await classify_request(
        gateway, session, payload or {},
        last_user=question, history=history)
    # Durable agent ledger: record the run header + every traced step.
    from app.services import agent_log
    factory = None
    try:
        from app.db import get_session_factory
        factory = get_session_factory()
    except Exception:
        factory = None
    run_id = None
    t0 = time.monotonic()
    if factory:
        async with factory() as s:
            run_id = await agent_log.start_run(
                s, "orchestrate", question, model=str((payload or {}).get("model") or "auto"),
                meta={"intent": intent.intent.value if intent else ""})
            await s.commit()
    tapped = agent_log.tap_emit(run_id, emit) if run_id else emit
    result = await orchestrate(
        gateway, session, question,
        intent=intent, history=history,
        model=str((payload or {}).get("model") or "auto"),
        emit=tapped)
    if run_id and factory:
        async with factory() as s:
            await agent_log.persist_events(run_id, result.trace)
            await agent_log.finish_run(
                s, run_id, "degraded" if result.degraded else "done",
                meta={"intent": result.intent, "strategy": result.strategy,
                      "steps": len(result.steps)},
                duration_ms=int((time.monotonic() - t0) * 1000))
            await s.commit()
    return result
