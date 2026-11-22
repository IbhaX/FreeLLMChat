"""Person-profile synthesizer agent.

Takes the raw candidate list produced by ``lookup_people`` (Wikipedia,
GitHub and web/social search listings) and fuses it into one judgment:
which profiles likely describe the same person, a one-line synthesized
identity and the agent's confidence. Discovery stays deterministic in
``app.services.person`` — this agent only *synthesizes* the data it is
handed; it never fetches and never invents facts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

from app.agents.deps import AgentDeps
from app.agents.factory import build_simple_agent
from app.agents.schemas import PersonSynthesis

log = logging.getLogger("nova.synthesizer")

SYNTHESIZER_MODEL = os.environ.get("SYNTHESIZER_MODEL", "auto")
_SYNTH_TIMEOUT_S = 45.0

_SYSTEM = """You are IbhaX's identity synthesizer. You are given a queried name \
and a JSON list of public profile candidates found across the web (GitHub, \
LinkedIn, Wikipedia, social search listings). Fuse them into ONE \
assessment and reply with ONLY a JSON object (no prose, no markdown \
fences) of this shape:

{"summary": "...", "clusters": [{"label": "...", "urls": ["..."], \
"confidence": "low|medium|high"}], "confidence": "low|medium|high", \
"notes": "..."}

- summary: one or two sentences synthesizing who this name most likely \
refers to based on the candidates (occupation, location, platforms) — or \
stating that the name matches several distinct people.
- clusters: group the candidates that likely describe the SAME person; \
each cluster gets a short label, the profile URLs it covers, and its own \
confidence. Candidates that look like different people belong in \
different clusters.
- confidence: overall confidence that the synthesis identifies the person.
- notes: anything important (e.g. common name, thin data).

Judge ONLY from the data given — never invent employers, links, handles or \
facts that are not in the candidates. When evidence is thin, lower the \
confidence instead of guessing."""

_MAX_CANDIDATES = 12
_JSON_RE = re.compile(r"\{.*\}", re.S)


def _parse(text: str) -> PersonSynthesis | None:
    """Extract the JSON object from the model's reply and validate it."""
    m = _JSON_RE.search(text or "")
    if not m:
        return None
    try:
        synth = PersonSynthesis.model_validate(json.loads(m.group(0)))
    except Exception:
        return None
    return synth if (synth.summary or synth.clusters) else None


def _candidates(people: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Compact projection of the candidate list for the prompt."""
    out: list[dict[str, str]] = []
    for p in people[:_MAX_CANDIDATES]:
        if not isinstance(p, dict):
            continue
        out.append({
            "name": str(p.get("name") or ""),
            "source": str(p.get("source") or ""),
            "url": str(p.get("url") or ""),
            "description": str(p.get("description") or "")[:200],
        })
    return out


async def synthesize_person(
    name: str,
    people: list[dict[str, Any]],
    gateway: Any,
    *,
    emit: Any = None,
    model: str | None = None,
    timeout: float = _SYNTH_TIMEOUT_S,
) -> PersonSynthesis | None:
    """Fuse profile candidates into a :class:`PersonSynthesis`.

    Best-effort: returns ``None`` on any failure so callers can render the
    plain carousel. The model only judges over supplied data — it has no
    tools — so a synthesis can never invent a profile that wasn't found.
    """
    if not name or len(people) < 2 or gateway is None:
        return None
    try:
        deps = AgentDeps(gateway, emit=emit)
        agent = build_simple_agent(
            model or SYNTHESIZER_MODEL,
            _SYSTEM,
            deps,
            tools=[],
            temperature=0,
            retries=1,
        )
        prompt = (
            f"Name: {name}\n"
            f"Candidates (JSON): "
            f"{json.dumps(_candidates(people), ensure_ascii=False)}"
        )
        result = await asyncio.wait_for(
            agent.run(prompt, deps=deps), timeout)
        return _parse(result.output if isinstance(result.output, str) else "")
    except Exception as exc:
        log.debug("person synthesis failed for %r: %s", name, exc)
    return None
