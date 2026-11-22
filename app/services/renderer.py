"""Component renderer agent — the dedicated converter to IbhaX components.

One job: take arbitrary output (a plain-markdown answer, research findings,
tool results, another model's prose) and produce a validated
``nova-response`` envelope the frontend can render. Research and chat agents
are responsible for the *content*; this agent owns *presentation*.

It runs on a cheap/fast model by default (``RENDERER_MODEL``, default
``auto:fast``) at temperature 0 — formatting is not a reasoning task — and is
only invoked when the primary answer failed to produce components itself, so
the streaming path (inline envelopes) is untouched.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from app.response_protocol import structured_system_prompt

log = logging.getLogger("nova.renderer")

# Cheap/fast model for formatting work; override with RENDERER_MODEL.
RENDERER_MODEL = os.environ.get("RENDERER_MODEL", "auto:fast")

CONVERTER_PREAMBLE = """You are IbhaX's component renderer. Your ONLY job is presentation: convert the answer you are given into the component format below. Do not add facts, do not drop substance, do not invent data. Preserve every number, name, and claim from the source answer; restructure it into components and express all prose as `heading` + `paragraph` components (keep `content` empty or a one-line lead). Follow the RESPONSE DEPTH rules: the converted answer must be at least as thorough as the source — never compress it into a summary.

"""

RENDERER_SYSTEM = CONVERTER_PREAMBLE + structured_system_prompt()

# Instruction prefix kept stable: component_enforce's repair flow and its
# tests key off this wording.
CONVERT_INSTRUCTION = (
    "Rewrite your previous answer using the component format. "
    "Output only components."
)


def renderer_model() -> str:
    return os.environ.get("RENDERER_MODEL", RENDERER_MODEL)


async def convert_to_components(
    gateway: Any,
    session: Any,
    content: str,
    *,
    history: list[dict[str, Any]] | None = None,
    model: str | None = None,
    attempt_timeout: float | None = None,
) -> str | None:
    """Convert ``content`` into a ``nova-response`` fence.

    Thin wrapper over the dedicated renderer agent
    (``app.agents.renderer.render_envelope``): the agent produces a
    validated ``ResponseEnvelope`` through PromptedOutput with native
    output retries; this function serializes it back to the fence text the
    callers resplice. Returns None when no candidate model produced a
    valid envelope (caller falls back to the deterministic markdown
    converter). Never raises.
    """
    try:
        from app.agents.renderer import render_envelope

        return await render_envelope(
            gateway, content, model=model, history=history,
            attempt_timeout=attempt_timeout)
    except Exception as exc:  # rendering is best-effort; caller falls back
        log.debug("component renderer call failed: %s", exc)
        return None

