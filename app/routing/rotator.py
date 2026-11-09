"""Key selection strategies: round_robin, least_used, weighted, priority_failover.

Keys sharing a quota_group are treated as one pool: selecting any member
checks pool-level usage, and cooldown applies to the whole group.
"""

from __future__ import annotations

import itertools
import random
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import ApiKey, Provider
from app.routing.quota import QuotaTracker


class Rotator:
    def __init__(self, quota: QuotaTracker | None = None) -> None:
        self.quota = quota or QuotaTracker()
        self._rr_counters: dict[int, itertools.count] = {}

    async def healthy_keys(
        self, session: AsyncSession, provider: Provider
    ) -> list[ApiKey]:
        keys = list(
            (
                await session.execute(
                    select(ApiKey).where(
                        ApiKey.provider_id == provider.id, ApiKey.enabled.is_(True)
                    )
                )
            )
            .scalars()
            .all()
        )
        now = datetime.now(timezone.utc)
        active_groups: set[str] = set()
        out: list[ApiKey] = []
        for k in keys:
            cd = k.cooldown_until
            if cd is not None and self._aware(cd) > now:
                if k.quota_group:
                    active_groups.add(k.quota_group)
                continue
            out.append(k)
        if active_groups:
            out = [k for k in out if k.quota_group not in active_groups]
        # Pre-flight local quota checks. Off by default: the configured
        # limits in providers.yaml are per-account guesses (e.g. OpenRouter
        # is 50 rpd unfunded but 1000 rpd once credited), so guessing wrong
        # blocks working keys. Real provider 429s still cool keys properly.
        if not get_settings().preflight_quota_check:
            return out
        result: list[ApiKey] = []
        seen_groups: set[str] = set()
        for k in out:
            if k.quota_group and k.quota_group in seen_groups:
                # Representative already checked for this pool.
                result.append(k)
                continue
            if k.quota_group:
                seen_groups.add(k.quota_group)
            if await self.quota.within_limits(session, k, provider):
                result.append(k)
            elif k.quota_group:
                seen_groups.add(k.quota_group)
        return result

    async def pick(
        self, session: AsyncSession, provider: Provider, exclude_ids: set[int] | None = None
    ) -> ApiKey | None:
        keys = await self.healthy_keys(session, provider)
        if exclude_ids:
            keys = [k for k in keys if k.id not in exclude_ids]
        if not keys:
            return None
        strategy = provider.rotation_strategy
        if strategy == "round_robin":
            return self._round_robin(provider.id, keys)
        if strategy == "weighted":
            return self._weighted(keys)
        if strategy == "priority_failover":
            return min(keys, key=lambda k: (k.priority, k.daily_request_count))
        return min(keys, key=lambda k: (k.daily_request_count, k.last_used_at is not None, k.id))

    def _round_robin(self, provider_id: int, keys: list[ApiKey]) -> ApiKey:
        counter = self._rr_counters.setdefault(provider_id, itertools.count())
        return sorted(keys, key=lambda k: k.id)[next(counter) % len(keys)]

    @staticmethod
    def _weighted(keys: list[ApiKey]) -> ApiKey:
        weights = [max(k.weight, 1) for k in keys]
        return random.choices(keys, weights=weights, k=1)[0]

    @staticmethod
    def _aware(dt: datetime) -> datetime:
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
