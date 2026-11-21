"""/v1/auth — registration, login, logout, profile, plans, usage."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.deps import optional_user, require_gateway_key, subject_for
from app.db import get_session_factory
from app.models import User
from app.services import usage as usage_svc
from app.services.auth import (PLAN_LIMITS, authenticate, auth_required,
                               bearer_token, create_user, issue_token,
                               plan_limits, revoke_token)

router = APIRouter(prefix="/v1/auth", tags=["auth"],
                   dependencies=[Depends(require_gateway_key)])


class RegisterBody(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=256)
    email: str = ""
    display_name: str = ""


class LoginBody(BaseModel):
    username: str
    password: str


def _public_user(u: User) -> dict[str, Any]:
    return {"id": u.id, "username": u.username, "email": u.email,
            "display_name": u.display_name, "role": u.role, "plan": u.plan,
            "created_at": u.created_at.isoformat() if u.created_at else None}


@router.post("/register", status_code=201)
async def register(body: RegisterBody) -> dict[str, Any]:
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
                "plan": "free", "limits": plan_limits("free")}
    return {"user": _public_user(user), "auth_required": auth_required(),
            "plan": user.plan, "limits": plan_limits(user.plan)}


@router.get("/plans")
async def plans() -> dict[str, Any]:
    return {"plans": [{"name": k, **v} for k, v in PLAN_LIMITS.items()]}


@router.get("/usage")
async def my_usage(user: User | None = Depends(optional_user)) -> dict[str, Any]:
    factory = get_session_factory()
    async with factory() as session:
        return await usage_svc.summary(session, subject_for(user))
