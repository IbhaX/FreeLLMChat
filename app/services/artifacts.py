"""Artifactory — catalog of every file agents generate.

Generation tools (``python_exec``, media generators, …) write files under
``data/media``; :func:`record` registers them in the ``artifacts`` table
with provenance from a per-request contextvar (``set_context`` — set at
route entry so detached jobs and pydantic tool loops inherit it).

:func:`backfill` sweeps the media dir so files written before the
registry existed (or by paths that bypassed ``record``) are catalogued
too.
"""

from __future__ import annotations

import contextvars
from pathlib import Path
from typing import Any

_CTX: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "artifact_ctx", default={})

_KIND_BY_EXT: dict[str, str] = {
    ".py": "code", ".js": "code", ".ts": "code", ".html": "code",
    ".css": "code", ".sql": "code", ".sh": "code", ".json": "code",
    ".yaml": "code", ".yml": "code", ".toml": "code",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image",
    ".svg": "image", ".webp": "image",
    ".mp3": "audio", ".wav": "audio", ".ogg": "audio", ".m4a": "audio",
    ".mp4": "video", ".webm": "video", ".mov": "video",
    ".pdf": "document", ".docx": "document", ".pptx": "document",
    ".txt": "document", ".md": "document", ".rtf": "document",
    ".xlsx": "data", ".csv": "data", ".tsv": "data", ".parquet": "data",
    ".zip": "archive", ".tar": "archive", ".gz": "archive",
}

_MIME_BY_EXT: dict[str, str] = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".svg": "image/svg+xml", ".webp": "image/webp",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".mp4": "video/mp4",
    ".pdf": "application/pdf", ".html": "text/html", ".json": "application/json",
    ".csv": "text/csv", ".md": "text/markdown", ".txt": "text/plain",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def set_context(**kw: Any) -> None:
    """Stamp provenance for artifacts recorded during this request/task."""
    _CTX.set({k: v for k, v in kw.items() if v is not None})


def current_context() -> dict[str, Any]:
    """The request-scoped context dict (``source``, ``conversation_id``,
    ``owner_id``). Connector tools read ``owner_id`` from this to resolve
    per-user credentials."""
    return _CTX.get()


def kind_for(name: str) -> str:
    return _KIND_BY_EXT.get(Path(name).suffix.lower(), "file")


def mime_for(name: str) -> str:
    return _MIME_BY_EXT.get(Path(name).suffix.lower(), "")


def media_dir() -> Path:
    """The shared generated-media dir (data/media)."""
    from app.config import get_settings

    p = get_settings().data_path / "media"
    p.mkdir(parents=True, exist_ok=True)
    return p


async def _insert(row: dict[str, Any]) -> None:
    from sqlalchemy import select

    from app.db import get_session_factory
    from app.models import GeneratedArtifact

    async with get_session_factory()() as s:
        exists = (await s.execute(
            select(GeneratedArtifact.id).where(GeneratedArtifact.path == row["path"]))
        ).first()
        if exists:
            return
        s.add(GeneratedArtifact(**row))
        await s.commit()


def record(fname: str, *, title: str = "", source: str = "",
           extra: dict[str, Any] | None = None) -> None:
    """Register ``fname`` (a file already in the media dir) as an artifact.

    Fire-and-forget: never blocks or fails the caller's tool result; a
    duplicate ``path`` is ignored so re-runs stay idempotent."""
    import asyncio
    import logging

    ctx = _CTX.get()
    row: dict[str, Any] = {
        "name": fname,
        "title": title or fname,
        "kind": kind_for(fname),
        "mime": mime_for(fname),
        "path": fname,
        "source": source or str(ctx.get("source") or "agent"),
        "conversation_id": ctx.get("conversation_id"),
        "owner_id": ctx.get("owner_id"),
        "meta": {"tool": ctx.get("tool"), **(extra or {})},
    }
    f = media_dir() / fname
    try:
        row["size"] = f.stat().st_size
    except OSError:
        pass
    try:
        asyncio.get_running_loop().create_task(_insert(row))
    except RuntimeError:
        # no loop (sync/test path) — record inline
        asyncio.run(_insert(row))
    except Exception:
        logging.getLogger("nova").exception("artifact record failed")


async def backfill(session: Any) -> int:
    """Catalog media-dir files that have no artifact row yet."""
    from sqlalchemy import select

    from app.models import GeneratedArtifact

    have = {
        p for (p,) in (await session.execute(select(GeneratedArtifact.path))).all()
    }
    n = 0
    for f in sorted(media_dir().iterdir()):
        if not f.is_file() or f.name in have:
            continue
        session.add(GeneratedArtifact(
            name=f.name, title=f.name, kind=kind_for(f.name),
            mime=mime_for(f.name), size=f.stat().st_size,
            path=f.name, source="media",
        ))
        n += 1
    if n:
        await session.commit()
    return n
