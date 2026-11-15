"""Detached chat jobs: replay, re-attach, reply persistence."""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Conversation
from app.services import chat_jobs


def _frame(obj: dict) -> bytes:
    import json
    return f"data: {json.dumps(obj)}\n\n".encode()


async def _fake_gen(frames: list[bytes], delay: float = 0.0):
    for f in frames:
        yield f
        if delay:
            await asyncio.sleep(delay)


@pytest.fixture(autouse=True)
def _clean_registry():
    chat_jobs._jobs.clear()
    yield
    chat_jobs._jobs.clear()


@pytest.fixture
async def patched_factory(engine, monkeypatch):
    """_persist_reply opens its own session via get_session_factory()."""
    from app.db import get_session_factory  # noqa: F401
    factory = async_sessionmaker(engine, class_=AsyncSession,
                                 expire_on_commit=False)
    monkeypatch.setattr("app.db.get_session_factory", lambda: factory)
    return factory


class TestJobLifecycle:
    async def test_stream_replays_and_finishes(self):
        frames = [
            _frame({"freellm": {"event": "routing", "text": "r"}}),
            _frame({"choices": [{"delta": {"content": "Hello "}}]}),
            _frame({"choices": [{"delta": {"content": "world"}}]}),
        ]
        job = chat_jobs.start("c1", _fake_gen(frames))
        out = [f async for f in job.stream()]
        assert job.done and out[-1] == b"data: [DONE]\n\n"
        assert job.reply == "Hello world"
        assert out[:3] == frames

    async def test_replace_content_wins(self):
        frames = [
            _frame({"choices": [{"delta": {"content": "raw "}}]}),
            _frame({"freellm": {"event": "replace_content",
                                "content": "enforced answer"}}),
        ]
        job = chat_jobs.start("c2", _fake_gen(frames))
        async for _ in job.stream():
            pass
        assert job.reply == "enforced answer"

    async def test_reattach_running_job(self):
        async def slow():
            yield _frame({"choices": [{"delta": {"content": "x"}}]})
            await asyncio.sleep(60)
        job = chat_jobs.start("c3", slow())
        assert chat_jobs.get_running("c3") is job
        assert chat_jobs.start("c3", slow()) is job  # dedupes
        job.cancel()
        job2 = chat_jobs.get("c3")
        assert job2 is job
        async for _ in job.stream():  # cancel → done
            pass
        assert job.done
        assert chat_jobs.get_running("c3") is None

    async def test_late_subscriber_gets_full_replay(self):
        frames = [_frame({"choices": [{"delta": {"content": "a"}}]})]
        job = chat_jobs.start("c4", _fake_gen(frames))
        while not job.done:
            await asyncio.sleep(0.01)
        # subscriber arriving after completion still sees everything
        out = [f async for f in job.stream()]
        assert out[0] == frames[0] and out[-1] == b"data: [DONE]\n\n"

    async def test_status_shape(self):
        assert chat_jobs.status(None) == {"running": False, "done": False}
        job = chat_jobs.start("c5", _fake_gen([_frame({"choices": []})]))
        async for _ in job.stream():
            pass
        st = chat_jobs.status(job)
        assert st["done"] is True and st["running"] is False
        assert "elapsed_s" in st


class TestPersistReply:
    async def test_reply_appended_to_conversation(self, patched_factory):
        conv_id = "live-conv"
        frames = [_frame({"choices": [{"delta": {"content": "final text"}}]})]
        job = chat_jobs.start(conv_id, _fake_gen(frames))
        async for _ in job.stream():
            pass
        async with patched_factory() as s:
            conv = (
                await s.execute(
                    select(Conversation).where(
                        Conversation.client_id == conv_id))
            ).scalar_one()
        msgs = conv.messages
        assert msgs[-1]["role"] == "assistant"
        assert msgs[-1]["text"] == "final text"

    async def test_no_duplicate_append(self, patched_factory):
        conv_id = "live-dup"
        async with patched_factory() as s:
            s.add(Conversation(client_id=conv_id, name="c", messages=[
                {"role": "user", "text": "hi"},
                {"role": "assistant", "text": "same reply here"},
            ]))
            await s.commit()
        frames = [_frame({"choices": [{"delta": {"content": "same reply here"}}]})]
        job = chat_jobs.start(conv_id, _fake_gen(frames))
        async for _ in job.stream():
            pass
        async with patched_factory() as s:
            conv = (
                await s.execute(
                    select(Conversation).where(
                        Conversation.client_id == conv_id))
            ).scalar_one()
        assert len(conv.messages) == 2  # not re-appended
