"""/v1/auth — registration, login, logout, profile, plans, usage."""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.deps import optional_user, subject_for
from app.config import get_settings
from app.db import get_session_factory
from app.models import User
from app.services import usage as usage_svc
from app.services.auth import (PLAN_LIMITS, authenticate, auth_required,
                               bearer_token, create_user, issue_token,
                               plan_limits, revoke_token)

# Deliberately no require_user: login/register/me must be reachable
# by visitors — user auth IS the gate, and registration is gated by the
# referral code when configured.
router = APIRouter(prefix="/v1/auth", tags=["auth"])


class RegisterBody(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=256)
    email: str = ""
    display_name: str = ""
    referral_code: str = ""


class LoginBody(BaseModel):
    username: str
    password: str


class GoogleBody(BaseModel):
    credential: str
    referral_code: str = ""


def _public_user(u: User) -> dict[str, Any]:
    return {"id": u.id, "username": u.username, "email": u.email,
            "display_name": u.display_name, "role": u.role, "plan": u.plan,
            "bio": getattr(u, "bio", "") or "",
            "avatar_url": getattr(u, "avatar_url", "") or "",
            "github_username": getattr(u, "github_username", "") or "",
            "pronouns": getattr(u, "pronouns", "") or "",
            "company": getattr(u, "company", "") or "",
            "location": getattr(u, "location", "") or "",
            "website": getattr(u, "website", "") or "",
            "created_at": u.created_at.isoformat() if u.created_at else None}


def _auth_required() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"error": {"message": "Authentication required",
                          "type": "authentication_required"}})


def referral_required() -> bool:
    """Invite-only registration is on when REFERRAL_CODE is configured."""
    return bool(get_settings().referral_code)


@router.post("/register", status_code=201)
async def register(body: RegisterBody) -> dict[str, Any]:
    expected = get_settings().referral_code
    if expected and not hmac.compare_digest(body.referral_code, expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Invalid or missing referral code")
    factory = get_session_factory()
    async with factory() as session:
        try:
            user = await create_user(session, body.username, body.password,
                                     body.email, body.display_name)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        token = await issue_token(session, user)
        await session.commit()
        return {"user": _public_user(user), "token": token}


@router.post("/login")
async def login(body: LoginBody) -> dict[str, Any]:
    factory = get_session_factory()
    async with factory() as session:
        user = await authenticate(session, body.username, body.password)
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                detail="Invalid username or password")
        token = await issue_token(session, user)
        await session.commit()
        return {"user": _public_user(user), "token": token}


async def _verify_google_id_token(id_token: str, client_id: str) -> dict[str, Any]:
    """Validate a Google ID token against the tokeninfo endpoint."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                "https://oauth2.googleapis.com/tokeninfo",
                params={"id_token": id_token})
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503,
                            detail=f"could not reach Google: {e}")
    if r.status_code != 200:
        raise HTTPException(status_code=401, detail="invalid Google credential")
    claims = r.json()
    if claims.get("aud") != client_id:
        raise HTTPException(status_code=401,
                            detail="Google credential audience mismatch")
    email = (claims.get("email") or "").strip().lower()
    if not email or str(claims.get("email_verified")) not in ("true", "True"):
        raise HTTPException(
            status_code=403,
            detail="Google account has no verified email address")
    return {"email": email, "name": claims.get("name") or email,
            "sub": claims.get("sub") or ""}


async def _username_for(session: Any, email: str) -> str:
    """Derive a unique username from an email's local part."""
    import re

    from sqlalchemy import func, select

    base = re.sub(r"[^a-z0-9_-]", "", email.split("@", 1)[0].lower()) or "user"
    base = base[:60]
    name = base
    for i in range(2, 1000):
        taken = (await session.execute(
            select(User.id).where(User.username == name))
        ).scalar_one_or_none()
        if not taken:
            return name
        name = f"{base}{i}"[:64]
    raise HTTPException(status_code=500,
                        detail="could not allocate a username")


