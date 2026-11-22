"""Razorpay payment service — order creation and signature verification.

The key secret never leaves this module's trust boundary: order creation
uses it for HTTP basic auth, verification uses it as the HMAC key. Nothing
below logs credentials or signature material.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
from typing import Any, Optional

from app.config import get_settings

log = logging.getLogger("nova.payments")

MIN_AMOUNT_PAISE = 100          # Razorpay minimum: 1 INR
SUPPORTED_CURRENCIES = frozenset({"INR"})
ORDER_TIMEOUT_S = 20.0
USD_TO_INR_RATE = 83.0          # plan prices are USD; charges settle in INR

# Subscription tiers. Prices are the trusted backend source of truth —
# the client only ever sends a plan id.
PLANS: dict[str, dict[str, Any]] = {
    "free": {
        "name": "Free", "usd_cents": 0,
        "features": ["Chat, Research and Roleplay",
                     "Community model pool",
                     "1 seat"],
    },
    "basic": {
        "name": "Basic", "usd_cents": 500,
        "features": ["Everything in Free",
                     "Priority failover queue",
                     "Library captures"],
    },
    "plus": {
        "name": "Plus", "usd_cents": 1000,
        "features": ["Everything in Basic",
                     "Deep research runs",
                     "Memory Explorer"],
    },
    "pro": {
        "name": "Pro", "usd_cents": 2000,
        "features": ["Everything in Plus",
                     "Custom agents and skills",
                     "Faster streaming"],
    },
    "ultra": {
        "name": "Ultra", "usd_cents": 5000,
        "features": ["Everything in Pro",
                     "Highest rate limits",
                     "Early access features"],
    },
}
PLAN_INTERVAL = "monthly"
PLAN_TOTAL_COUNT = 12           # monthly subscription billed for a year

# Lazily created Razorpay plan ids — disposable cache; plans are immutable
# upstream so recreating after a restart is harmless.
_plan_ids: dict[str, str] = {}


class PaymentError(Exception):
    """Safe upstream-failure signal; ``public`` is client-displayable."""

    def __init__(self, public: str, *, status_code: int = 500) -> None:
        super().__init__(public)
        self.public = public
        self.status_code = status_code


def _credentials() -> tuple[str, str]:
    """(key_id, key_secret) or raise when env config is missing."""
    s = get_settings()
    key_id = s.razorpay_key_id
    secret = s.razorpay_key_secret
    secret_val = secret.get_secret_value() if secret is not None else ""
    if not key_id or not secret_val:
        raise PaymentError("Payments are not configured on this server")
    return key_id, secret_val


def key_id_for_client() -> str:
    """The publishable key id for the checkout SDK, or '' when unset."""
    return get_settings().razorpay_key_id or ""


def list_plans() -> list[dict[str, Any]]:
    """Public plan catalogue — prices for display, never for charging."""
    out: list[dict[str, Any]] = []
    for pid, p in PLANS.items():
        out.append({
            "id": pid,
            "name": p["name"],
            "usd_cents": p["usd_cents"],
            "amount_paise": _plan_paise(pid),
            "currency": "INR",
            "interval": PLAN_INTERVAL,
            "features": p["features"],
        })
    return out


def _plan_paise(plan_id: str) -> int:
    """Trusted charge amount for a plan in INR paise."""
    cents = PLANS[plan_id]["usd_cents"]
    return int(round(cents / 100 * USD_TO_INR_RATE * 100))


async def _rzp_plan_id(client: Any, plan_id: str) -> str:
    """Find-or-create the Razorpay plan for a tier; ids are cached."""
    from razorpay.errors import BadRequestError, ServerError

    cached = _plan_ids.get(plan_id)
    if cached:
        return cached
    p = PLANS[plan_id]
    try:
        plan = await asyncio.wait_for(
            asyncio.to_thread(client.plan.create, {
                "period": PLAN_INTERVAL, "interval": 1,
                "item": {"name": f"{p['name']} (monthly)",
                         "amount": _plan_paise(plan_id),
                         "currency": "INR",
                         "description": f"IbhaX {p['name']} tier"},
                "notes": {"tier": plan_id},
            }), timeout=ORDER_TIMEOUT_S)
    except (BadRequestError, ServerError) as e:
        log.warning("razorpay plan create failed: %s", e)
        raise PaymentError("Payment service is unavailable",
                           status_code=502)
    except (asyncio.TimeoutError, TimeoutError):
        raise PaymentError("Payment service timed out", status_code=504)
    pid = plan.get("id")
    if not isinstance(pid, str) or not pid:
        raise PaymentError("Payment service returned an invalid plan",
                           status_code=502)
    _plan_ids[plan_id] = pid
    return pid


async def create_subscription(plan_id: str) -> dict[str, Any]:
    """Create a Razorpay subscription for a paid tier."""
    import razorpay
    from razorpay.errors import BadRequestError, ServerError

    if plan_id not in PLANS:
        raise PaymentError("Unknown plan", status_code=400)
    if PLANS[plan_id]["usd_cents"] <= 0:
        raise PaymentError("The free plan needs no checkout",
                           status_code=400)
    key_id, secret = _credentials()
    client = razorpay.Client(auth=(key_id, secret))
    rzp_plan = await _rzp_plan_id(client, plan_id)
    try:
        sub = await asyncio.wait_for(
            asyncio.to_thread(client.subscription.create, {
                "plan_id": rzp_plan,
                "total_count": PLAN_TOTAL_COUNT,
                "quantity": 1,
                "customer_notify": 0,
                "notes": {"tier": plan_id},
            }), timeout=ORDER_TIMEOUT_S)
    except BadRequestError as e:
        log.warning("razorpay subscription rejected: %s", e)
        if getattr(e, "status_code", None) == 401:
            raise PaymentError("Payment credentials were rejected",
                               status_code=401)
        raise PaymentError("Subscription request was rejected",
                           status_code=502)
    except ServerError as e:
        log.warning("razorpay server error on subscription: %s", e)
        raise PaymentError("Payment service is unavailable",
                           status_code=502)
    except (asyncio.TimeoutError, TimeoutError):
        raise PaymentError("Payment service timed out", status_code=504)
    except Exception as e:
        log.warning("razorpay subscription failed: %s", e)
        raise PaymentError("Payment service request failed",
                           status_code=502)
    sub_id = sub.get("id")
    if not isinstance(sub_id, str) or not sub_id:
        raise PaymentError("Payment service returned an invalid "
                           "subscription", status_code=502)
    return {"subscription_id": sub_id, "plan_id": plan_id,
            "amount": _plan_paise(plan_id), "currency": "INR"}


async def create_order(amount_paise: int, currency: str = "INR",
                       receipt: Optional[str] = None,
                       notes: Optional[dict[str, str]] = None) -> dict[str, Any]:
    """Create a Razorpay order. ``amount_paise`` is the smallest currency
    unit — callers must have already validated it against the minimum."""
    import razorpay
    from razorpay.errors import BadRequestError, ServerError

    key_id, secret = _credentials()
    if amount_paise < MIN_AMOUNT_PAISE:
        raise PaymentError(
            f"Amount must be at least {MIN_AMOUNT_PAISE} paise",
            status_code=400)
    if currency.upper() not in SUPPORTED_CURRENCIES:
        raise PaymentError(
            f"Unsupported currency '{currency}'", status_code=400)

    payload: dict[str, Any] = {
        "amount": amount_paise,
        "currency": currency.upper(),
        "payment_capture": 1,
    }
    if receipt:
        payload["receipt"] = receipt[:40]
    if notes:
        payload["notes"] = {str(k)[:32]: str(v)[:256]
                            for k, v in notes.items()}

    client = razorpay.Client(auth=(key_id, secret))
    try:
        order = await asyncio.wait_for(
            asyncio.to_thread(client.order.create, payload),
            timeout=ORDER_TIMEOUT_S)
    except BadRequestError as e:
        log.warning("razorpay rejected order payload: %s", e)
        if getattr(e, "status_code", None) == 401:
            raise PaymentError("Payment credentials were rejected",
                               status_code=401)
        raise PaymentError("Order request was rejected", status_code=502)
    except ServerError as e:
        log.warning("razorpay server error on order create: %s", e)
        raise PaymentError("Payment service is unavailable",
                           status_code=502)
    except (asyncio.TimeoutError, TimeoutError):
        raise PaymentError("Payment service timed out", status_code=504)
    except Exception as e:
        # razorpay raises its own typed errors plus requests errors; treat
        # anything else (auth, network) as an upstream failure.
        log.warning("razorpay order create failed: %s", e)
        raise PaymentError("Payment service request failed",
                           status_code=502)

    order_id = order.get("id")
    if not isinstance(order_id, str) or not order_id:
        log.warning("razorpay order missing id: %r", order)
        raise PaymentError("Payment service returned an invalid order",
                           status_code=502)
    return {
        "order_id": order_id,
        "amount": order.get("amount", amount_paise),
        "currency": order.get("currency", currency.upper()),
    }


async def fetch_subscription_tier(subscription_id: str) -> Optional[str]:
    """Authoritative plan tier for a subscription, read from Razorpay.

    Notes are set server-side at creation — the client can never claim a
    tier it didn't pay for. Returns None when the fetch fails or the tier
    is unknown.
    """
    from razorpay.errors import BadRequestError, ServerError

    key_id, secret = _credentials()
    import razorpay
    client = razorpay.Client(auth=(key_id, secret))
    try:
        sub = await asyncio.wait_for(
            asyncio.to_thread(client.subscription.fetch, subscription_id),
            timeout=ORDER_TIMEOUT_S)
    except (BadRequestError, ServerError, asyncio.TimeoutError,
            TimeoutError) as e:
        log.warning("razorpay subscription fetch failed: %s", e)
        return None
    except Exception as e:
        log.warning("razorpay subscription fetch failed: %s", e)
        return None
    tier = (sub.get("notes") or {}).get("tier")
    return tier if tier in PLANS else None


def verify_signature(order_id: str, payment_id: str,
                     signature: str,
                     subscription_id: Optional[str] = None) -> bool:
    """Constant-time HMAC-SHA256 check of the checkout callback triple.

    Orders sign ``order_id|payment_id``; subscriptions sign
    ``payment_id|subscription_id``.
    """
    try:
        _key_id, secret = _credentials()
    except PaymentError:
        return False
    if subscription_id:
        signed = f"{payment_id}|{subscription_id}"
    else:
        signed = f"{order_id}|{payment_id}"
    expected = hmac.new(
        secret.encode("utf-8"), signed.encode("utf-8"),
        hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
