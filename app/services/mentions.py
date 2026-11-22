"""@mention resolution — ``@agent``, ``@skill`` and ``@tool`` invocations.

A message may reference registered entities inline: ``@backend_developer``
adopts that agent's persona and model, ``@web_research`` grafts the skill's
instruction block, and ``@exa_search <query>`` runs the tool immediately so
its output is injected as context. Resolution order per token is
agent → skill → tool, so a name collision resolves to the most specific
kind.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from app.models import AgentConfig
from app.services import skills as skills_mod
from app.services.tools import TOOLS, run_tool

_MENTION_RE = re.compile(r"@(?:(agent|skill|tool)s?:)?([A-Za-z][\w-]*)")

_MAX_ARG = 2000      # chars of trailing text fed to one tool
_MAX_OUT = 4000      # chars of tool output injected into the prompt


@dataclass
class Resolved:
    """Entities mentioned in one user message, in mention order."""

    agents: list[AgentConfig] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    tools: list[tuple[str, str]] = field(default_factory=list)  # (name, arg)

    @property
    def any(self) -> bool:
        return bool(self.agents or self.skills or self.tools)


def _arg_text(text: str, mention: str) -> str:
    """Text following the full ``@mention`` (including any ``kind:`` prefix)
    up to the next mention or end — the natural-language argument for
    ``@tool`` invocations."""
    start = text.find(mention)
    if start < 0:
        return ""
    rest = text[start + len(mention):]
    nxt = _MENTION_RE.search(rest)
    return (rest[:nxt.start()] if nxt else rest).strip()[:_MAX_ARG]


async def resolve(session: Any, text: str) -> Resolved:
    """Classify each ``@token`` against agents, skills, then tools.

    ``@name`` resolves in order agent → skill → tool; an explicit
    ``@agent:name`` / ``@skill:name`` / ``@tool:name`` prefix pins the
    token to that kind only."""
    out = Resolved()
    if "@" not in text:
        return out
    seen: set[str] = set()
    mentions: list[tuple[str, str, str]] = []  # (kind, name, raw "@...")
    for m in _MENTION_RE.finditer(text):
        kind, name, raw = (m.group(1) or "").rstrip("s"), m.group(2), m.group(0)
        if raw.lower() not in seen:
            seen.add(raw.lower())
            mentions.append((kind, name, raw))
    names = [n for k, n, _ in mentions
             if k in ("", "agent") and n not in TOOLS
             and not skills_mod.get_skill(n)]
    agent_by_name: dict[str, AgentConfig] = {}
    if names:
        rows = (await session.execute(
            select(AgentConfig).where(
                AgentConfig.name.in_(names), AgentConfig.enabled.is_(True))
        )).scalars().all()
        agent_by_name = {a.name: a for a in rows}
    for kind, name, raw in mentions:
        if kind == "agent":
            if (a := agent_by_name.get(name)) is not None:
                out.agents.append(a)
        elif kind == "skill":
            if skills_mod.get_skill(name):
                out.skills.append(name)
        elif kind == "tool":
            if name in TOOLS:
                out.tools.append((name, _arg_text(text, raw)))
        else:
            if (a := agent_by_name.get(name)) is not None:
                out.agents.append(a)
            elif skills_mod.get_skill(name):
                out.skills.append(name)
            elif name in TOOLS:
                out.tools.append((name, _arg_text(text, raw)))
    return out


_AUTO_STOP = frozenset(
    "the a an and or of to in for on with is are was were be been do does did "
    "can could should would will shall may might must i you we they he she it "
    "this that these those my your our their me us him her them what which who "
    "how when where why not no yes so but if then than too very just about "
    "into over after before between under again more some any all each "
    "from".split())

# Name tokens shared across many tools/skills — a hit on these alone is weak
# evidence ('page' → notion_page, 'create' → jira_create_issue).
_WEAK_TOKS = frozenset(
    "create post send make list get new page issue issues search check read "
    "write run use set code file report data text message query repo repos "
    "result results generate".split())


def _query_terms(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z][a-z0-9]{2,}", text.lower())
            if w not in _AUTO_STOP]


def _score_entity(words: list[str], name: str, desc: str) -> tuple[int, bool]:
    """+3 per query term matching a distinctive name token (substring for
    hyphenated names) or the skill's trigger phrase (the 'Use this skill
    when…' opening ~60 chars of the description); +1 for shared/generic
    name tokens and per term appearing anywhere else in the description."""
    name_l = name.lower()
    desc_l = desc.lower()
    name_toks = [t for t in re.split(r"[^a-z0-9]+", name_l) if t]
    zone = desc_l[:60]
    hay = f"{name_l} {desc_l}"
    score, name_hit = 0, False
    for w in words:
        matched = [t for t in name_toks if w in t or t in w]
        if matched or (len(w) >= 4 and w in zone):
            name_hit = True
            weak = (bool(matched) and all(t in _WEAK_TOKS for t in matched)) \
                or (not matched and w in _WEAK_TOKS)
            score += 1 if weak else 3
        elif w in hay:
            score += 1
    return score, name_hit


def auto_select(text: str) -> dict[str, list[str]]:
    """Pick skills and tools that clearly match the query — the automatic
    counterpart of explicit ``@skill``/``@tool`` mentions.

    Skills need a name-token hit (they inject large instruction blocks, so
    the bar is higher); tools only need description relevance since they
    stay dormant until the model calls them. Returns ``{"skills": [...],
    "tools": [...]}`` capped at 2 and 4 respectively."""
    words = _query_terms(text)
    if len(words) < 2:
        return {"skills": [], "tools": []}
    scored_skills = sorted(
        (( *_score_entity(words, s.name, s.description or ""), s.name)
         for s in skills_mod.all_skills()),
        key=lambda x: -x[0])
    skills = [n for sc, hit, n in scored_skills[:2] if hit and sc >= 4]
    scored_tools = sorted(
        ((*_score_entity(words, t.name, t.description or ""), t.name)
         for t in TOOLS.values()),
        key=lambda x: -x[0])
    tools = [n for sc, hit, n in scored_tools[:4] if sc >= 4 or (hit and sc >= 3)]
    return {"skills": skills, "tools": tools}


async def run_tool_for(name: str, arg_text: str) -> str:
    """Run one tool with ``arg_text`` mapped into its first required string
    parameter (e.g. ``query`` for searches, ``expression`` for calculator).
    Output is capped so a big fetch cannot blow up the prompt."""
    t = TOOLS.get(name)
    if t is None:
        return f"error: unknown tool '{name}'"
    props = t.parameters.get("properties", {})
    required = t.parameters.get("required", [])
    args: dict[str, Any] = {}
    if arg_text:
        pick = next(
            (p for p in required
             if props.get(p, {}).get("type") == "string"), None
        ) or next(
            (p for p, s in props.items()
             if s.get("type") == "string"), None)
        if pick:
            args[pick] = arg_text
    out = await run_tool(name, args)
    return out[:_MAX_OUT] + ("\n…(truncated)" if len(out) > _MAX_OUT else "")
