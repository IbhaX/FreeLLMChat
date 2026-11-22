"""User authentication: accounts, opaque session tokens, plan context.

When ``NOVA_AUTH_REQUIRED`` is true (the default) /v1 endpoints require a
bearer token issued by ``/v1/auth/login``; set it false only for trusted
single-tenant local installs where anonymous access is acceptable.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, or_, select

from app.models import AuthToken, User

TOKEN_TTL_DAYS = int(os.environ.get("NOVA_TOKEN_TTL_DAYS", "30"))


def auth_required() -> bool:
    """Whether /v1 endpoints require an authenticated user session.

    Sourced from ``Settings.auth_required`` (env ``NOVA_AUTH_REQUIRED`` or
    .env) — read lazily so tests can flip it without re-importing."""
    from app.config import get_settings
    return bool(get_settings().auth_required)


def is_admin_email(email: str) -> bool:
    """Whether ``email`` is in the ``ADMIN_EMAILS`` env allowlist — those
    accounts always hold the admin role."""
    from app.config import get_settings
    allowed = {e.strip().lower()
               for e in (get_settings().admin_emails or "").split(",") if e.strip()}
    return bool(allowed) and (email or "").strip().lower() in allowed

MIN_USERNAME = 3
MIN_PASSWORD = 8

def _prehash(password: str) -> bytes:
    """SHA-256 pre-hash: bcrypt's 72-byte cap + uniform input length."""
    return hashlib.sha256(password.encode("utf-8")).digest()


def hash_password(password: str) -> str:
    import bcrypt
    return bcrypt.hashpw(_prehash(password), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, hashed: str) -> bool:
    import bcrypt
    try:
        return bool(bcrypt.checkpw(_prehash(password), hashed.encode("ascii")))
    except Exception:
        return False


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def create_user(session: Any, username: str, password: str,
                      email: str = "", display_name: str = "") -> User:
    username = (username or "").strip().lower()
    if len(username) < MIN_USERNAME:
        raise ValueError(f"username must be at least {MIN_USERNAME} characters")
    if len(password or "") < MIN_PASSWORD:
        raise ValueError(f"password must be at least {MIN_PASSWORD} characters")
    existing = (await session.execute(
        select(User).where(User.username == username))).scalar_one_or_none()
    if existing:
        raise ValueError("username is already taken")
    # First account becomes admin of the install.
    first = (await session.execute(
        select(User.id).limit(1))).scalar_one_or_none() is None
    email = (email or "").strip().lower()
    user = User(
        username=username, email=email,
        password_hash=hash_password(password),
        display_name=(display_name or "").strip() or username,
        role="admin" if (first or is_admin_email(email)) else "user",
    )
    session.add(user)
    await session.flush()
    return user


async def authenticate(session: Any, username: str, password: str) -> Optional[User]:
    ident = (username or "").strip().lower()
    user = (await session.execute(
        select(User).where(
            or_(User.username == ident,
                func.lower(User.email) == ident))
    )).scalar_one_or_none()
    if not user or user.disabled or not verify_password(password, user.password_hash):
        return None
    if user.role != "admin" and is_admin_email(user.email):
        user.role = "admin"
        await session.flush()
    return user


async def issue_token(session: Any, user: User) -> str:
    token = secrets.token_urlsafe(32)
    session.add(AuthToken(
        user_id=user.id, token_hash=_hash_token(token),
        expires_at=datetime.now(timezone.utc) + timedelta(days=TOKEN_TTL_DAYS)))
    await session.flush()
    return token


async def resolve_token(session: Any, token: str) -> Optional[User]:
    if not token:
        return None
    row = (await session.execute(
        select(AuthToken).where(AuthToken.token_hash == _hash_token(token)))
    ).scalar_one_or_none()
    if not row or row.revoked:
        return None
    now = datetime.now(timezone.utc)
    exp = row.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp < now:
        return None
    row.last_used_at = now
    user = await session.get(User, row.user_id)
    if not user or user.disabled:
        return None
    return user


async def revoke_token(session: Any, token: str) -> None:
    row = (await session.execute(
        select(AuthToken).where(AuthToken.token_hash == _hash_token(token)))
    ).scalar_one_or_none()
    if row:
        row.revoked = True


def bearer_token(authorization: str | None) -> str:
    if not authorization:
        return ""
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return ""


# ---------------------------------------------------------------- plans

PLAN_LIMITS: dict[str, dict[str, int]] = {
    # credits_per_month: 100 billed tokens = 1 credit
    "free": {"requests_per_day": 100, "research_per_day": 5,
             "documents": 20, "rooms": 5, "collections": 3,
             "credits_per_month": 1000},
    "basic": {"requests_per_day": 300, "research_per_day": 15,
              "documents": 50, "rooms": 15, "collections": 8,
              "credits_per_month": 5000},
    "plus": {"requests_per_day": 600, "research_per_day": 30,
             "documents": 100, "rooms": 30, "collections": 15,
             "credits_per_month": 15000},
    "pro": {"requests_per_day": 1000, "research_per_day": 50,
            "documents": 200, "rooms": 50, "collections": 25,
            "credits_per_month": 40000},
    "ultra": {"requests_per_day": 5000, "research_per_day": 200,
              "documents": 1000, "rooms": 200, "collections": 100,
              "credits_per_month": 150000},
    # legacy aliases kept for accounts provisioned before tiers existed
    "team": {"requests_per_day": 5000, "research_per_day": 200,
             "documents": 1000, "rooms": 200, "collections": 100,
             "credits_per_month": 150000},
}


def plan_limits(plan: str) -> dict[str, int]:
    return PLAN_LIMITS.get(plan or "free", PLAN_LIMITS["free"])