@router.post("/google")
async def google_sign_in(body: GoogleBody) -> dict[str, Any]:
    """Google Identity Services credential → local session token.

    Existing users are matched by verified email; new emails register an
    account and honor the referral gate like /v1/auth/register."""
    from sqlalchemy import func, select

    client_id = get_settings().google_client_id
    if not client_id:
        raise HTTPException(status_code=404,
                            detail="Google sign-in is not configured")
    claims = await _verify_google_id_token(body.credential, client_id)
    email = claims["email"].strip().lower()
    factory = get_session_factory()
    async with factory() as session:
        user = (await session.execute(
            select(User).where(
                func.lower(User.email) == email))
        ).scalar_one_or_none()
        if user is None:
            expected = get_settings().referral_code
            if expected and not hmac.compare_digest(
                    body.referral_code, expected):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Invalid or missing referral code")
            import secrets

            try:
                user = await create_user(
                    session,
                    await _username_for(session, email),
                    secrets.token_urlsafe(24),
                    email, claims["name"])
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
        if user.disabled:
            raise HTTPException(status_code=403, detail="account is disabled")
        token = await issue_token(session, user)
        await session.commit()
        return {"user": _public_user(user), "token": token}


@router.post("/logout")
async def logout(request: Request) -> dict[str, bool]:
    token = bearer_token(request.headers.get("authorization"))
    if token:
        factory = get_session_factory()
        async with factory() as session:
            await revoke_token(session, token)
            await session.commit()
    return {"ok": True}


@router.get("/me")
async def me(user: User | None = Depends(optional_user)) -> dict[str, Any]:
    if not user:
        return {"user": None, "auth_required": auth_required(),
                "referral_required": referral_required(),
                "google_client_id": get_settings().google_client_id,
                "plan": "free", "limits": plan_limits("free")}
    return {"user": _public_user(user), "auth_required": auth_required(),
            "referral_required": referral_required(),
            "google_client_id": get_settings().google_client_id,
            "plan": user.plan, "limits": plan_limits(user.plan)}


class ProfileBody(BaseModel):
    display_name: str | None = Field(default=None, max_length=128)
    bio: str | None = Field(default=None, max_length=512)
    avatar_url: str | None = Field(default=None, max_length=512)
    github_username: str | None = Field(default=None, max_length=64)
    pronouns: str | None = Field(default=None, max_length=64)
    company: str | None = Field(default=None, max_length=128)
    location: str | None = Field(default=None, max_length=128)
    website: str | None = Field(default=None, max_length=512)


@router.patch("/me")
async def update_profile(
        body: ProfileBody,
        user: User | None = Depends(optional_user)) -> dict[str, Any]:
    """Update the signed-in user's profile fields."""
    if not user:
        raise _auth_required()
    if body.display_name is not None:
        user.display_name = body.display_name.strip() or user.username
    if body.bio is not None:
        user.bio = body.bio.strip()
    if body.avatar_url is not None:
        url = body.avatar_url.strip()
        if url and not url.startswith(("https://", "http://")):
            raise HTTPException(status_code=400,
                                detail="avatar_url must be an http(s) URL")
        user.avatar_url = url
    if body.github_username is not None:
        gh = body.github_username.strip().lstrip("@")
        gh = gh.rsplit("github.com/", 1)[-1].strip("/") if "github.com" in gh else gh
        if gh and not all(c.isalnum() or c == "-" for c in gh):
            raise HTTPException(status_code=400,
                                detail="invalid GitHub username")
        user.github_username = gh[:39]
    if body.pronouns is not None:
        user.pronouns = body.pronouns.strip()
    if body.company is not None:
        user.company = body.company.strip()
    if body.location is not None:
        user.location = body.location.strip()
    if body.website is not None:
        site = body.website.strip()
        if site and not site.startswith(("https://", "http://")):
            raise HTTPException(status_code=400,
                                detail="website must be an http(s) URL")
        user.website = site
    factory = get_session_factory()
    async with factory() as session:
        merged = await session.merge(user)
        await session.commit()
        return {"user": _public_user(merged)}


@router.get("/github")
async def github_profile(
        username: str = "",
        user: User | None = Depends(optional_user)) -> dict[str, Any]:
    """GitHub profile for the linked (or given) username."""
    if not user:
        raise _auth_required()
    login = (username or user.github_username or "").strip()
    if not login:
        raise HTTPException(
            status_code=400,
            detail="Link a GitHub username on your profile first")
    from app.services.github import lookup_github

    gh = await lookup_github(login)
    if gh is None:
        raise HTTPException(status_code=404,
                            detail="GitHub user not found or unreachable")
    return {"github": gh}


@router.get("/plans")
async def plans() -> dict[str, Any]:
    return {"plans": [{"name": k, **v} for k, v in PLAN_LIMITS.items()]}


@router.get("/usage")
async def my_usage(user: User | None = Depends(optional_user)) -> dict[str, Any]:
    factory = get_session_factory()
    async with factory() as session:
        return await usage_svc.summary(session, subject_for(user))
