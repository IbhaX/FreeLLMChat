"""Tests for auth, usage metering, documents, roleplay and custom skills."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base
from app.services import auth, documents, roleplay, usage


@pytest.fixture()
def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async def _mk():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return async_sessionmaker(engine, expire_on_commit=False)

    factory = asyncio.run(_mk())
    yield factory


def _run(coro):
    return asyncio.run(coro)


def test_extract_text_formats():
    assert "hello" in documents.extract_text("a.txt", b"hello world")
    md = documents.extract_text("a.md", b"# Title\n\nbody text")
    assert "Title" in md
    html = documents.extract_text("a.html", b"<html><body><p>para</p></body></html>")
    assert "para" in html
    with pytest.raises(ValueError):
        documents.extract_text("a.exe", b"\x00\x01")


def test_chunk_text_overlap():
    text = "\n\n".join(f"paragraph {i} " + "x" * 400 for i in range(10))
    chunks = documents.chunk_text(text, target=600, overlap=80)
    assert len(chunks) >= 5
    assert all(len(c) <= 1300 for c in chunks)


def test_auth_user_lifecycle(session_factory):
    async def go():
        async with session_factory() as s:
            u = await auth.create_user(s, "Tester", "password123")
            assert u.role == "admin"  # first user is admin
            assert await auth.authenticate(s, "tester", "password123")
            assert not await auth.authenticate(s, "tester", "wrong")
            token = await auth.issue_token(s, u)
            assert await auth.resolve_token(s, token)
            await auth.revoke_token(s, token)
            assert await auth.resolve_token(s, token) is None

            with pytest.raises(ValueError):
                await auth.create_user(s, "tester", "password123")
            with pytest.raises(ValueError):
                await auth.create_user(s, "ab", "password123")
            with pytest.raises(ValueError):
                await auth.create_user(s, "validname", "short")
    _run(go())


def test_usage_quota(session_factory):
    async def go():
        async with session_factory() as s:
            for _ in range(3):
                await usage.record(s, "u1", "chat", tokens_in=10)
            assert await usage.today_count(s, "u1") == 3
            assert await usage.today_count(s, "u1", "chat") == 3
            ok, _ = await usage.check_quota(s, "u1", "chat", "free")
            assert ok
            # exhaust research quota (free = 5/day)
            for _ in range(5):
                await usage.record(s, "u1", "research")
            ok, reason = await usage.check_quota(s, "u1", "research", "free")
            assert not ok and "limit" in reason
            summ = await usage.summary(s, "u1")
            assert summ["by_kind"]["chat"]["events"] == 3
            assert summ["by_kind"]["research"]["tokens_in"] == 0
    _run(go())


def test_document_ingest_and_search(session_factory):
    async def go():
        async with session_factory() as s:
            text = (b"Photosynthesis converts light into chemical energy.\n\n"
                    b"The Calvin cycle fixes carbon in the stroma.\n\n"
                    b"C4 plants use bundle sheath cells to concentrate CO2.")
            doc = await documents.ingest(
                s, name="photo.txt", data=text, owner_id=1, collection_id=None)
            assert doc.status == "ready"
            hits = await documents.search(s, "calvin cycle carbon",
                                          owner_id=1)
            assert hits and "Calvin" in hits[0]["text"]
            assert hits[0]["document"] == "photo.txt"
            # unrelated query returns little or nothing
            hits2 = await documents.search(s, "quantum entanglement",
                                           owner_id=1)
            assert len(hits2) <= len(hits)
    _run(go())


def test_roleplay_persona_and_room(session_factory):
    from app.models import Character, RoleplayRoom

    async def go():
        async with session_factory() as s:
            c1 = Character(name="Sherlock", emoji="🕵", personality="deductive",
                           boundaries="never reveal the culprit early")
            c2 = Character(name="Watson", emoji="🩺", personality="loyal")
            s.add_all([c1, c2])
            await s.flush()
            prompt = roleplay.persona_prompt(
                c1, scenario="A locked-room mystery", others=[c2])
            assert "Sherlock" in prompt and "deductive" in prompt
            assert "never reveal" in prompt
            assert "Watson" in prompt

            room = RoleplayRoom(name="221B", scenario="mystery",
                                character_ids=[c1.id, c2.id], messages=[])
            s.add(room)
            await s.flush()
            chars = await roleplay.room_characters(s, room)
            assert [c.name for c in chars] == ["Sherlock", "Watson"]
    _run(go())


def test_custom_skill_registry(session_factory):
    from app.models import CustomSkill
    from app.services import skills

    async def go():
        async with session_factory() as s:
            before = len(skills.all_skills())
            s.add(CustomSkill(name="sql_lint", description="lints sql",
                              instructions="Always lint the SQL.",
                              tools=["sql"], enabled=True))
            await s.flush()
            n = await skills.load_custom_skills(s)
            assert n == 1
            assert len(skills.all_skills()) == before + 1
            sk = skills.get_skill("sql_lint")
            assert sk and "lint" in sk.instructions.lower()
            assert "sql" in skills.required_tools(["sql_lint"])
            out = skills.attach_instructions("base", ["sql_lint"])
            assert "Always lint the SQL." in out
            # disabled skills unload
            row = (await s.execute(
                __import__("sqlalchemy").select(CustomSkill))
            ).scalar_one()
            row.enabled = False
            await s.flush()
            await skills.load_custom_skills(s)
            assert skills.get_skill("sql_lint") is None
    _run(go())


def test_agent_run_ledger(session_factory):

    from app.models import AgentRun
    from app.services import agent_log

    async def go():
        async with session_factory() as s:
            rid = await agent_log.start_run(s, "orchestrate", "test q", meta={"intent": "lookup"})
            await s.commit()
            assert rid
            await agent_log.record(s, rid, 1, stage="plan", actor="orchestrator",
                                   event="stage", detail="planning")
            await agent_log.finish_run(s, rid, "done", meta={"steps": 2}, duration_ms=1500)
            await s.commit()
            run = await s.get(AgentRun, rid)
            assert run.status == "done" and run.meta["steps"] == 2
            assert len(run.events) == 1 and run.events[0].actor == "orchestrator"
            runs = await agent_log.list_runs(s, kind="orchestrate")
            assert runs[0].id == rid
    _run(go())
