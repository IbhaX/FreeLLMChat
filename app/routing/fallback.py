"""Model resolution: map a requested model to an ordered candidate list
of (provider, provider_model) pairs."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Provider
from app.providers.registry import load_fallback_chains


async def resolve_candidates(
    session: AsyncSession,
    requested: str,
    available: dict[str, tuple[Provider, list[str]]],
) -> list[tuple[Provider, str]]:
    """Return ordered (provider, model) candidates.

    `available` maps provider name -> (Provider, known model ids).
    """
    if requested.startswith("auto"):
        chain = load_fallback_chains().get(requested) or load_fallback_chains().get(
            "auto", []
        )
        out: list[tuple[Provider, str]] = []
        for name in chain:
            if name not in available:
                continue
            provider, models = available[name]
            for m in models:
                out.append((provider, m))
        return out

    if "/" in requested:
        pname, model = requested.split("/", 1)
        if pname in available:
            return [(available[pname][0], model)]
        # Not a known provider prefix — treat whole string as model id below.

    # Bare model id: every provider that serves it, seed order.
    out = [
        (provider, requested)
        for provider, models in available.values()
        if not models or requested in models
    ]
    return out
