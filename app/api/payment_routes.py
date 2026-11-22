"""Razorpay Standard Web Checkout — order + signature verification routes.

The publishable key id is exposed via ``GET /api/razorpay/config``; the
key secret stays server-side (order auth + HMAC verification only).
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import require_user
from app.models import User
from app.services import entitlements, payments

router = APIRouter(prefix="/api", tags=["payments"],
                   dependencies=[Depends(require_user)])


class OrderIn(BaseModel):
    """Client checkout request. ``amount`` is in paise (smallest unit)."""
    amount: int = Field(..., ge=1, le=10_000_000)
    currency: str = "INR"
    receipt: Optional[str] = Field(default=None, max_length=40)


class VerifyIn(BaseModel):
    razorpay_order_id: Optional[str] = Field(default=None, max_length=64)
    razorpay_payment_id: str = Field(..., min_length=1, max_length=64)
    razorpay_signature: str = Field(..., min_length=1, max_length=256)
    razorpay_subscription_id: Optional[str] = Field(default=None,
                                                  max_length=64)


class SubscriptionIn(BaseModel):
    plan_id: str = Field(..., min_length=1, max_length=32)


@router.get("/razorpay/config")
async def razorpay_config() -> dict[str, str]:
    """Publishable checkout config — never includes the key secret."""
    key_id = payments.key_id_for_client()
    return {"key_id": key_id, "currency": "INR"}


@router.get("/plans")
async def list_plans() -> dict[str, Any]:
    """Subscription tiers; prices are the trusted backend catalogue."""
    return {"plans": payments.list_plans(),
            "usd_to_inr": payments.USD_TO_INR_RATE}


@router.get("/entitlements")
async def my_entitlements(
        user: User | None = Depends(require_user)) -> dict[str, Any]:
    """Caller plan + resolved capability map (drives feature gating)."""
    from app.api.deps import entitled
    from app.services.auth import plan_limits
    plan = user.plan if user else "free"
    return {"plan": plan,
            "capabilities": {cap: entitled(user, cap)
                             for cap in entitlements.CAPABILITIES},
            "limits": plan_limits(plan)}


@router.post("/create-subscription")
async def create_subscription(body: SubscriptionIn,
                              user: User | None = Depends(require_user)):
    """Create a Razorpay subscription for a paid plan tier."""
    try:
        sub = await payments.create_subscription(body.plan_id)
    except payments.PaymentError as e:
        raise HTTPException(e.status_code, detail={"error": {
            "message": e.public, "type": "payment_error"}})
    except Exception:
        raise HTTPException(500, detail={"error": {
            "message": "Could not create subscription",
            "type": "payment_error"}})
    return {**sub, "key_id": payments.key_id_for_client()}


@router.post("/create-order")
async def create_order(body: OrderIn,
                       user: User | None = Depends(require_user)):
    """Create a Razorpay order for the checkout modal."""
    if body.amount < payments.MIN_AMOUNT_PAISE:
        raise HTTPException(
            400, detail={"error": {
                "message": f"Amount must be at least "
                           f"{payments.MIN_AMOUNT_PAISE} paise",
                "type": "invalid_amount"}})
    try:
        order = await payments.create_order(
            body.amount, currency=body.currency, receipt=body.receipt)
    except payments.PaymentError as e:
        raise HTTPException(e.status_code, detail={"error": {
            "message": e.public, "type": "payment_error"}})
    except Exception:
        raise HTTPException(500, detail={"error": {
            "message": "Could not create payment order",
            "type": "payment_error"}})
    return {
        "order_id": order["order_id"],
        "amount": order["amount"],
        "currency": order["currency"],
        "key_id": payments.key_id_for_client(),
    }


@router.post("/verify-payment")
async def verify_payment(body: VerifyIn,
                         user: User | None = Depends(require_user)):
    """Verify the checkout callback signature — the only success proof."""
    if not body.razorpay_subscription_id and not body.razorpay_order_id:
        raise HTTPException(400, detail={
            "success": False,
            "message": "razorpay_order_id or razorpay_subscription_id "
                       "is required"})
    ok = payments.verify_signature(
        body.razorpay_order_id or "", body.razorpay_payment_id,
        body.razorpay_signature,
        subscription_id=body.razorpay_subscription_id)
    if not ok:
        raise HTTPException(400, detail={
            "success": False,
            "message": "Payment signature verification failed"})

    # Verified subscription → grant the tier the subscription was actually
    # created for (fetched server-side from Razorpay notes, never trusted
    # from the client).
    plan: Optional[str] = None
    if body.razorpay_subscription_id and user is not None:
        tier = await payments.fetch_subscription_tier(
            body.razorpay_subscription_id)
        if tier:
            from app.db import get_session_factory
            async with get_session_factory()() as s:
                row = await s.get(User, user.id)
                if row is not None:
                    row.plan = tier
                    await s.commit()
                    plan = tier
    return {"success": True, "message": "Payment verified successfully",
            "order_id": body.razorpay_order_id,
            "subscription_id": body.razorpay_subscription_id,
            "payment_id": body.razorpay_payment_id,
            "plan": plan}
