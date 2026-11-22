"""Razorpay checkout — order creation + signature verification contracts."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import payments


class _FakeOrderClient:
    """Stand-in for razorpay.Client — records the payload, returns an order."""

    last_payload: dict[str, Any] = {}
    fail: Exception | None = None

    class _Order:
        def create(self, payload: dict[str, Any]) -> dict[str, Any]:
            _FakeOrderClient.last_payload = payload
            if _FakeOrderClient.fail is not None:
                raise _FakeOrderClient.fail
            return {"id": "order_test123", "amount": payload["amount"],
                    "currency": payload["currency"]}

    class _Plan:
        def create(self, payload: dict[str, Any]) -> dict[str, Any]:
            _FakeOrderClient.last_payload = payload
            if _FakeOrderClient.fail is not None:
                raise _FakeOrderClient.fail
            return {"id": "plan_test999",
                    "item": payload["item"]}

    class _Sub:
        def create(self, payload: dict[str, Any]) -> dict[str, Any]:
            _FakeOrderClient.last_payload = payload
            if _FakeOrderClient.fail is not None:
                raise _FakeOrderClient.fail
            return {"id": "sub_test777", "plan_id": payload["plan_id"],
                    "status": "created"}

        def fetch(self, subscription_id: str) -> dict[str, Any]:
            if _FakeOrderClient.fail is not None:
                raise _FakeOrderClient.fail
            return {"id": subscription_id, "status": "active",
                    "notes": {"tier": "plus"}}

    def __init__(self, auth: Any = None) -> None:
        self.order = self._Order()
        self.plan = self._Plan()
        self.subscription = self._Sub()


def _sig(order_id: str, payment_id: str, secret: str) -> str:
    return hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(),
                    hashlib.sha256).hexdigest()


@pytest.fixture(autouse=True)
def _test_creds(monkeypatch):
    """Inject test credentials for the duration of each test."""
    from app.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "razorpay_key_id", "rzp_test_key")
    from pydantic import SecretStr
    monkeypatch.setattr(s, "razorpay_key_secret", SecretStr("test_secret"))
    import razorpay
    _FakeOrderClient.fail = None
    monkeypatch.setattr(razorpay, "Client", _FakeOrderClient)
    yield


def test_config_returns_key_id_only():
    with TestClient(app) as c:
        r = c.get("/api/razorpay/config")
        assert r.status_code == 200
        body = r.json()
        assert body["key_id"] == "rzp_test_key"
        assert "secret" not in body
        assert "test_secret" not in r.text


def test_create_order_rejects_below_minimum():
    with TestClient(app) as c:
        r = c.post("/api/create-order", json={"amount": 50})
        assert r.status_code == 400
        r = c.post("/api/create-order", json={"amount": 0})
        assert r.status_code in (400, 422)


def test_create_order_rejects_bad_currency():
    with TestClient(app) as c:
        r = c.post("/api/create-order",
                   json={"amount": 5000, "currency": "USD"})
        assert r.status_code == 400


def test_create_order_success_sends_paise():
    with TestClient(app) as c:
        r = c.post("/api/create-order", json={
            "amount": 10000, "currency": "INR", "receipt": "r1"})
        assert r.status_code == 200
        body = r.json()
        assert body["order_id"] == "order_test123"
        assert body["amount"] == 10000
        assert body["currency"] == "INR"
        assert body["key_id"] == "rzp_test_key"
        sent = _FakeOrderClient.last_payload
        assert sent["amount"] == 10000 and sent["currency"] == "INR"


def test_verify_payment_accepts_valid_signature():
    sig = _sig("order_test123", "pay_test456", "test_secret")
    with TestClient(app) as c:
        r = c.post("/api/verify-payment", json={
            "razorpay_order_id": "order_test123",
            "razorpay_payment_id": "pay_test456",
            "razorpay_signature": sig})
        assert r.status_code == 200
        assert r.json()["success"] is True


def test_verify_payment_rejects_bad_signature():
    with TestClient(app) as c:
        r = c.post("/api/verify-payment", json={
            "razorpay_order_id": "order_test123",
            "razorpay_payment_id": "pay_test456",
            "razorpay_signature": "deadbeef" * 8})
        assert r.status_code == 400
        body = r.json()
        assert body["detail"]["success"] is False


def test_verify_payment_rejects_tampered_payment_id():
    sig = _sig("order_test123", "pay_test456", "test_secret")
    with TestClient(app) as c:
        r = c.post("/api/verify-payment", json={
            "razorpay_order_id": "order_test123",
            "razorpay_payment_id": "pay_OTHER789",
            "razorpay_signature": sig})
        assert r.status_code == 400


def test_verify_payment_missing_fields():
    with TestClient(app) as c:
        r = c.post("/api/verify-payment", json={
            "razorpay_order_id": "order_test123"})
        assert r.status_code in (400, 422)


def test_create_order_missing_credentials(monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "razorpay_key_secret", None)
    with TestClient(app) as c:
        r = c.post("/api/create-order", json={"amount": 10000})
        assert r.status_code == 500
        assert "test_secret" not in r.text


def test_verify_signature_constant_time_path():
    assert payments.verify_signature("o1", "p1",
                                     _sig("o1", "p1", "test_secret"))
    assert not payments.verify_signature("o1", "p1", "bogus")
    assert not payments.verify_signature("o1", "p1", "")


# --- subscriptions ---------------------------------------------------------

def _sub_sig(payment_id: str, sub_id: str, secret: str) -> str:
    return hmac.new(secret.encode(),
                    f"{payment_id}|{sub_id}".encode(),
                    hashlib.sha256).hexdigest()


def test_plans_returns_five_tiers():
    with TestClient(app) as c:
        r = c.get("/api/plans")
        assert r.status_code == 200
        plans = {p["id"]: p for p in r.json()["plans"]}
        assert set(plans) == {"free", "basic", "plus", "pro", "ultra"}
        assert plans["free"]["usd_cents"] == 0
        assert [plans[k]["usd_cents"] for k in
                ("basic", "plus", "pro", "ultra")] == [500, 1000, 2000, 5000]
        # INR conversion is applied; prices are backend-derived.
        assert plans["basic"]["amount_paise"] == \
            int(round(5 * payments.USD_TO_INR_RATE * 100))
        assert "secret" not in r.text


def test_create_subscription_rejects_unknown_plan():
    with TestClient(app) as c:
        r = c.post("/api/create-subscription",
                   json={"plan_id": "platinum"})
        assert r.status_code == 400


def test_create_subscription_rejects_free_plan():
    with TestClient(app) as c:
        r = c.post("/api/create-subscription", json={"plan_id": "free"})
        assert r.status_code == 400


def test_create_subscription_success(monkeypatch):
    monkeypatch.setitem(payments._plan_ids, "basic", "plan_cached")
    with TestClient(app) as c:
        r = c.post("/api/create-subscription", json={"plan_id": "basic"})
        assert r.status_code == 200
        body = r.json()
        assert body["subscription_id"] == "sub_test777"
        assert body["plan_id"] == "basic"
        assert body["amount"] == int(round(5 * 83.0 * 100))
        assert body["key_id"] == "rzp_test_key"
        assert _FakeOrderClient.last_payload["plan_id"] == "plan_cached"


def test_create_subscription_creates_plan_when_uncached():
    payments._plan_ids.pop("plus", None)
    with TestClient(app) as c:
        r = c.post("/api/create-subscription", json={"plan_id": "plus"})
        assert r.status_code == 200
        assert r.json()["subscription_id"] == "sub_test777"
        assert _FakeOrderClient.last_payload["plan_id"] == "plan_test999"


def test_verify_payment_accepts_subscription_signature():
    sig = _sub_sig("pay_test456", "sub_test777", "test_secret")
    with TestClient(app) as c:
        r = c.post("/api/verify-payment", json={
            "razorpay_subscription_id": "sub_test777",
            "razorpay_payment_id": "pay_test456",
            "razorpay_signature": sig})
        assert r.status_code == 200
        assert r.json()["success"] is True


def test_verify_payment_rejects_order_sig_as_subscription():
    # An order-style signature must not verify as a subscription triple.
    sig = _sig("sub_test777", "pay_test456", "test_secret")
    with TestClient(app) as c:
        r = c.post("/api/verify-payment", json={
            "razorpay_subscription_id": "sub_test777",
            "razorpay_payment_id": "pay_test456",
            "razorpay_signature": sig})
        assert r.status_code == 400


def test_verify_payment_requires_reference_id():
    with TestClient(app) as c:
        r = c.post("/api/verify-payment", json={
            "razorpay_payment_id": "pay_test456",
            "razorpay_signature": "deadbeef" * 8})
        assert r.status_code == 400


# --- entitlements / RBAC ---------------------------------------------------

def test_entitlement_plan_hierarchy():
    from app.services import entitlements as ent
    assert ent.plan_rank("free") < ent.plan_rank("basic") < \
        ent.plan_rank("plus") < ent.plan_rank("pro") < \
        ent.plan_rank("ultra")
    # Free holds no paid capability; each tier unlocks its own.
    assert not ent.has_cap("free", "library_captures", 1)
    assert ent.has_cap("basic", "library_captures", 1)
    assert not ent.has_cap("basic", "deep_research", 1)
    assert ent.has_cap("plus", "deep_research", 1)
    assert not ent.has_cap("plus", "custom_agents", 1)
    assert ent.has_cap("pro", "custom_agents", 1)
    assert ent.has_cap("ultra", "ultra_rate_limits", 1)


def test_entitlement_admin_bypass():
    from app.services import entitlements as ent
    assert ent.has_cap("free", "deep_research", 1, is_admin=True)


def test_entitlement_rollout_partial_and_deterministic():
    from app.services import entitlements as ent
    # Partial rollout: at 50%, a stable slice of plus users holds the cap.
    results = [ent.has_cap("plus", "memory_explorer", uid)
               for uid in range(200)]
    assert 20 < sum(results) < 160  # roughly half, deterministic
    # Same user, same answer every call.
    assert (ent.has_cap("plus", "memory_explorer", 42) ==
            ent.has_cap("plus", "memory_explorer", 42))
    # Below the plan tier, rollout never applies.
    assert not any(ent.has_cap("free", "memory_explorer", uid)
                   for uid in range(50))


def test_entitlement_rollout_boundaries(monkeypatch):
    from app.services import entitlements as ent
    monkeypatch.setitem(ent.CAPABILITIES["early_access"], "rollout", 0)
    assert not any(ent.has_cap("ultra", "early_access", uid)
                   for uid in range(50))
    monkeypatch.setitem(ent.CAPABILITIES["early_access"], "rollout", 100)
    assert all(ent.has_cap("ultra", "early_access", uid)
               for uid in range(50))


def test_upgrade_error_shape():
    from app.services import entitlements as ent
    e = ent.upgrade_error("deep_research")["error"]
    assert e["type"] == "upgrade_required"
    assert e["capability"] == "deep_research"
    assert e["plan_required"] == "plus"


def test_entitlements_endpoint():
    with TestClient(app) as c:
        r = c.get("/api/entitlements")
        # Anonymous in no-auth local mode → fully entitled snapshot.
        assert r.status_code == 200
        body = r.json()
        assert body["plan"] in payments.PLANS or body["plan"] == "free"
        assert set(body["capabilities"]) == set(
            __import__("app.services.entitlements",
                       fromlist=["CAPABILITIES"]).CAPABILITIES)
        assert "requests_per_day" in body["limits"]


def test_verify_subscription_grants_plan_to_signed_in_user(monkeypatch):
    """Regression: verified sub payment must persist the tier on the user.

    Covers the full grant path — signature check, Razorpay notes fetch,
    session-factory write — which previously 500'd on a bare
    sessionmaker used as an async context manager."""
    import asyncio

    from app.config import get_settings
    from app.db import get_session_factory
    from app.models import User

    get_settings().auth_required = True
    monkeypatch.setattr(get_settings(), "referral_code", "")
    try:
        with TestClient(app) as c:
            r = c.post("/v1/auth/register", json={
                "username": "pay-user", "password": "password123"})
            assert r.status_code == 201, r.text
            token = r.json()["token"]
            sig = _sub_sig("pay_test456", "sub_test777", "test_secret")
            r = c.post("/api/verify-payment", headers={
                "X-User-Token": token}, json={
                "razorpay_subscription_id": "sub_test777",
                "razorpay_payment_id": "pay_test456",
                "razorpay_signature": sig})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["success"] is True
            # _Sub.fetch returns notes.tier="plus" → persisted on user.
            assert body["plan"] == "plus"

            async def _plan() -> str:
                async with get_session_factory()() as s:
                    u = (await s.execute(
                        __import__("sqlalchemy").select(User).where(
                            User.username == "pay-user"))).scalar_one()
                    return u.plan
            assert asyncio.run(_plan()) == "plus"
    finally:
        get_settings().auth_required = False


def test_verify_subscription_no_plan_grant_without_user():
    """Verified subscription w/o a signed-in user grants no plan."""
    sig = _sub_sig("pay_test456", "sub_test777", "test_secret")
    with TestClient(app) as c:
        r = c.post("/api/verify-payment", json={
            "razorpay_subscription_id": "sub_test777",
            "razorpay_payment_id": "pay_test456",
            "razorpay_signature": sig})
        assert r.status_code == 200
        assert r.json()["success"] is True
        assert r.json()["plan"] is None  # anonymous → nothing promoted


def test_fetch_subscription_tier_reads_notes():
    """notes.tier (set server-side at creation) maps to a known plan."""
    import asyncio
    tier = asyncio.run(payments.fetch_subscription_tier("sub_test777"))
    assert tier == "plus"


# --- admin user management -------------------------------------------------

def _register_as_admin(c, username: str) -> str:
    import asyncio

    r = c.post("/v1/auth/register", json={
        "username": username, "password": "password123"})
    assert r.status_code == 201, r.text

    async def _promote() -> None:
        from sqlalchemy import update
        from app.db import get_session_factory
        from app.models import User
        async with get_session_factory()() as s:
            await s.execute(update(User).where(
                User.username == username).values(role="admin"))
            await s.commit()
    asyncio.run(_promote())
    return r.json()["token"]


def test_admin_users_list_and_plan_change(monkeypatch):
    """Admin can list accounts and upgrade/downgrade a plan; the write
    route stays behind require_admin for regular users."""
    import asyncio

    from app.config import get_settings
    from app.db import get_session_factory
    from app.models import User

    get_settings().auth_required = True
    monkeypatch.setattr(get_settings(), "referral_code", "")
    try:
        with TestClient(app) as c:
            admin_tok = _register_as_admin(c, "mgmt-admin")
            r = c.post("/v1/auth/register", json={
                "username": "mgmt-user", "password": "password123"})
            uid = r.json()["user"]["id"]
            hdrs = {"X-User-Token": admin_tok}

            users = c.get("/admin/users", headers=hdrs).json()
            me = next(u for u in users if u["username"] == "mgmt-user")
            assert me["plan"] == "free" and me["disabled"] is False

            r = c.patch(f"/admin/users/{uid}", headers=hdrs,
                        json={"plan": "pro"})
            assert r.status_code == 200
            assert r.json()["plan"] == "pro"

            r = c.patch(f"/admin/users/{uid}", headers=hdrs,
                        json={"plan": "diamond"})
            assert r.status_code == 400

            # Regular user token gets 403 admin_required on the write route.
            r = c.post("/v1/auth/register", json={
                "username": "mgmt-plain", "password": "password123"})
            r = c.patch(f"/admin/users/{uid}", headers={
                "X-User-Token": r.json()["token"]}, json={"plan": "ultra"})
            assert r.status_code == 403
            assert r.json()["detail"]["error"]["type"] == "admin_required"

            r = c.patch(f"/admin/users/{uid}", headers=hdrs,
                        json={"disabled": True})
            assert r.json()["disabled"] is True

            async def _db() -> tuple[str, bool]:
                async with get_session_factory()() as s:
                    u = (await s.execute(
                        __import__("sqlalchemy").select(User).where(
                            User.username == "mgmt-user"))).scalar_one()
                    return u.plan, u.disabled
            assert asyncio.run(_db()) == ("pro", True)

            # Disabled account is locked out of login entirely.
            r = c.post("/v1/auth/login", json={
                "username": "mgmt-user", "password": "password123"})
            assert r.status_code in (401, 403)
    finally:
        get_settings().auth_required = False


def test_admin_user_delete_soft_and_hard(monkeypatch):
    """Soft delete disables + revokes tokens (recoverable); hard delete
    removes the row. Neither works on the caller's own account."""
    import asyncio

    from app.config import get_settings
    from app.db import get_session_factory
    from app.models import User

    get_settings().auth_required = True
    monkeypatch.setattr(get_settings(), "referral_code", "")
    try:
        with TestClient(app) as c:
            admin_tok = _register_as_admin(c, "del-admin")
            hdrs = {"X-User-Token": admin_tok}
            r = c.post("/v1/auth/register", json={
                "username": "del-soft", "password": "password123"})
            soft_id, soft_tok = (r.json()["user"]["id"], r.json()["token"])
            r = c.post("/v1/auth/register", json={
                "username": "del-hard", "password": "password123"})
            hard_id = r.json()["user"]["id"]

            # Soft delete: disabled + deleted_at + token revoked.
            r = c.delete(f"/admin/users/{soft_id}", headers=hdrs)
            assert r.status_code == 200 and r.json()["deleted"] == "soft"
            users = c.get("/admin/users", headers=hdrs).json()
            me = next(u for u in users if u["username"] == "del-soft")
            assert me["disabled"] is True and me["deleted_at"] is not None
            # The victim's existing session stops resolving.
            assert c.get("/v1/models", headers={
                "X-User-Token": soft_tok}).status_code == 401

            # Restore via PATCH disabled:false.
            r = c.patch(f"/admin/users/{soft_id}", headers=hdrs,
                        json={"disabled": False})
            assert r.json()["deleted_at"] is None
            assert r.json()["disabled"] is False

            # Hard delete: row gone entirely.
            r = c.delete(f"/admin/users/{hard_id}?mode=hard", headers=hdrs)
            assert r.status_code == 200 and r.json()["deleted"] == "hard"

            async def _exists() -> bool:
                async with get_session_factory()() as s:
                    return (await s.get(User, hard_id)) is not None
            assert asyncio.run(_exists()) is False
            assert all(u["username"] != "del-hard"
                       for u in c.get("/admin/users", headers=hdrs).json())

            # Self-delete and bad mode are rejected.
            me = c.get("/v1/auth/me", headers=hdrs).json()["user"]
            assert c.delete(f"/admin/users/{me['id']}",
                            headers=hdrs).status_code == 400
            assert c.delete(f"/admin/users/{soft_id}?mode=bogus",
                            headers=hdrs).status_code == 400

            # Non-admin is refused outright.
            r = c.post("/v1/auth/register", json={
                "username": "del-plain", "password": "password123"})
            assert c.delete(f"/admin/users/{soft_id}", headers={
                "X-User-Token": r.json()["token"]}).status_code == 403
    finally:
        get_settings().auth_required = False


def test_admin_self_disable_rejected(monkeypatch):
    from app.config import get_settings
    get_settings().auth_required = True
    monkeypatch.setattr(get_settings(), "referral_code", "")
    try:
        with TestClient(app) as c:
            tok = _register_as_admin(c, "self-admin")
            me = c.get("/v1/auth/me",
                       headers={"X-User-Token": tok}).json()["user"]
            r = c.patch(f"/admin/users/{me['id']}", headers={
                "X-User-Token": tok}, json={"disabled": True})
            assert r.status_code == 400
    finally:
        get_settings().auth_required = False


def test_fetch_subscription_tier_rejects_unknown():
    """A tampered/unknown tier in notes resolves to None, never granted."""
    import asyncio

    async def _check() -> str | None:
        orig = _FakeOrderClient._Sub.fetch

        def bad_fetch(self: Any, sid: str) -> dict[str, Any]:
            return {"id": sid, "notes": {"tier": "diamond"}}
        _FakeOrderClient._Sub.fetch = bad_fetch
        try:
            return await payments.fetch_subscription_tier("sub_x")
        finally:
            _FakeOrderClient._Sub.fetch = orig
    assert asyncio.run(_check()) is None
