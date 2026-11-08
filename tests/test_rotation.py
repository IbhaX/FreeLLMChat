from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.routing.rotator import Rotator
from tests.conftest import make_key


async def test_least_used_picks_lowest_count(session, provider):
    k1 = await make_key(session, provider, "sk-aaa-1111", label="a")
    k2 = await make_key(session, provider, "sk-bbb-2222", label="b")
    k1.daily_request_count = 10
    k2.daily_request_count = 2
    await session.commit()
    r = Rotator()
    picked = await r.pick(session, provider)
    assert picked.id == k2.id


async def test_round_robin_cycles(session, provider):
    provider.rotation_strategy = "round_robin"
    a = await make_key(session, provider, "sk-aaa-1111")
    b = await make_key(session, provider, "sk-bbb-2222")
    c = await make_key(session, provider, "sk-ccc-3333")
    r = Rotator()
    ids = [(await r.pick(session, provider)).id for _ in range(6)]
    assert ids[:3] == ids[3:]
    assert set(ids) == {a.id, b.id, c.id}


async def test_weighted_prefers_high_weight(session, provider):
    provider.rotation_strategy = "weighted"
    await make_key(session, provider, "sk-aaa-1111", weight=1)
    heavy = await make_key(session, provider, "sk-bbb-2222", weight=100)
    r = Rotator()
    picks = [(await r.pick(session, provider)).id for _ in range(50)]
    assert picks.count(heavy.id) > 40


async def test_priority_failover(session, provider):
    provider.rotation_strategy = "priority_failover"
    await make_key(session, provider, "sk-aaa-1111", priority=200)
    primary = await make_key(session, provider, "sk-bbb-2222", priority=10)
    r = Rotator()
    assert (await r.pick(session, provider)).id == primary.id


async def test_cooling_key_skipped(session, provider):
    hot = await make_key(session, provider, "sk-aaa-1111", label="hot")
    cold = await make_key(session, provider, "sk-bbb-2222", label="cold")
    cold.cooldown_until = datetime.now(timezone.utc) + timedelta(minutes=5)
    await session.commit()
    r = Rotator()
    assert (await r.pick(session, provider)).id == hot.id


async def test_all_cooling_returns_none(session, provider):
    k = await make_key(session, provider, "sk-aaa-1111")
    k.cooldown_until = datetime.now(timezone.utc) + timedelta(minutes=5)
    await session.commit()
    assert await Rotator().pick(session, provider) is None


async def test_quota_group_cools_whole_pool(session, provider):
    a = await make_key(session, provider, "sk-aaa-1111", quota_group="proj1")
    b = await make_key(session, provider, "sk-bbb-2222", quota_group="proj1")
    other = await make_key(session, provider, "sk-ccc-3333")
    a.cooldown_until = datetime.now(timezone.utc) + timedelta(minutes=5)
    await session.commit()
    picked = await Rotator().pick(session, provider)
    assert picked.id == other.id


async def test_disabled_key_skipped(session, provider):
    k = await make_key(session, provider, "sk-aaa-1111", label="only")
    k.enabled = False
    await session.commit()
    assert await Rotator().pick(session, provider) is None
