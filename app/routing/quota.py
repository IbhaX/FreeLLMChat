"""Local quota tracking: sliding-window RPM/RPD/TPM/TPD checks plus
cooldown computation with provider-specific daily reset times."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import ApiKey, KeyUsage, Provider


class QuotaTracker:
    """Pre-flight quota checks so keys are skipped before they 429."""

    WINDOW_S = 24 * 3600

    async def usage_totals(
        self,
        session: AsyncSession,
        key_ids: list[int],
        since: datetime,
    ) -> dict[int, tuple[int, int]]:
        """Return {key_id: (requests, tokens)} in the window."""
        if not key_ids:
            return {}
        rows = await session.execute(
            select(
                KeyUsage.key_id,
                func.count(KeyUsage.id),
                func.coalesce(func.sum(KeyUsage.tokens_in + KeyUsage.tokens_out), 0),
            )
            .where(KeyUsage.key_id.in_(key_ids), KeyUsage.ts >= since)
            .group_by(KeyUsage.key_id)
        )
        return {r[0]: (r[1], r[2]) for r in rows.all()}

    async def within_limits(
        self, session: AsyncSession, key: ApiKey, provider: Provider
    ) -> bool:
        limits = provider.limits or {}
        if not any(k in limits for k in ("rpm", "rpd", "tpm", "tpd")):
            return True
        # quota_group: pool usage across all member keys.
        key_ids = [key.id]
        if key.quota_group:
            sibs = (
                await session.execute(
                    select(ApiKey.id).where(
                        ApiKey.provider_id == provider.id,
                        ApiKey.quota_group == key.quota_group,
                    )
                )
            ).scalars().all()
            key_ids = list(sibs)
        now = datetime.now(timezone.utc)
        minute_ago = now - timedelta(seconds=60)
        day_ago = now - timedelta(seconds=self.WINDOW_S)

        day_totals = await self.usage_totals(session, key_ids, day_ago)
        reqs_day = sum(v[0] for v in day_totals.values())
        toks_day = sum(v[1] for v in day_totals.values())
        rpd = limits.get("rpd")
        tpd = limits.get("tpd")
        if rpd and reqs_day >= rpd:
            return False
        if tpd and toks_day >= tpd:
            return False

        minute_totals = await self.usage_totals(session, key_ids, minute_ago)
        reqs_min = sum(v[0] for v in minute_totals.values())
        toks_min = sum(v[1] for v in minute_totals.values())
        rpm = limits.get("rpm")
        tpm = limits.get("tpm")
        if rpm and reqs_min >= rpm:
            return False
        if tpm and toks_min >= tpm:
            return False
        return True

    async def record(
        self,
        session: AsyncSession,
        key_id: int,
        tokens_in: int = 0,
        tokens_out: int = 0,
    ) -> None:
        session.add(
            KeyUsage(key_id=key_id, tokens_in=tokens_in, tokens_out=tokens_out)
        )
        # Opportunistic cleanup of old rows (cheap, amortized).
        if random.random() < 0.02:
            cutoff = datetime.now(timezone.utc) - timedelta(days=7)
            await session.execute(delete(KeyUsage).where(KeyUsage.ts < cutoff))

    def cooldown_seconds(
        self,
        consecutive_failures: int,
        retry_after_s: float | None,
        provider: Provider,
        status_code: int | None,
    ) -> float:
        s = get_settings()
        if retry_after_s:
            return min(retry_after_s + random.uniform(0, 2), s.cooldown_max_s)
        if status_code in (401, 403):
            # Auth/quota errors: cool until provider's daily reset when known.
            reset_in = self._seconds_until_daily_reset(provider)
            if reset_in:
                return reset_in
            return min(s.cooldown_base_s * 8, s.cooldown_max_s)
        backoff = s.cooldown_base_s * (2 ** min(consecutive_failures, 6))
        return min(backoff + random.uniform(0, backoff * 0.25), s.cooldown_max_s)

    @staticmethod
    def _seconds_until_daily_reset(provider: Provider) -> float | None:
        if not provider.daily_reset_tz:
            return None
        try:
            tz = ZoneInfo(provider.daily_reset_tz)
        except Exception:
            return None
        now = datetime.now(tz)
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if now >= midnight:
            midnight += timedelta(days=1)
        return (midnight - now).total_seconds()
