"""Intent classification and task understanding.

Cheap, deterministic first pass over the user's request: what is being
asked, how complex it is, which capabilities it needs, and where it is
ambiguous. The orchestrator consumes :class:`IntentResult` to decide the
execution strategy (direct / tools / research / orchestrated) and which
experts to involve — no LLM call is made here so routing stays fast and
works even when every provider is degraded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Intent(str, Enum):
    """What kind of work the request fundamentally is."""

    CHAT = "chat"                    # small talk, opinions, explanations
    LOOKUP = "lookup"                # a fact, price, definition — tools suffice
    RESEARCH = "research"            # multi-source investigation with citations
    CODE = "code"                    # write/review/debug/refactor code
    DATA = "data"                    # dataset analysis, queries, metrics
    WRITING = "writing"              # draft/rewrite/summarize content
    SYSTEM_OP = "system_op"          # deploy, infra, automation, troubleshooting
    MIXED = "mixed"                  # clearly spans several domains


class Complexity(str, Enum):
    SIMPLE = "simple"       # one model call, no decomposition needed
    MODERATE = "moderate"   # tools or one specialist
    COMPLEX = "complex"     # multi-step plan / multiple experts


@dataclass
class IntentResult:
    """The orchestrator's understanding of a request."""

    intent: Intent
    complexity: Complexity
    confidence: float                  # 0..1 in the classification
    needs_tools: bool
    needs_research: bool
    domains: list[str] = field(default_factory=list)   # expert domain hints
    ambiguities: list[str] = field(default_factory=list)
    rationale: str = ""


# ---------------------------------------------------------------- patterns

_EXPLICIT_RESEARCH_RE = re.compile(
    r"\b(deep[ -]?research|research(?:\s+(?:this|it|that|the))?|deep[ -]?dive|"
    r"in[- ]depth|investigate|investigation|thorough(?:ly)?\s+(?:research|study|analysis)|"
    r"comprehensive\s+(?:research|study|analysis)|fact[- ]check|literature\s+review)\b",
    re.I,
)
_LOOKS_CURRENT_RE = re.compile(
    r"\b(latest|current|today|now|recent|news|price(?:s|d)?|weather|stock|"
    r"who is|who's|ceo|president|version|rank(?:ing)?s?|forecast|score)\b",
    re.I,
)
_CODE_RE = re.compile(
    r"```|\b(code|function|class|def |import |bug|error|exception|stack ?trace|"
    r"traceback|refactor|review (?:this|my|the) code|fix (?:this|the) (?:code|bug)|"
    r"unit ?test|pytest|regex|sql query|api endpoint|typescript|javascript|"
    r"python|rust|golang|c\+\+)\b",
    re.I,
)
_DEBUG_RE = re.compile(r"\b(debug|stack ?trace|traceback|segfault|core dump|"
                       r"why (?:does|is) (?:this|it|my) (?:code|app).*(fail|crash|error))\b", re.I)
_REVIEW_RE = re.compile(r"\b(code review|review (?:this|my|the) (?:code|pr|pull request)|"
                        r"security (?:audit|review) of)\b", re.I)
_DATA_RE = re.compile(
    r"\b(dataset|csv|dataframe|sql|query|table schema|aggregate|metrics?|kpi|"
    r"chart|visuali[sz]e|plot|correlation|regression|statistics|percentile)\b",
    re.I,
)
_WRITE_RE = re.compile(
    r"\b(write|draft|rewrite|rephrase|paraphrase|summari[sz]e|tl;?dr|blog post|"
    r"article|essay|email|letter|documentation|readme|release notes|"
    r"presentation|slides?)\b",
    re.I,
)
_SYSOP_RE = re.compile(
    r"\b(deploy|deployment|ci/?cd|pipeline|docker|kubernetes|k8s|terraform|"
    r"nginx|server|infrastructure|monitoring|alert|incident|outage|"
    r"why is .+ (down|slow|failing)|troubleshoot)\b",
    re.I,
)
_MULTI_PART_RE = re.compile(
    r"(?:^|[.!?]\s+|\n)\s*(?:then|next|after (?:that|which)|finally|also)\b|"
    r"^\s*(?:\d+[.)]|[-*])\s+\S", re.I | re.M,
)
_AMBIGUITY_RE = re.compile(r"^(?:it|that|this|them|those|the thing)\b", re.I)
_COMPARE_RE = re.compile(r"\bvs\.?\b|\bversus\b|\bcompare\b|\bcomparison\b|\bpros and cons\b", re.I)


