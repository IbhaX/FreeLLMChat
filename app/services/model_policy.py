"""Internal model policy — strong concrete models per role.

Internal pipeline stages (planner, researcher, verifier, synthesizer,
component renderer, expert calls, memory tasks) must not silently land on
the weakest model in the ``auto`` sweep — with local providers enabled,
``auto`` can resolve to a 1.5B model that produces confident fabrication.

Each *role* has an ordered preference list of concrete ``provider/model``
ids. ``auto`` is appended only as the last-resort backstop so a call can
still land somewhere when every preferred provider is cooled or down.

Overrides (comma-separated model ids):
- ``NOVA_MODEL_REASONING`` / ``NOVA_MODEL_GENERAL`` / ``NOVA_MODEL_CODING``
  / ``NOVA_MODEL_FAST`` — per-role preference lists
- ``NOVA_INTERNAL_MODEL`` — one override for all roles
"""

from __future__ import annotations

import os
from typing import Any

_PREFERRED: dict[str, tuple[str, ...]] = {
    # Complex planning, verification, final synthesis.
    "reasoning": (
        "groq/openai/gpt-oss-120b",
        "groq/llama-3.3-70b-versatile",
        "openrouter/openrouter/free",
    ),
    # General internal work (researcher, synthesist, default expert).
    "general": (
        "groq/llama-3.3-70b-versatile",
        "groq/openai/gpt-oss-120b",
        "openrouter/openrouter/free",
    ),
    # Code generation / review / debugging.
    "coding": (
        "groq/qwen/qwen3-32b",
        "groq/openai/gpt-oss-120b",
        "openrouter/openrouter/free",
    ),
    # Cheap formatting / classification / memory tasks.
    "fast": (
        "groq/openai/gpt-oss-20b",
        "gemini/gemini-flash-lite-latest",
        "openrouter/openrouter/free",
    ),
}

_ALIAS_ROLE = {
    "auto:coding": "coding",
    "auto:fast": "fast",
    "auto:reason": "reasoning",
    "auto:reasoning": "reasoning",
    "auto:smart": "reasoning",
}


def is_auto(model: str | None) -> bool:
    return bool(model) and str(model).startswith("auto")


def role_for_alias(model: str | None) -> str:
    return _ALIAS_ROLE.get(str(model or ""), "general")


def preferred_models(role: str) -> list[str]:
    """Ordered concrete model ids for ``role``.

    ``NOVA_MODEL_<ROLE>`` then ``NOVA_INTERNAL_MODEL`` env vars (comma
    separated) take precedence over the built-in preference list.
    """
    env = os.environ.get(f"NOVA_MODEL_{role.upper()}") or os.environ.get(
        "NOVA_INTERNAL_MODEL")
    if env:
        return [m.strip() for m in env.split(",") if m.strip()]
    return list(_PREFERRED.get(role) or _PREFERRED["general"])


def stage_models(requested: str | None, *, role: str = "general") -> list[str]:
    """Ordered model candidates for an internal call.

    - an explicit (non-auto) model choice is honored verbatim;
    - an ``auto*`` alias expands to the matching role's preference list;
    - a bare role name (``general``/``reasoning``/``coding``/``fast``)
      expands the same way;
    - ``auto`` is appended as the last-resort backstop unless the caller
      pinned an explicit model.
    """
    m = str(requested or "")
    if m and not is_auto(m) and m not in _PREFERRED:
        return [m]
    if m in _PREFERRED:
        r = m
    elif is_auto(m):
        r = _ALIAS_ROLE.get(m) or role  # bare "auto" -> caller's role
    else:
        r = role
    return preferred_models(r) + ["auto"]


async def chat_resp(gateway: Any, session: Any,
                    payload: dict[str, Any]) -> dict[str, Any]:
    """``Gateway.chat`` returns ``(resp, provider, key, model)`` — internal
    helpers only need the completion dict."""
    resp, _p, _k, _m = await gateway.chat(session, payload)
    return resp


async def internal_chat(
    gateway: Any, session: Any, payload: dict[str, Any],
    *, requested: str | None = None, role: str = "general",
    retries: int = 1, emit: Any | None = None, label: str = "",
) -> Any:
    """Reliable completion across the ordered candidate list.

    Strong concrete models are tried first (each with ``retries`` bounded
    retries); ``auto`` is appended as the last-resort backstop unless an
    explicit model was pinned. Returns the last ``CallResult`` — check
    ``.ok`` — never raises."""
    from app.services.reliable import reliable_call

    last = None
    for cand in stage_models(requested, role=role):
        p = {**payload, "model": cand}
        last = await reliable_call(
            lambda p=p: chat_resp(gateway, session, p),
            retries=retries, emit=emit, label=label or f"chat:{cand}")
        if last.ok:
            return last
    return last
