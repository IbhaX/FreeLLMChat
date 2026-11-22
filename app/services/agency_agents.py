"""Agency-agents bundle — seeds the msitarzewski/agency-agents catalog.

``app/data/agency_agents.json`` is generated once by
``tools/build_agency_agents.py`` (GitHub codeload zip → frontmatter parse)
and seeded into ``agent_configs`` at boot so the ~270 specialists appear
in the Agents UI, the ``@`` mention resolver, and chat auto-routing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_BUNDLE = Path(__file__).resolve().parent.parent / "data" / "agency_agents.json"
_cache: list[dict[str, Any]] | None = None


def load_bundle() -> list[dict[str, Any]]:
    """Parsed agent catalog — cached after first read."""
    global _cache
    if _cache is None:
        _cache = json.loads(_BUNDLE.read_text(encoding="utf-8"))
    return _cache


def _to_agent_dict(a: dict[str, Any]) -> dict[str, Any]:
    """AgentConfig-compatible row for one catalog entry."""
    return {
        "name": a["name"],
        "description": f"{a.get('emoji') or '🤖'} {a['display_name']} — "
                       f"{a['description']}",
        "system_prompt": a["system_prompt"],
        "model": "auto",
        "enable_tools": True,
        "output_format": "text",
        "config": {
            "builtin": True,
            "source": "agency-agents",
            "category": a["category"],
            "emoji": a.get("emoji") or "🤖",
            "vibe": a.get("vibe") or "",
        },
    }


async def seed_agency_agents(session: Any) -> int:
    """Upsert the catalog into ``agent_configs`` (idempotent).

    Same contract as ``seed_experts``: rows are platform-managed
    (``config.builtin``) and refresh on boot unless the user edited them
    (``config.customized``). Returns rows written.
    """
    from sqlalchemy import select

    from app.models import AgentConfig

    existing = {
        r.name: r
        for r in (await session.execute(select(AgentConfig))).scalars().all()
    }
    n = 0
    for a in load_bundle():
        d = _to_agent_dict(a)
        row = existing.get(a["name"])
        if row is None:
            session.add(AgentConfig(**d))
            n += 1
        elif (row.config or {}).get("source") == "agency-agents" and not (
            row.config or {}
        ).get("customized"):
            row.description = d["description"]
            row.system_prompt = d["system_prompt"]
            row.enable_tools = d["enable_tools"]
            row.output_format = d["output_format"]
            row.config = d["config"]
            n += 1
    await session.commit()
    return n
