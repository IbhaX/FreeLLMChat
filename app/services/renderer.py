"""Component renderer agent — the dedicated converter to Nova components.

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

log = logging.getLogger("freellm.renderer")

# Cheap/fast model for formatting work; override with RENDERER_MODEL.
RENDERER_MODEL = os.environ.get("RENDERER_MODEL", "auto:fast")

CONVERTER_PREAMBLE = """You are Nova's component renderer. Your ONLY job is presentation: convert the answer you are given into the component format below. Do not add facts, do not drop substance, do not invent data. Preserve every number, name, and claim from the source answer; restructure it into components and keep the explanatory prose in `content` (markdown, headings allowed). Follow the RESPONSE DEPTH rules: the converted answer must be at least as thorough as the source — never compress it into a summary.

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

    Returns the fenced envelope text when the renderer produced a valid
    component response, else None (caller falls back to the deterministic
    markdown converter). Never raises.
    """
    try:
        # Lazy import: component_enforce imports this module for its repair
        # step, so a top-level import here would be circular.
        from app.services.component_enforce import (
            _message_content,
            has_renderable_components,
        )

        if not isinstance(content, str) or not content.strip():
            return None
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": RENDERER_SYSTEM}
        ]
        for m in (history or [])[-12:]:
            if isinstance(m, dict) and m.get("role") in ("user", "assistant"):
                messages.append({"role": m["role"], "content": str(m.get("content") or "")})
        messages.append({"role": "assistant", "content": content})
        messages.append({"role": "user", "content": CONVERT_INSTRUCTION})
        payload = {
            "model": model or renderer_model(),
            "messages": messages,
            "stream": False,
            "temperature": 0,
            "max_tokens": 4096,
        }
        result, _p, _k, _m = await gateway.chat(
            session, payload, attempt_timeout=attempt_timeout
        )
        text = _message_content(result or {})
        if has_renderable_components(text):
            return text.strip()
        log.info("renderer produced no components (model=%s)", payload["model"])
        return None
    except Exception as exc:  # rendering is best-effort; caller falls back
        log.debug("component renderer call failed: %s", exc)
        return None

