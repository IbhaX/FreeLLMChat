"""Tests for automatic knowledge capture into the library."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Collection, Document, DocumentChunk
from app.services import documents, knowledge_capture


@pytest.fixture()
def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async def _mk():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return async_sessionmaker(engine, expire_on_commit=False)

    factory = asyncio.run(_mk())
    yield factory


@pytest.fixture(autouse=True)
def _patch_factory(monkeypatch, session_factory):
    """knowledge_capture resolves the app session factory lazily inside
    capture() — point it at the test engine."""
    monkeypatch.setattr("app.db.get_session_factory",
                        lambda: session_factory)


def _run(coro):
    return asyncio.run(coro)


LONG_ANSWER = ("Photosynthesis converts light energy into chemical energy "
               "stored in glucose. It proceeds in two stages: the "
               "light-dependent reactions in the thylakoid membranes, which "
               "split water and produce ATP and NADPH, and the Calvin cycle "
               "in the stroma, which fixes carbon dioxide into sugar. ")


def test_worth_capturing_filters_small_talk():
    assert not knowledge_capture.worth_capturing("hi", "Hello! How can I help?")
    assert not knowledge_capture.worth_capturing("thanks", "You're welcome!")
    assert not knowledge_capture.worth_capturing("", LONG_ANSWER)
    assert not knowledge_capture.worth_capturing("q", "")
    assert knowledge_capture.worth_capturing("explain photosynthesis", LONG_ANSWER)
    # short replies carrying code or links still count as knowledge
    assert knowledge_capture.worth_capturing(
        "how?", "```python\nprint(1)\n```")
    assert knowledge_capture.worth_capturing(
        "source?", "See https://example.com/ref for the citation.")


def test_capture_creates_collection_document_and_chunks(session_factory):
    async def go():
        doc_id = await knowledge_capture.capture(
            7, "chat", "explain photosynthesis", LONG_ANSWER)
        assert doc_id is not None
        async with session_factory() as s:
            col = (await s.execute(
                select(Collection).where(Collection.name == "AI Captures"))
            ).scalar_one()
            assert col.owner_id == 7
            doc = (await s.execute(
                select(Document).where(Document.collection_id == col.id))
            ).scalar_one()
            assert doc.status == "ready"
            assert doc.meta["capture"] == "chat"
            chunks = (await s.execute(
                select(func.count(DocumentChunk.id)).where(
                    DocumentChunk.document_id == doc.id))).scalar()
            assert chunks >= 1
            hits = await documents.search(
                s, "Calvin cycle carbon fixation", owner_id=7)
            assert hits and hits[0]["document"] == doc.name
    _run(go())


def test_capture_appends_to_same_daily_document(session_factory):
    async def go():
        first = await knowledge_capture.capture(
            7, "chat", "explain photosynthesis", LONG_ANSWER)
        second = await knowledge_capture.capture(
            7, "chat", "what is DNA?", LONG_ANSWER.replace(
                "Photosynthesis", "DNA replication"))
        assert first == second
        async with session_factory() as s:
            n_docs = (await s.execute(
                select(func.count(Document.id)))).scalar()
            assert n_docs == 1
            hits = await documents.search(
                s, "DNA replication", owner_id=7)
            assert hits
    _run(go())


def test_capture_deduplicates_identical_turn(session_factory):
    async def go():
        await knowledge_capture.capture(
            7, "chat", "explain photosynthesis", LONG_ANSWER)
        async with session_factory() as s:
            before = (await s.execute(
                select(func.count(DocumentChunk.id)))).scalar()
        await knowledge_capture.capture(
            7, "chat", "explain photosynthesis", LONG_ANSWER)
        async with session_factory() as s:
            after = (await s.execute(
                select(func.count(DocumentChunk.id)))).scalar()
        assert after == before
    _run(go())


def test_capture_skips_small_talk(session_factory):
    async def go():
        assert await knowledge_capture.capture(
            7, "chat", "hi", "Hello!") is None
        async with session_factory() as s:
            assert (await s.execute(
                select(func.count(Document.id)))).scalar() == 0
            assert (await s.execute(
                select(func.count(Collection.id)))).scalar() == 0
    _run(go())


def test_capture_is_owner_scoped(session_factory):
    async def go():
        await knowledge_capture.capture(
            7, "chat", "explain photosynthesis", LONG_ANSWER)
        await knowledge_capture.capture(
            9, "agent", "explain photosynthesis", LONG_ANSWER)
        async with session_factory() as s:
            cols = (await s.execute(select(Collection))).scalars().all()
            assert len(cols) == 2
            assert {c.owner_id for c in cols} == {7, 9}
            hits7 = await documents.search(
                s, "photosynthesis", owner_id=7)
            hits9 = await documents.search(
                s, "photosynthesis", owner_id=9)
            assert hits7 and hits9
    _run(go())


def test_schedule_never_raises_and_runs(session_factory):
    async def go():
        knowledge_capture.schedule(
            7, "chat", "explain photosynthesis", LONG_ANSWER)
        await asyncio.sleep(0)  # let the task run
        await asyncio.sleep(0.05)
        async with session_factory() as s:
            assert (await s.execute(
                select(func.count(Document.id)))).scalar() == 1
    _run(go())
