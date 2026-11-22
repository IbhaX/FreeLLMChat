"""Artifactory API — browse, preview and delete generated artifacts."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import optional_user
from app.db import get_session
from app.models import GeneratedArtifact, User

router = APIRouter(prefix="/v1/artifacts")


def _out(a: GeneratedArtifact) -> dict[str, Any]:
    return {
        "id": a.id,
        "name": a.name,
        "title": a.title,
        "kind": a.kind,
        "mime": a.mime,
        "size": a.size,
        "url": f"/media/{a.path}",
        "source": a.source,
        "conversation_id": a.conversation_id,
        "meta": a.meta or {},
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


@router.get("")
async def list_artifacts(
    request: Request,
    kind: str | None = None,
    q: str | None = None,
    limit: int = 300,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Newest-first catalog. Signed-in users see their own plus
    ownerless (anonymous-local) artifacts; admin-token/open mode sees all."""
    from app.services import artifacts as _artifacts

    user = await optional_user(request)
    await _artifacts.backfill(session)
    stmt = select(GeneratedArtifact).order_by(GeneratedArtifact.created_at.desc()).limit(
        max(1, min(limit, 1000)))
    if user is not None:
        stmt = stmt.where(
            or_(GeneratedArtifact.owner_id == user.id, GeneratedArtifact.owner_id.is_(None)))
    if kind:
        stmt = stmt.where(GeneratedArtifact.kind == kind)
    rows = (await session.execute(stmt)).scalars().all()
    if q:
        needle = q.lower()
        rows = [a for a in rows if needle in a.name.lower()
                or needle in (a.title or "").lower()
                or needle in (a.source or "").lower()]
    return {"artifacts": [_out(a) for a in rows]}


@router.get("/{artifact_id}")
async def get_artifact(
    artifact_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    user = await optional_user(request)
    a = await session.get(GeneratedArtifact, artifact_id)
    if not a:
        raise HTTPException(404, "artifact not found")
    if user is not None and a.owner_id not in (None, user.id):
        raise HTTPException(403, "not your artifact")
    return _out(a)


@router.delete("/{artifact_id}", status_code=204)
async def delete_artifact(
    artifact_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> None:
    user: User | None = await optional_user(request)
    a = await session.get(GeneratedArtifact, artifact_id)
    if not a:
        raise HTTPException(404, "artifact not found")
    if user is not None and a.owner_id not in (None, user.id):
        raise HTTPException(403, "not your artifact")
    from app.services import artifacts as _artifacts

    try:
        (_artifacts.media_dir() / a.path).unlink(missing_ok=True)
    except OSError:
        pass
    await session.delete(a)
    await session.commit()
