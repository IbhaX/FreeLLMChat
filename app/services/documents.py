"""Knowledge workspace: document ingestion, chunking, and retrieval.

Extraction supports txt/md/csv/json/html/pdf/docx. Retrieval is
local-first: a lexical TF-IDF scorer over chunks, upgraded to a hybrid
cosine+lexical score when a local sentence-transformers model is
available. No external service is required for the basic path.
"""

from __future__ import annotations

import asyncio
import io
import math
import re
from collections import Counter
from typing import Any, Optional

from sqlalchemy import func, or_, select

from app.models import Collection, Document, DocumentChunk

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
SUPPORTED_EXT = {".txt", ".md", ".markdown", ".csv", ".json", ".html",
                 ".htm", ".pdf", ".docx"}

_ext_re = re.compile(r"\.[a-zA-Z0-9]+$")
_word_re = re.compile(r"[a-z0-9]{2,}")


def ext_of(name: str) -> str:
    m = _ext_re.search(name or "")
    return m.group(0).lower() if m else ""


def extract_text(name: str, data: bytes) -> str:
    """Extract plain text from a supported file. Raises ValueError for
    unsupported types or empty content."""
    ext = ext_of(name)
    if ext not in SUPPORTED_EXT:
        raise ValueError(f"unsupported file type '{ext or name}'")
    if ext in (".txt", ".md", ".markdown", ".csv", ".json"):
        return data.decode("utf-8", errors="replace")
    if ext in (".html", ".htm"):
        from bs4 import BeautifulSoup
        return BeautifulSoup(data, "html.parser").get_text("\n")
    if ext == ".docx":
        import docx
        doc = docx.Document(io.BytesIO(data))
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    if ext == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    return ""


def chunk_text(text: str, *, target: int = 1200, overlap: int = 160) -> list[str]:
    """Split into ~`target`-char chunks on paragraph boundaries with a
    small overlap so retrieval keeps context."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        if len(buf) + len(p) + 2 > target and buf:
            chunks.append(buf.strip())
            buf = buf[-overlap:] + "\n\n" + p if overlap else p
        else:
            buf = (buf + "\n\n" + p) if buf else p
        while len(buf) > target * 2:  # a single huge paragraph
            chunks.append(buf[:target].strip())
            buf = buf[target - overlap:]
    if buf.strip():
        chunks.append(buf.strip())
    return chunks


# ------------------------------------------------------------- embedding

_embedder: Any = None
_embedder_tried = False


def _local_embedder() -> Any:
    """Lazy-load a local sentence-transformers model; None when offline."""
    global _embedder, _embedder_tried
    if _embedder_tried:
        return _embedder
    _embedder_tried = True
    try:
        from sentence_transformers import SentenceTransformer
        _embedder = SentenceTransformer("all-MiniLM-L6-v2")
    except Exception:
        _embedder = None
    return _embedder


def _embed(texts: list[str]) -> list[Optional[list[float]]]:
    model = _local_embedder()
    if not model:
        return [None] * len(texts)
    try:
        vecs = model.encode(texts, normalize_embeddings=True)
        return [v.tolist() for v in vecs]
    except Exception:
        return [None] * len(texts)


# ------------------------------------------------------------ retrieval

def _tokens(text: str) -> Counter:
    return Counter(_word_re.findall((text or "").lower()))


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


async def ingest(session: Any, *, name: str, data: bytes,
                 owner_id: Optional[int], collection_id: Optional[int]) -> Document:
    """Extract → chunk → (optionally) embed → persist a document."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("file exceeds the 25MB limit")
    text = extract_text(name, data)
    if not text.strip():
        raise ValueError("no readable text found in the file")
    chunks: list[str] = []
    embeds: list[Any] = []
    failure: Optional[Exception] = None
    try:
        chunks = chunk_text(text)
        embeds = await asyncio.to_thread(_embed, chunks)
    except Exception as e:  # keep the row; status surfaces the failure
        failure = e
    doc = Document(name=name[:256], size=len(data),
                   mime=ext_of(name).lstrip("."), owner_id=owner_id,
                   collection_id=collection_id, status="processing",
                   meta={"chars": len(text)})
    session.add(doc)
    await session.flush()
    try:
        if failure is not None:
            raise failure
        for i, (c, e) in enumerate(zip(chunks, embeds)):
            session.add(DocumentChunk(
                document_id=doc.id, ord=i, text=c, embedding=e,
                tokens=len(c) // 4))
        doc.status = "ready"
        doc.meta = {**doc.meta, "chunks": len(chunks),
                    "embedded": embeds[0] is not None if embeds else False}
    except Exception as e:  # keep the row; status surfaces the failure
        doc.status = "error"
        doc.error = str(e)[:400]
    await session.flush()
    return doc


async def search(session: Any, query: str, *, owner_id: Optional[int],
                 collection_id: Optional[int] = None, k: int = 8) -> list[dict[str, Any]]:
    """Hybrid retrieval: lexical TF-IDF always; cosine when chunks carry
    embeddings. Scoped to the owner's documents."""
    q = (select(DocumentChunk, Document)
         .join(Document, DocumentChunk.document_id == Document.id)
         .where(Document.status == "ready"))
    if owner_id is not None:
        q = q.where(or_(Document.owner_id == owner_id, Document.owner_id.is_(None)))
    if collection_id is not None:
        q = q.where(Document.collection_id == collection_id)
    rows = (await session.execute(q)).all()
    if not rows:
        return []

    qv = _embed([query])[0]
    qtok = _tokens(query)
    scored: list[tuple[float, DocumentChunk, Document]] = []
    for chunk, doc in rows:
        ctok = _tokens(chunk.text)
        # lexical score: weighted token overlap
        common = set(qtok) & set(ctok)
        lex = sum(min(qtok[t], 2) * math.sqrt(ctok[t]) for t in common)
        lex = lex / math.sqrt(1 + len(chunk.text) / 1200)
        score = lex
        if qv and chunk.embedding:
            score = 0.65 * (50.0 * _cosine(qv, chunk.embedding)) + 0.35 * lex
        if score > 0:
            scored.append((score, chunk, doc))
    scored.sort(key=lambda t: -t[0])
    return [{"score": round(s, 4), "text": c.text[:2000],
             "document": d.name, "document_id": d.id, "ord": c.ord}
            for s, c, d in scored[:k]]


async def list_collections(session: Any, owner_id: Optional[int]) -> list[dict[str, Any]]:
    q = select(Collection)
    if owner_id is not None:
        q = q.where(or_(Collection.owner_id == owner_id, Collection.owner_id.is_(None)))
    cols = (await session.execute(q.order_by(Collection.created_at.desc()))).scalars().all()
    out = []
    for c in cols:
        n = (await session.execute(
            select(func.count(Document.id)).where(Document.collection_id == c.id))
        ).scalar() or 0
        out.append({"id": c.id, "name": c.name, "description": c.description,
                    "documents": n, "created_at": c.created_at.isoformat()})
    return out