def _domains_for(text: str) -> list[str]:
    """Map request signals to expert-domain hints."""
    out: list[str] = []
    if _CODE_RE.search(text):
        out.append("engineering")
    if _DATA_RE.search(text):
        out.append("data")
    if _SYSOP_RE.search(text):
        out.append("operations")
    if _EXPLICIT_RESEARCH_RE.search(text) or _LOOKS_CURRENT_RE.search(text):
        out.append("research")
    if _WRITE_RE.search(text):
        out.append("content")
    return out


def classify(
    question: str,
    history: list[dict[str, Any]] | None = None,
    *,
    wants_tools: bool | None = None,
) -> IntentResult:
    """Classify ``question`` into an :class:`IntentResult`. Never raises.

    ``wants_tools`` lets the caller pass the existing tool-gate verdict so
    the classifier does not second-guess it.
    """
    q = (question or "").strip()
    if not q:
        return IntentResult(Intent.CHAT, Complexity.SIMPLE, 1.0, False, False)

    low = q.lower()
    domains = _domains_for(low)
    explicit_research = bool(_EXPLICIT_RESEARCH_RE.search(low))
    current = bool(_LOOKS_CURRENT_RE.search(low))
    compare = bool(_COMPARE_RE.search(low))
    code = bool(_CODE_RE.search(low))
    data = bool(_DATA_RE.search(low))
    sysop = bool(_SYSOP_RE.search(low))
    writing = bool(_WRITE_RE.search(low))
    parts = len([p for p in re.split(r"[.!?\n]+", q) if p.strip()])
    words = len(q.split())

    # Ambiguity signals: pronoun-led follow-ups with no resolvable context,
    # extremely short underspecified asks.
    ambiguities: list[str] = []
    if words <= 4 and not history:
        ambiguities.append("very short request with no prior context")
    if _AMBIGUITY_RE.search(q) and not history:
        ambiguities.append("references 'it/that/this' with no prior turn")
    if not ambiguities and words <= 2:
        ambiguities.append("possibly underspecified")

    # Intent decision — most specific signal wins; research beats lookup.
    votes: list[tuple[Intent, float]] = []
    if explicit_research:
        votes.append((Intent.RESEARCH, 0.95))
    elif current and (code or data):
        votes.append((Intent.RESEARCH, 0.6))
    if _DEBUG_RE.search(low):
        votes.append((Intent.CODE, 0.85))
    elif _REVIEW_RE.search(low):
        votes.append((Intent.CODE, 0.8))
    elif code:
        votes.append((Intent.CODE, 0.7))
    if data:
        votes.append((Intent.DATA, 0.7))
    if sysop:
        votes.append((Intent.SYSTEM_OP, 0.7))
    if writing and not code:
        votes.append((Intent.WRITING, 0.6))
    if current and not explicit_research:
        votes.append((Intent.LOOKUP, 0.65))
    if compare and not explicit_research:
        votes.append((Intent.LOOKUP, 0.6))

    if not votes:
        intent = Intent.CHAT
        confidence = 0.6
    else:
        # Highest-confidence vote wins; near-ties across domains -> MIXED.
        votes.sort(key=lambda v: -v[1])
        intent, confidence = votes[0]
        if len(votes) > 1 and votes[1][1] >= votes[0][1] - 0.05 \
                and votes[1][0] != intent and len(set(domains)) > 1:
            intent = Intent.MIXED

    # Complexity: multi-part structure, comparison, or mixed domains raise it.
    multi = parts >= 3 or bool(_MULTI_PART_RE.search(q)) or len(set(domains)) > 1
    if intent is Intent.CHAT and words < 25 and not multi:
        complexity = Complexity.SIMPLE
    elif explicit_research or compare or (multi and words > 30):
        complexity = Complexity.COMPLEX
    else:
        complexity = Complexity.MODERATE

    needs_tools = bool(wants_tools) if wants_tools is not None else current
    needs_research = explicit_research

    rationale = (
        f"intent={intent.value} complexity={complexity.value} "
        f"domains={domains or '-'} signals="
        f"{{research:{explicit_research},current:{current},code:{code},"
        f"data:{data},sysop:{sysop},writing:{writing},compare:{compare}}}"
    )
    return IntentResult(
        intent=intent,
        complexity=complexity,
        confidence=round(confidence, 2),
        needs_tools=needs_tools,
        needs_research=needs_research,
        domains=domains,
        ambiguities=ambiguities,
        rationale=rationale,
    )
