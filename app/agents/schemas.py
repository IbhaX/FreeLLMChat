"""Typed contracts for agent I/O and inter-agent communication.

``ResponseEnvelope`` (the nova-response contract) stays in
``app.response_protocol`` — these models cover the orchestration layer:
plans, delegation payloads and run metadata exchanged *between* agents.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class AgentReply(BaseModel):
    """Validated assistant output for plain (non-envelope) agents."""

    text: str
    model_used: str | None = None


class PlanTask(BaseModel):
    """One unit of delegated work in a research/orchestration plan."""

    id: str
    question: str
    rationale: str = ""
    tools: list[str] = Field(default_factory=list)


class ResearchPlan(BaseModel):
    """Planner output → researcher inputs (inter-agent payload)."""

    subject: str
    tasks: list[PlanTask] = Field(default_factory=list, max_length=8)


class ResearchFinding(BaseModel):
    """One researcher's structured result consumed by the synthesist."""

    task_id: str
    answer: str
    sources: list[dict[str, str]] = Field(default_factory=list)
    confidence: str = "medium"


class SynthesisResult(BaseModel):
    """Final synthesis payload passed back to the API layer."""

    report: str
    findings: list[ResearchFinding] = Field(default_factory=list)
    sources: list[dict[str, str]] = Field(default_factory=list)


class RoleplayTurn(BaseModel):
    """Structured character-turn output inside roleplay orchestration."""

    speaker: str
    dialogue: str
    narration: str = ""
    emotion: str = ""
