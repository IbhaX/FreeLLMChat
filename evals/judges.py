"""Deterministic judges for the research eval harness (spec §16.2).

These metrics need no LLM. They are lower-bound proxies — a fact counted
only when its literal text (or an ``a|b`` alternative) appears in the
report — so they under-count paraphrased coverage. An optional LLM judge
can layer on top later via ``run_eval --judge-model``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

_CITATION_RE = re.compile(r"\[\d+\]")
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _fact_present(text: str, fact: str) -> bool:
    """A required fact matches when any ``|``-separated alternative's words
    all appear in the text (order-free, literal)."""
    hay = _norm(text)
    for alt in fact.split("|"):
        words = [w for w in _norm(alt).split() if len(w) > 2]
        if words and all(w in hay for w in words):
            return True
    return False


def coverage(report_text: str, required_facts: list[str]) -> tuple[float, list[str]]:
    """Fraction of required facts present; returns (score, missing)."""
    if not required_facts:
        return 1.0, []
    missing = [f for f in required_facts if not _fact_present(report_text, f)]
    return round(1 - len(missing) / len(required_facts), 4), missing


def citation_recall(report_text: str) -> float:
    """Fraction of factual-looking sentences that carry a [n] citation."""
    sentences = [s for s in _SENT_SPLIT.split(report_text or "")
                 if len(s) > 40]
    factual = [s for s in sentences
               if re.search(r"\d|\b(is|are|was|were|has|have)\b", s)]
    if not factual:
        return 0.0
    cited = sum(1 for s in factual if _CITATION_RE.search(s))
    return round(cited / len(factual), 4)


def claim_quote_precision(
    claims: list[dict[str, Any]],
    links: dict[str, list[dict[str, Any]]],
) -> tuple[float, list[str]]:
    """Deterministic entailment proxy: for each claim with 'supports' links,
    check that the claim's numbers and long tokens appear in at least one
    linked quote. Returns (precision, unsupported claim ids)."""
    checked: list[tuple[str, bool]] = []
    for claim in claims:
        evidences = links.get(claim.get("id", ""), [])
        supports = [e for e in evidences if e.get("relation") == "supports"]
        if not supports:
            continue
        text = _norm(claim.get("text", ""))
        tokens = {t for t in re.findall(r"[a-z][a-z\-]{4,}|\d[\d.,%]*", text)}
        quotes = _norm(" ".join(e.get("quote", "") for e in supports))
        ok = bool(tokens) and sum(1 for t in tokens if t in quotes) >= \
            max(1, len(tokens) // 2)
        checked.append((claim.get("id", ""), ok))
    if not checked:
        return 0.0, []
    bad = [cid for cid, ok in checked if not ok]
    return round(1 - len(bad) / len(checked), 4), bad


@dataclass
class EvalScores:
    question_id: str
    coverage: float
    missing_facts: list[str] = field(default_factory=list)
    citation_recall: float = 0.0
    claim_quote_precision: Optional[float] = None
    wall_s: float = 0.0
    state: str = ""


def score_run(
    question_id: str,
    report_markdown: str,
    required_facts: list[str],
    *,
    claims: Optional[list[dict[str, Any]]] = None,
    links: Optional[dict[str, list[dict[str, Any]]]] = None,
    wall_s: float = 0.0,
    state: str = "done",
) -> EvalScores:
    cov, missing = coverage(report_markdown, required_facts)
    precision: Optional[float] = None
    if claims:
        precision, _bad = claim_quote_precision(claims, links or {})
    return EvalScores(
        question_id=question_id,
        coverage=cov,
        missing_facts=missing,
        citation_recall=citation_recall(report_markdown),
        claim_quote_precision=precision,
        wall_s=round(wall_s, 1),
        state=state,
    )
