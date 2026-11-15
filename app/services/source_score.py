"""Source scoring for the v2 evidence store (spec §9).

score = 0.40 * authority + 0.25 * primacy + 0.20 * recency_fit + 0.15 * transparency

Authority comes from a maintained domain-class table; primacy from the
document kind; recency decays only for time-sensitive questions;
transparency rewards named authorship, dates and references.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse

# ---------------------------------------------------------------- classes

SOURCE_CLASSES = (
    "primary", "official", "academic", "news", "reference", "blog", "forum",
    "unknown",
)

# Maintained domain → class table. Suffix matching: an entry for "gov"
# covers any *.gov host. Extend as the corpus grows.
_DOMAIN_CLASS: list[tuple[str, str]] = [
    ("arxiv.org", "academic"),
    ("semanticscholar.org", "academic"),
    ("openalex.org", "academic"),
    ("pubmed.ncbi.nlm.nih.gov", "academic"),
    ("doi.org", "academic"),
    ("nature.com", "academic"),
    ("science.org", "academic"),
    ("springer.com", "academic"),
    ("ieee.org", "academic"),
    ("acm.org", "academic"),
    (".gov", "official"),
    (".edu", "academic"),
    ("sec.gov", "primary"),
    ("europa.eu", "official"),
    ("who.int", "official"),
    ("un.org", "official"),
    ("reuters.com", "news"),
    ("apnews.com", "news"),
    ("bbc.", "news"),
    ("nytimes.com", "news"),
    ("theguardian.com", "news"),
    ("wsj.com", "news"),
    ("ft.com", "news"),
    ("bloomberg.com", "news"),
    ("wikipedia.org", "reference"),
    ("britannica.com", "reference"),
    ("stackoverflow.com", "forum"),
    ("reddit.com", "forum"),
    ("quora.com", "forum"),
    ("medium.com", "blog"),
    ("substack.com", "blog"),
    ("wordpress.com", "blog"),
    ("blogspot.com", "blog"),
]

_CLASS_AUTHORITY = {
    "primary": 1.0,
    "official": 0.95,
    "academic": 0.9,
    "news": 0.7,
    "reference": 0.6,
    "blog": 0.35,
    "forum": 0.25,
    "unknown": 0.4,
}

_DOC_SUFFIX = ("int/annual-report", "10-k", "10-q", "filing", "dataset",
               "whitepaper", "press-release", "docs", "documentation")


@dataclass
class ScoreResult:
    score: float
    source_class: str
    detail: dict[str, float | str] = field(default_factory=dict)


def domain_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except Exception:
        return ""


def classify_domain(url: str) -> str:
    """Map a URL's domain to a source class using the maintained table."""
    host = domain_of(url)
    if not host:
        return "unknown"
    for suffix, cls in _DOMAIN_CLASS:
        if suffix.startswith("."):  # TLD-ish rules (.gov, .edu)
            if host.endswith(suffix):
                return cls
        elif host == suffix or host.endswith("." + suffix) or suffix in host:
            return cls
    return "unknown"


def _primacy(source_class: str, url: str, content_type: str) -> float:
    if source_class == "primary":
        return 1.0
    if source_class in ("official", "academic"):
        return 0.9
    path = (urlparse(url).path or "").lower()
    if any(s in path for s in _DOC_SUFFIX) or content_type == "pdf":
        return 0.8
    if source_class == "news":
        return 0.6
    if source_class == "reference":
        return 0.5
    return 0.3


def _recency_fit(
    published_at: datetime | None, fetched_at: datetime, time_sensitive: bool
) -> float:
    if not time_sensitive or published_at is None:
        return 1.0
    age_days = max((fetched_at - published_at).days, 0)
    # Half-life ~1 year: a year-old source scores ~0.5 on time-sensitive asks.
    return round(0.5 ** (age_days / 365.0), 4)


def _transparency(author: str | None, published_at: datetime | None,
                  has_references: bool) -> float:
    score = 0.0
    if author and author.strip():
        score += 0.4
    if published_at is not None:
        score += 0.3
    if has_references:
        score += 0.3
    return score


def score_source(
    url: str,
    *,
    content_type: str = "html",
    author: str | None = None,
    published_at: datetime | None = None,
    fetched_at: datetime | None = None,
    has_references: bool = False,
    time_sensitive: bool = False,
    declared_class: str | None = None,
) -> ScoreResult:
    """Score a source 0..1 with an auditable per-factor breakdown."""
    fetched = fetched_at or datetime.now(timezone.utc)
    cls = declared_class if declared_class in SOURCE_CLASSES else classify_domain(url)
    authority = _CLASS_AUTHORITY.get(cls, 0.4)
    primacy = _primacy(cls, url, content_type)
    recency = _recency_fit(published_at, fetched, time_sensitive)
    transparency = _transparency(author, published_at, has_references)
    score = (0.40 * authority + 0.25 * primacy + 0.20 * recency
             + 0.15 * transparency)
    return ScoreResult(
        score=round(min(max(score, 0.0), 1.0), 4),
        source_class=cls,
        detail={
            "authority": authority,
            "primacy": primacy,
            "recency_fit": recency,
            "transparency": transparency,
        },
    )


# ------------------------------------------------------- corroboration

def is_corroborated(independent_sources: int, best_primary_score: float) -> bool:
    """A key claim is supported with ≥2 independent sources, or one primary
    source scoring ≥0.8 (spec §9 corroboration rule)."""
    return independent_sources >= 2 or best_primary_score >= 0.8


def looks_time_sensitive(question: str) -> bool:
    return bool(re.search(
        r"\b(latest|current|today|now|recent|news|price|pricing|release|"
        r"version|ranking|forecast|stock|weather|who is|ceo|president|202[0-9])\b",
        question or "", re.IGNORECASE))
