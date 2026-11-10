"""Orchestrator — the single routing step for chat requests.

One classification decides which pipeline handles a message:

- ``direct``  — plain streaming, no tools (greetings, small talk)
- ``tools``   — the server-side tool-call loop (search, calc, media)
- ``research``— the deep-research pipeline (planner → researchers →
  gap-check → verifier → synthesizer), for deep multi-source questions

Routing is cheap: the research pipeline runs only on explicit research
intent ("research", "deep dive", "investigate", … — see
``_EXPLICIT_RESEARCH_RE``); everything else stays on the tool loop, which
already answers comparisons and lookups cheaply. ``research.classify_depth``
still picks quick/standard/deep once research is chosen. The tools gate is
``tools.wants_tools`` (one tiny classifier call, fail-open). The
orchestrator never raises — any failure falls back to the safest pipeline
(``tools`` when tools were requested, else ``direct``).
"""

from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger("freellm.orchestrator")

# The research pipeline only runs on explicit intent — "compare X vs Y" or
# long questions alone are NOT enough (they stay on the cheap tool loop).
# ``classify_depth`` still picks quick/standard/deep once research is chosen.
_EXPLICIT_RESEARCH_RE = re.compile(
    r"\b(deep[ -]?research|research(?:\s+(?:this|it|that|the))?|deep[ -]?dive|"
    r"in[- ]depth|investigate|investigation|thorough(?:ly)?\s+(?:research|study|analysis)|"
    r"comprehensive\s+(?:research|study|analysis)|fact[- ]check|literature\s+review)\b",
    re.I,
)


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
        # Only explicit research intent goes to the research pipeline.
        if _EXPLICIT_RESEARCH_RE.search(last_user or ""):
            return "research"
        return "tools"
    except Exception as exc:  # routing must never break chat
        log.debug("orchestrator routing failed, using safe default: %s", exc)
        return "tools" if payload.get("builtin_tools") else "direct"
