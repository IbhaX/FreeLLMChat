from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models import KeyUsage, Provider
from app.routing.quota import QuotaTracker
from tests.conftest import make_key


async def test_rpm_limit_blocks(session, provider):
    provider.limits = {"rpm": 2}
    k = await make_key(session, provider, "sk-aaa-1111")
    for _ in range(2):
        session.add(KeyUsage(key_id=k.id, ts=datetime.now(timezone.utc)))
    await session.commit()
    assert not await QuotaTracker().within_limits(session, k, provider)


async def test_old_requests_dont_count(session, provider):
    provider.limits = {"rpm": 1}
    k = await make_key(session, provider, "sk-aaa-1111")
    session.add(KeyUsage(
        key_id=k.id, ts=datetime.now(timezone.utc) - timedelta(minutes=5)))
    await session.commit()
    assert await QuotaTracker().within_limits(session, k, provider)


async def test_rpd_limit_blocks(session, provider):
    provider.limits = {"rpd": 3}
    k = await make_key(session, provider, "sk-aaa-1111")
    for _ in range(3):
        session.add(KeyUsage(
            key_id=k.id, ts=datetime.now(timezone.utc) - timedelta(hours=2)))
    await session.commit()
    assert not await QuotaTracker().within_limits(session, k, provider)


async def test_cooldown_honors_retry_after():
    q = QuotaTracker()
    p = Provider(name="p", base_url="x", limits={})
    assert abs(q.cooldown_seconds(1, 120.0, p, 429) - 120.0) < 5


async def test_cooldown_exponential():
    q = QuotaTracker()
    p = Provider(name="p", base_url="x", limits={})
    assert q.cooldown_seconds(0, None, p, 429) < q.cooldown_seconds(3, None, p, 429)


async def test_daily_reset_tz_cooldown():
    q = QuotaTracker()
    p = Provider(name="p", base_url="x", limits={}, daily_reset_tz="UTC")
    secs = q.cooldown_seconds(1, None, p, 403)
    assert 0 < secs <= 86400
