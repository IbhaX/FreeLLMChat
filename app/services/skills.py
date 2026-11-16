"""Skill registry — reusable capability modules agents can attach.

A skill is a named, versioned bundle of instructions (and any tools it
requires) that can be grafted onto any agent's system prompt. Agents own
*reasoning and role*; skills own *reusable method* — search strategy,
verification checklists, report formats. Skills are not tied to a single
agent and are discovered through this registry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SkillDef:
    """A reusable capability module."""

    name: str
    version: str
    description: str
    instructions: str              # injected into the agent system prompt
    requires_tools: tuple[str, ...] = ()   # tools the skill needs to work
    domains: tuple[str, ...] = ("*",)      # compatible expert domains


SKILLS: tuple[SkillDef, ...] = (
    SkillDef(
        name="web_research",
        version="1.0",
        description="Systematic web search and retrieval method.",
        requires_tools=("web_search", "http_get"),
        domains=("research", "operations", "engineering"),
        instructions=(
            "## SKILL: Web research\n"
            "- Decompose the need into 2-4 distinct short queries; never repeat a phrasing.\n"
            "- Open (fetch) the top result pages — snippets are leads, not evidence.\n"
            "- Prefer primary/official sources; record title + URL for everything used.\n"
            "- Stop when the question is answered or after the tool budget; report what was not found."
        ),
    ),
    SkillDef(
        name="verification",
        version="1.0",
        description="Claim verification and contradiction handling.",
        requires_tools=("http_get",),
        domains=("research",),
        instructions=(
            "## SKILL: Verification\n"
            "- Every factual claim needs a source; grade each source (primary/secondary/tertiary).\n"
            "- Cross-check the load-bearing claims against a second independent source.\n"
            "- Mark claims as verified / partially verified / unverified — never blur them.\n"
            "- Present contradictions with the strongest case each side has."
        ),
    ),
    SkillDef(
        name="structured_report",
        version="1.0",
        description="Citation-ready report structure with explicit confidence.",
        domains=("*",),
        instructions=(
            "## SKILL: Structured reporting\n"
            "- Open with the direct answer, then evidence.\n"
            "- Cite claims as [n] tied to the source list.\n"
            "- Separate facts, inferences (label them), and recommendations.\n"
            "- End with confidence level and explicit limitations."
        ),
    ),
    SkillDef(
        name="code_review",
        version="1.0",
        description="Priority-ordered constructive review checklist.",
        domains=("engineering",),
        instructions=(
            "## SKILL: Code review\n"
            "- Priority order: correctness > security > maintainability > performance > tests > style.\n"
            "- Every finding: location, why it matters, concrete suggestion.\n"
            "- Tag findings BLOCKER / SUGGESTION / NIT.\n"
            "- Call out what the code does well."
        ),
    ),
    SkillDef(
        name="architecture_review",
        version="1.0",
        description="Architecture evaluation method (trade-offs, failure modes).",
        domains=("engineering",),
        instructions=(
            "## SKILL: Architecture review\n"
            "- Evaluate against stated scale, team, and ops constraints — not abstract best practices.\n"
            "- Name the failure mode each component introduces.\n"
            "- Data flow, consistency boundaries, and blast radius before diagrams.\n"
            "- Prefer fewer moving parts; justify each one."
        ),
    ),
    SkillDef(
        name="data_storytelling",
        version="1.0",
        description="Turn analysis into the right visual + takeaway narrative.",
        domains=("data", "research"),
        instructions=(
            "## SKILL: Data storytelling\n"
            "- Match chart to comparison: trend->line, parts-of-whole->donut, ranking->bar, distribution->histogram.\n"
            "- Title states the finding, not the metric ('Revenue grew 40%' not 'Revenue chart').\n"
            "- Support visuals with the underlying numbers (table or stat_grid).\n"
            "- Flag outliers and small-sample noise explicitly."
        ),
    ),
    SkillDef(
        name="sql_tuning",
        version="1.0",
        description="Query correctness, NULL semantics, and performance review.",
        domains=("data", "engineering"),
        instructions=(
            "## SKILL: SQL tuning\n"
            "- Check NULL semantics (joins, NOT IN, aggregates) — they change results silently.\n"
            "- Prefer set operations over row-by-row constructs.\n"
            "- Consider index usage for WHERE/JOIN columns on large tables.\n"
            "- Verify the query shape against the expected result, not just that it runs."
        ),
    ),
    SkillDef(
        name="security_review",
        version="1.0",
        description="OWASP-aligned vulnerability review method.",
        domains=("operations", "engineering"),
        instructions=(
            "## SKILL: Security review\n"
            "- Check: injection, auth/authz gaps, SSRF, secrets handling, input validation, dependency risk.\n"
            "- Rate severity by exploitability x blast radius — not by category.\n"
            "- Give concrete minimal fixes; note what could not be assessed.\n"
            "- Flag missing controls (rate limits, audit logs) as findings too."
        ),
    ),
    SkillDef(
        name="incident_triage",
        version="1.0",
        description="Mitigate-first operational incident method.",
        domains=("operations",),
        instructions=(
            "## SKILL: Incident triage\n"
            "- First: impact + blast radius + the fastest SAFE mitigation.\n"
            "- Build a timeline (symptoms, deploys, config changes) before root-cause theories.\n"
            "- Separate known facts from hypotheses.\n"
            "- Every remediation step gets a verification step and a rollback note."
        ),
    ),
)

_BY_NAME: dict[str, SkillDef] = {s.name: s for s in SKILLS}
_DYNAMIC: dict[str, SkillDef] = {}


def register_dynamic(name: str, version: str, description: str,
                     instructions: str, tools: tuple[str, ...] = ()) -> None:
    """Register a user-defined skill at runtime (overwrites same name)."""
    _DYNAMIC[name] = SkillDef(
        name=name, version=version, description=description,
        instructions=instructions, requires_tools=tools)


def unregister_dynamic(name: str) -> None:
    _DYNAMIC.pop(name, None)


async def load_custom_skills(session: Any) -> int:
    """Refresh the dynamic registry from the custom_skills table."""
    from sqlalchemy import select
    from app.models import CustomSkill
    _DYNAMIC.clear()
    rows = (await session.execute(
        select(CustomSkill).where(CustomSkill.enabled.is_(True)))
    ).scalars().all()
    for r in rows:
        register_dynamic(r.name, str(r.version), r.description,
                         r.instructions, tuple(r.tools or ()))
    return len(_DYNAMIC)


def _lookup(name: str) -> SkillDef | None:
    # user overrides shadow built-ins with the same name
    return _DYNAMIC.get(name) or _BY_NAME.get(name)


def all_skills() -> list[SkillDef]:
    return [s for s in SKILLS if s.name not in _DYNAMIC] \
        + list(_DYNAMIC.values())


def get_skill(name: str) -> SkillDef | None:
    return _lookup(name)


def skills_for(expert: Any) -> list[SkillDef]:
    """Resolve an expert's skill names to defs (unknown names skipped)."""
    names = getattr(expert, "skills", ()) or ()
    return [s for n in names if (s := _lookup(n))]


def attach_instructions(
    base_prompt: str, skill_names: tuple[str, ...] | list[str]
) -> str:
    """Append skill instruction blocks to a system prompt.

    Skills are additive and domain-agnostic at this layer — the caller
    decides compatibility (the expert registry already declares it).
    """
    blocks = [
        s.instructions for n in skill_names if (s := _lookup(n))
    ]
    if not blocks:
        return base_prompt
    return base_prompt.rstrip() + "\n\n" + "\n\n".join(blocks)


def required_tools(skill_names: tuple[str, ...] | list[str]) -> list[str]:
    """Union of tools the given skills require."""
    out: list[str] = []
    for n in skill_names:
        s = _lookup(n)
        if s:
            for t in s.requires_tools:
                if t not in out:
                    out.append(t)
    return out


def describe() -> str:
    """Registry listing for the planner/UI."""
    return "\n".join(
        f"- {s.name} v{s.version}: {s.description}" for s in all_skills()
    )
