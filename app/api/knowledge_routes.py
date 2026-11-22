"""/v1/knowledge — collections, document upload, retrieval, and Q&A."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import (APIRouter, Depends, File, Form, HTTPException,
                     Request, UploadFile)
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (optional_user, require_capability, require_user,
                          subject_for)
from app.db import get_session
from app.models import Collection, Document, DocumentChunk, User
from app.services import documents as docs
from app.services.auth import plan_limits

router = APIRouter(prefix="/v1/knowledge", tags=["knowledge"],
                   dependencies=[Depends(require_capability(
                                     "library_captures"))])


class CollectionBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""


def _doc_out(d: Document) -> dict[str, Any]:
    return {"id": d.id, "name": d.name, "mime": d.mime, "size": d.size,
            "status": d.status, "error": d.error or None,
            "collection_id": d.collection_id, "meta": d.meta,
            "created_at": d.created_at.isoformat()}


def _scope(user: User | None) -> Optional[int]:
    return user.id if user else None


@router.get("/collections")
async def collections(session: AsyncSession = Depends(get_session),
                      user: User | None = Depends(optional_user)):
    return {"collections": await docs.list_collections(session, _scope(user))}


@router.post("/collections", status_code=201)
async def create_collection(body: CollectionBody,
                            session: AsyncSession = Depends(get_session),
                            user: User | None = Depends(optional_user)):
    if user is not None:
        lim = plan_limits(user.plan).get("collections", 0)
        if lim > 0:
            n = (await session.execute(select(func.count(Collection.id))
                 .where(Collection.owner_id == user.id))).scalar() or 0
            if n >= lim:
                raise HTTPException(
                    429, f"collection limit reached for the {user.plan} "
                         f"plan ({n}/{lim})")
    c = Collection(name=body.name.strip(), description=body.description,
                   owner_id=_scope(user))
    session.add(c)
    await session.commit()
    return {"id": c.id, "name": c.name, "description": c.description}


@router.delete("/collections/{cid}")
async def delete_collection(cid: int,
                            session: AsyncSession = Depends(get_session),
                            user: User | None = Depends(optional_user)):
    c = await session.get(Collection, cid)
    if not c:
        raise HTTPException(404, "collection not found")
    if c.owner_id is not None and c.owner_id != _scope(user):
        raise HTTPException(403, "not your collection")
    await session.execute(delete(Document).where(Document.collection_id == cid))
    await session.delete(c)
    await session.commit()
    return {"ok": True}


@router.get("/documents")
async def list_documents(collection_id: Optional[int] = None,
                         session: AsyncSession = Depends(get_session),
                         user: User | None = Depends(optional_user)):
    q = select(Document).order_by(Document.created_at.desc())
    if collection_id is not None:
        q = q.where(Document.collection_id == collection_id)
    if user:
        q = q.where(or_(Document.owner_id == user.id, Document.owner_id.is_(None)))
    rows = (await session.execute(q)).scalars().all()
    return {"documents": [_doc_out(d) for d in rows]}


@router.post("/documents", status_code=201)
async def upload_document(file: UploadFile = File(...),
                          collection_id: Optional[int] = Form(None),
                          session: AsyncSession = Depends(get_session),
                          user: User | None = Depends(optional_user)):
    if user is not None:
        lim = plan_limits(user.plan).get("documents", 0)
        if lim > 0:
            n = (await session.execute(select(func.count(Document.id))
                 .where(Document.owner_id == user.id))).scalar() or 0
            if n >= lim:
                raise HTTPException(
                    429, f"document limit reached for the {user.plan} "
                         f"plan ({n}/{lim})")
    data = await file.read()
    try:
        doc = await docs.ingest(session, name=file.filename or "upload",
                                data=data, owner_id=_scope(user),
                                collection_id=collection_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    await session.commit()
    return _doc_out(doc)


@router.delete("/documents/{did}")
async def delete_document(did: int,
                          session: AsyncSession = Depends(get_session),
                          user: User | None = Depends(optional_user)):
    d = await session.get(Document, did)
    if not d:
        raise HTTPException(404, "document not found")
    if d.owner_id is not None and d.owner_id != _scope(user):
        raise HTTPException(403, "not your document")
    await session.execute(
        delete(DocumentChunk).where(DocumentChunk.document_id == did))
    await session.delete(d)
    await session.commit()
    return {"ok": True}


@router.get("/search")
async def search_knowledge(q: str, collection_id: Optional[int] = None,
                           k: int = 8,
                           session: AsyncSession = Depends(get_session),
                           user: User | None = Depends(optional_user)):
    results = await docs.search(session, q, owner_id=_scope(user),
                                collection_id=collection_id, k=min(k, 20))
    return {"query": q, "results": results}
