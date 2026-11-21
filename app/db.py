"""Async SQLAlchemy engine/session setup. SQLite runs in WAL mode."""

from __future__ import annotations

from typing import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        url = get_settings().database_url
        if url.startswith("sqlite"):
            from pathlib import Path

            db_path = url.split("///")[-1]
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        _engine = create_async_engine(url, echo=False, pool_pre_ping=True)

        @event.listens_for(_engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _record):  # type: ignore[no-untyped-def]
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(), class_=AsyncSession, expire_on_commit=False
        )
    return _session_factory


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with get_session_factory()() as session:
        yield session


async def init_db() -> None:
    """Create all tables (dev convenience; Alembic owns migrations)."""
    from app.models import Base

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Lightweight column migration for existing DBs (create_all won't
        # alter existing tables).
        cols = {
            r[1]
            for r in (await conn.exec_driver_sql(
                "PRAGMA table_info(request_logs)")).fetchall()
        }
        for col in ("request_input", "response_text"):
            if col not in cols:
                await conn.exec_driver_sql(
                    f"ALTER TABLE request_logs ADD COLUMN {col} TEXT")
        # create_all adds new tables but never alters existing ones.
        rr_cols = {
            r[1]
            for r in (await conn.exec_driver_sql(
                "PRAGMA table_info(research_runs)")).fetchall()
        }
        if rr_cols and "conversation_id" not in rr_cols:
            await conn.exec_driver_sql(
                "ALTER TABLE research_runs ADD COLUMN conversation_id VARCHAR(64)")
        p_cols = {
            r[1]
            for r in (await conn.exec_driver_sql(
                "PRAGMA table_info(providers)")).fetchall()
        }
        if p_cols and "pricing" not in p_cols:
            await conn.exec_driver_sql(
                "ALTER TABLE providers ADD COLUMN pricing JSON")
        # Extended character columns for the roleplay studio.
        c_cols = {
            r[1]
            for r in (await conn.exec_driver_sql(
                "PRAGMA table_info(characters)")).fetchall()
        }
        rp_cols = {
            r[1]
            for r in (await conn.exec_driver_sql(
                "PRAGMA table_info(roleplay_rooms)")).fetchall()
        }
        if rp_cols:
            rp_adds = {
                "summary": "TEXT DEFAULT ''",
                "mood": "VARCHAR(32) DEFAULT ''",
                "paused_ids": "JSON",
                "director_notes": "TEXT DEFAULT ''",
                "tools_enabled": "BOOLEAN DEFAULT 0",
            }
            for col, ddl in rp_adds.items():
                if col not in rp_cols:
                    await conn.exec_driver_sql(
                        f"ALTER TABLE roleplay_rooms ADD COLUMN {col} {ddl}")
        char_adds = {
            "background": "TEXT DEFAULT ''",
            "knowledge_domain": "TEXT DEFAULT ''",
            "motivations": "TEXT DEFAULT ''",
            "boundaries": "TEXT DEFAULT ''",
            "example_dialogue": "TEXT DEFAULT ''",
            "speaking_style": "TEXT DEFAULT ''",
            "category": "VARCHAR(64) DEFAULT 'custom'",
            "tags": "JSON",
            "visibility": "VARCHAR(16) DEFAULT 'public'",
            "creator_id": "INTEGER",
            "avatar_url": "VARCHAR(512) DEFAULT ''",
        }
        for col, ddl in char_adds.items():
            if c_cols and col not in c_cols:
                await conn.exec_driver_sql(
                    f"ALTER TABLE characters ADD COLUMN {col} {ddl}")
        # Profile fields on users (bio / avatar / GitHub link).
        u_cols = {
            r[1]
            for r in (await conn.exec_driver_sql(
                "PRAGMA table_info(users)")).fetchall()
        }
        u_adds = {
            "bio": "VARCHAR(512) DEFAULT ''",
            "avatar_url": "VARCHAR(512) DEFAULT ''",
            "github_username": "VARCHAR(64) DEFAULT ''",
            "pronouns": "VARCHAR(64) DEFAULT ''",
            "company": "VARCHAR(128) DEFAULT ''",
            "location": "VARCHAR(128) DEFAULT ''",
            "website": "VARCHAR(512) DEFAULT ''",
        }
        for col, ddl in u_adds.items():
            if u_cols and col not in u_cols:
                await conn.exec_driver_sql(
                    f"ALTER TABLE users ADD COLUMN {col} {ddl}")
        # Activity-log v2: normalized event fields on the agent ledger.
        ae_cols = {
            r[1]
            for r in (await conn.exec_driver_sql(
                "PRAGMA table_info(agent_events)")).fetchall()
        }
        ae_adds = {
            "kind": "VARCHAR(24) DEFAULT 'note'",
            "title": "VARCHAR(512) DEFAULT ''",
            "parent": "VARCHAR(64) DEFAULT ''",
            "level": "VARCHAR(16) DEFAULT 'normal'",
            "payload": "JSON",
        }
        for col, ddl in ae_adds.items():
            if ae_cols and col not in ae_cols:
                await conn.exec_driver_sql(
                    f"ALTER TABLE agent_events ADD COLUMN {col} {ddl}")


async def close_db() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
