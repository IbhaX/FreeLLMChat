"""Per-subject usage metering and plan entitlement checks."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, select

from app.models import UsageEvent
from app.services.auth import plan_limits


async def record(session: Any, subject: str, kind: str, *,
                 model: str = "", provider: str = "",
                 tokens_in: int = 0, tokens_out: int = 0
                 ) -> Optional[UsageEvent]:
    """Append one usage event; returns the row so callers can settle token
    counts after the upstream call completes. Never raises — metering must
    not break requests."""
    try:
        ev = UsageEvent(
            subject=str(subject or "anon"), kind=kind,
            model=model or "", provider=provider or "",
            tokens_in=int(tokens_in or 0), tokens_out=int(tokens_out or 0))
        session.add(ev)
        await session.flush()
        return ev
    except Exception:
        return None


async def settle_tokens(event_id: Optional[int], tokens_in: int,
                        tokens_out: int) -> None:
    """Backfill real token counts onto the UsageEvent row recorded at the
    start of a request — events are written upfront for quota counting,
    but credits only decrement once billed tokens land here. Uses its own
    session so it is safe to call after the request session closed."""
    if not event_id or not (tokens_in or tokens_out):
        return
    try:
        from app.db import get_session_factory

        async with get_session_factory()() as s:
            row = await s.get(UsageEvent, int(event_id))
            if row is not None:
                row.tokens_in = int(tokens_in)
                row.tokens_out = int(tokens_out)
                await s.commit()
    except Exception:
        pass  # metering must never break the response path


async def today_count(session: Any, subject: str, kind: str | None = None) -> int:
    day_ago = datetime.now(timezone.utc) - timedelta(hours=24)
    q = select(func.count(UsageEvent.id)).where(
        UsageEvent.subject == str(subject), UsageEvent.ts >= day_ago)
    if kind:
        q = q.where(UsageEvent.kind == kind)
    return int((await session.execute(q)).scalar() or 0)


async def check_quota(session: Any, subject: str, kind: str,
                      plan: str = "free") -> tuple[bool, str]:
    """Return (allowed, reason). Enforces per-day counters from the plan."""
    limits = plan_limits(plan)
    key = {"chat": "requests_per_day", "research": "research_per_day"}.get(kind)
    if not key:
        return True, ""
    limit = limits.get(key, 0)
    if limit <= 0:
        return True, ""
    used = await today_count(session, subject, "research" if kind == "research" else None)
    if used >= limit:
        return False, f"{kind} limit reached for the {plan} plan ({used}/{limit} per day)"
    return True, ""


async def summary(session: Any, subject: str, days: int = 30) -> dict[str, Any]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (await session.execute(
        select(UsageEvent.kind,
               func.count(UsageEvent.id),
               func.coalesce(func.sum(UsageEvent.tokens_in), 0),
               func.coalesce(func.sum(UsageEvent.tokens_out), 0))
        .where(UsageEvent.subject == str(subject), UsageEvent.ts >= since)
        .group_by(UsageEvent.kind))).all()
    by_kind = {
        r[0]: {"events": r[1], "tokens_in": r[2], "tokens_out": r[3]}
        for r in rows
    }
    return {"subject": str(subject), "days": days, "by_kind": by_kind,
            "total_events": sum(v["events"] for v in by_kind.values())}
