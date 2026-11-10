"""Orchestrator — the single routing step for chat requests.

One classification decides which pipeline handles a message:

- ``direct``  — plain streaming, no tools (greetings, small talk)
- ``tools``   — the server-side tool-call loop (search, calc, media)
- ``research``— the deep-research pipeline (planner → researchers →
  gap-check → verifier → synthesizer), for deep multi-source questions

Routing is cheap and deterministic where possible: depth comes from
``research.classify_depth`` (rule-based, no LLM call) and the tools gate
from ``tools.wants_tools`` (one tiny classifier call, fail-open). The
orchestrator never raises — any failure falls back to the safest pipeline
(``tools`` when tools were requested, else ``direct``).
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("freellm.orchestrator")

# Depths that justify the full research fan-out. ``quick`` and ``standard``
# stay on the tool loop, which already does one search + answer cheaply.
RESEARCH_DEPTHS = {"deep"}


async def route_message(
    gateway: Any,
    session: Any,
    payload: dict[str, Any],
    *,
    last_user: str = "",
    history: list[dict[str, Any]] | None = None,
) -> str:
    """Classify a chat request into ``direct`` | ``tools`` | ``research``.

    ``payload`` is the OpenAI-style chat payload (may carry the
    ``builtin_tools`` extra field). Never raises.
    """
    try:
        builtin = bool(payload.get("builtin_tools"))
        if not builtin:
            return "direct"
        # Greetings/small talk skip tools entirely.
        from app.services.tools import wants_tools

        if not await wants_tools(gateway, session, payload):
            return "direct"
        # Deep, multi-source questions go to the research pipeline.
        from app.services.research import classify_depth

        if classify_depth(last_user, history) in RESEARCH_DEPTHS:
            return "research"
        return "tools"
    except Exception as exc:  # routing must never break chat
        log.debug("orchestrator routing failed, using safe default: %s", exc)
        return "tools" if payload.get("builtin_tools") else "direct"
