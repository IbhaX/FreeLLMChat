"""Tests for the Stage turn engine: kinds, streaming, whisper, retry."""
import asyncio
import json
from unittest.mock import MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Character, RoleplayRoom
from app.services import roleplay


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


class _FakeGW:
    """Gateway double: chat_stream returns scripted delta chunks."""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.payloads: list[dict] = []

    async def chat_stream(self, session, payload, on_event=None):
        self.payloads.append(payload)
        text = self.responses.pop(0) if self.responses else "…"

        async def gen():
            for piece in text.split("|"):
                yield (f"data: {json.dumps({'choices':[{'delta':{'content':piece}}]})}"
                       "\n\n").encode()

        return gen(), MagicMock(), MagicMock(), payload["model"]


async def _collect(agen):
    frames = []
    async for b in agen:
        for line in b.decode().splitlines():
            if line.startswith("data:") and line != "data: [DONE]":
                j = json.loads(line[5:].strip())
                if j.get("freellm"):
                    frames.append(j["freellm"])
    return frames


def _mk_cast(session):
    sher = Character(name="Sherlock", emoji="S", personality="deductive")
    wat = Character(name="Watson", emoji="W", personality="loyal",
                    greeting="*Watson raises an eyebrow.* 'Well, this is a start.'")
    session.add_all([sher, wat])
    return sher, wat


def test_normalize_backfills_ids_and_kinds(session_factory):
    async def go():
        async with session_factory():
            room = RoleplayRoom(name="r", messages=[
                {"role": "user", "speaker": "You", "text": "hi"},
                {"role": "character", "speaker": "Sherlock", "text": "hm"}])
            roleplay.normalize_room(room)
            assert room.messages[0]["kind"] == "user"
            assert room.messages[1]["kind"] == "dialogue"
            assert all(m.get("id") for m in room.messages)
    _run(go())


def test_transcript_maps_kinds_and_hides_whispers(session_factory):
    room = RoleplayRoom(name="r", messages=[
        {"id": "1", "role": "narrator", "kind": "narration",
         "speaker": "Narrator", "text": "Rain falls."},
        {"id": "2", "role": "user", "kind": "ooc",
         "speaker": "You (OOC)", "text": "be scarier"},
        {"id": "3", "role": "user", "kind": "whisper", "speaker": "You",
         "text": "psst", "whisper_to": 7},
        {"id": "4", "role": "character", "kind": "dialogue",
         "speaker": "Sherlock", "text": "Indeed."}])
    narr = roleplay.transcript_messages(room, for_char=1)
    assert narr[0]["content"].startswith("[Narrator]:")
    assert narr[1]["content"].startswith("((out of character")
    assert all("psst" not in m["content"] for m in narr)
    target = roleplay.transcript_messages(room, for_char=7)
    assert any("whispered only to you" in m["content"] for m in target)


def test_persona_prompt_injects_mood_notes_summary(session_factory):
    c = Character(name="S", personality="x")
    p = roleplay.persona_prompt(c, mood="tense",
                                director_notes="storm coming",
                                summary="they met at the docks")
    assert "tense" in p and "storm coming" in p and "docks" in p


def test_regular_talk_queues_only_one_speaker(session_factory):
    async def go():
        async with session_factory() as s:
            sher, wat = _mk_cast(s)
            await s.flush()
            room = RoleplayRoom(name="221B", character_ids=[sher.id, wat.id],
                                messages=[])
            s.add(room)
            await s.commit()
            rid = room.id
        gw = _FakeGW(["elementary"])
        frames = await _collect(roleplay.turn_stream(
            gw, session_factory, rid, kind="talk", text="what do you make of it?"))
        queue = next(f for f in frames if f["event"] == "turn_queue")
        assert len(queue["order"]) == 1
        starts = [f for f in frames if f["event"] == "char_start"]
        assert len(starts) == 1 and not starts[0].get("aside")
    _run(go())


def test_turn_stream_sequences_characters(session_factory):
    async def go():
        async with session_factory() as s:
            sher, wat = _mk_cast(s)
            await s.flush()
            room = RoleplayRoom(name="221B", character_ids=[sher.id, wat.id],
                                messages=[{"id": "a1", "role": "character",
                                           "kind": "dialogue", "speaker": "X",
                                           "text": "prior"}])
            s.add(room)
            await s.commit()
            rid = room.id
        gw = _FakeGW(["elementary|my dear", "quite so|!"])
        frames = await _collect(roleplay.turn_stream(
            gw, session_factory, rid, kind="talk",
            text="each of you, what do you make of it?"))
        evs = [f["event"] for f in frames]
        assert evs[0] == "beat" and "turn_queue" in evs
        starts = [f["name"] for f in frames if f["event"] == "char_start"]
        assert starts == ["Sherlock", "Watson"]
        asides = [f.get("aside") for f in frames if f["event"] == "char_start"]
        assert asides == [False, True]
        ends = [f["text"] for f in frames if f["event"] == "char_end"]
        assert ends == ["elementarymy dear", "quite so!"]
        async with session_factory() as s:
            room = await s.get(RoleplayRoom, rid)
            kinds = [m.get("kind") for m in room.messages]
            assert kinds.count("dialogue") >= 2
            assert all(m.get("id") for m in room.messages)
    _run(go())


def test_whisper_only_target_replies(session_factory):
    async def go():
        async with session_factory() as s:
            sher, wat = _mk_cast(s)
            await s.flush()
            room = RoleplayRoom(name="r", character_ids=[sher.id, wat.id],
                                messages=[{"id": "x", "role": "character",
                                           "kind": "dialogue", "text": "prior"}])
            s.add(room)
            await s.commit()
            rid = room.id
        gw = _FakeGW(["hmm"])
        frames = await _collect(roleplay.turn_stream(
            gw, session_factory, rid, kind="whisper", text="psst",
            whisper_to=wat.id))
        speakers = [f["name"] for f in frames if f["event"] == "char_start"]
        assert speakers == ["Watson"]
        beat = next(f for f in frames if f["event"] == "beat")
        assert beat["message"]["whisper_to"] == wat.id
    _run(go())


def test_paused_characters_are_skipped(session_factory):
    async def go():
        async with session_factory() as s:
            sher, wat = _mk_cast(s)
            await s.flush()
            room = RoleplayRoom(name="r", character_ids=[sher.id, wat.id],
                                paused_ids=[sher.id],
                                messages=[{"id": "x", "role": "character",
                                           "kind": "dialogue", "text": "prior"}])
            s.add(room)
            await s.commit()
            rid = room.id
        frames = await _collect(roleplay.turn_stream(
            _FakeGW(["ok"]), session_factory, rid, kind="talk", text="hi"))
        speakers = [f["name"] for f in frames if f["event"] == "char_start"]
        assert speakers == ["Watson"]
    _run(go())


def test_greeting_autoplay_on_empty_scene(session_factory):
    async def go():
        async with session_factory() as s:
            sher, wat = _mk_cast(s)
            await s.flush()
            room = RoleplayRoom(name="r", character_ids=[wat.id], messages=[])
            s.add(room)
            await s.commit()
            rid = room.id
        gw = _FakeGW()  # no scripted replies — greeting must not call the model
        frames = await _collect(roleplay.turn_stream(
            gw, session_factory, rid, kind="talk", text="hello"))
        end = next(f for f in frames if f["event"] == "char_end")
        assert "raises an eyebrow" in end["text"]
        assert not gw.payloads  # greeting replayed verbatim, zero model calls
    _run(go())


def test_retry_appends_variant(session_factory):
    async def go():
        async with session_factory() as s:
            sher = Character(name="Sherlock", personality="x")
            s.add(sher)
            await s.flush()
            room = RoleplayRoom(
                name="r", character_ids=[sher.id],
                messages=[{"id": "m1", "role": "character", "kind": "dialogue",
                           "character_id": sher.id, "speaker": "Sherlock",
                           "text": "first answer", "variants": []}])
            s.add(room)
            await s.commit()
            rid = room.id
        frames = await _collect(roleplay.retry_stream(
            _FakeGW(["second|answer"]), session_factory, rid,
            message_id="m1"))
        end = next(f for f in frames if f["event"] == "char_end")
        assert end["text"] == "secondanswer"
        assert "first answer" in end["variants"]
    _run(go())


def test_beat_only_records_without_replies(session_factory):
    async def go():
        async with session_factory() as s:
            sher = Character(name="Sherlock", personality="x")
            s.add(sher)
            await s.flush()
            room = RoleplayRoom(name="r", character_ids=[sher.id], messages=[])
            s.add(room)
            await s.commit()
            rid = room.id
        frames = await _collect(roleplay.beat_only(
            session_factory, rid, kind="narrate", text="The lights go out."))
        evs = [f["event"] for f in frames]
        assert "char_start" not in evs
        beat = next(f for f in frames if f["event"] == "beat")
        assert beat["message"]["kind"] == "narrate"
    _run(go())


def test_clean_turn_text_strips_persona_breaks():
    assert roleplay.clean_turn_text("[Sherlock]: Elementary.", "Sherlock") \
        == "Elementary."
    assert roleplay.clean_turn_text(
        "As an AI I cannot.\nThe door creaks.", "Sherlock") \
        == "The door creaks."


@pytest.mark.parametrize("text,expected", [
    ("who are you?", False),
    ("What is your name?", False),
    ("Tell me about yourself.", False),
    ("What's your favourite case?", False),
    ("Where are you from?", False),
    ("what is life?", False),
    ("what is the meaning of life?", False),
    ("research the biological definition of life", True),
    ("who is the other one?", False),
    ("who is the second character?", False),
    ("who is the CEO of Nagarro?", True),
    ("when was the eiffel tower built?", True),
    ("look up your family history online", True),
    ("search for who you are in the archives", True),
])
def test_research_requested_persona_vs_lookup(text, expected):
    assert roleplay._research_requested(text) is expected


def test_persona_turn_never_searches_in_tools_room(session_factory, monkeypatch):
    seen_tools: list[bool] = []
    prefetches: list[str] = []
    real_stream = roleplay._char_reply_stream

    def spy_stream(*args, **kwargs):
        seen_tools.append(kwargs.get("tools"))
        return real_stream(*args, **kwargs)

    async def spy_prefetch(*args, **kwargs):
        prefetches.append("called")
        return "", []

    monkeypatch.setattr(roleplay, "_char_reply_stream", spy_stream)
    monkeypatch.setattr(roleplay, "_research_prefetch", spy_prefetch)

    async def go():
        async with session_factory() as s:
            sher, _ = _mk_cast(s)
            await s.flush()
            room = RoleplayRoom(name="r", character_ids=[sher.id],
                                tools_enabled=True, messages=[])
            s.add(room)
            await s.commit()
            rid = room.id
        gw = _FakeGW(["I am Sherlock."])
        await _collect(roleplay.turn_stream(
            gw, session_factory, rid, kind="talk", text="who are you?"))
    _run(go())
    assert seen_tools == [False]
    assert prefetches == []


def _responder_room(last_speaker_id: int | None) -> RoleplayRoom:
    msgs = [{"id": "u1", "role": "user", "kind": "talk", "speaker": "You",
             "text": "hello"}]
    if last_speaker_id is not None:
        msgs.insert(0, {"id": "c1", "role": "character", "kind": "dialogue",
                        "speaker": "x", "character_id": last_speaker_id,
                        "text": "greetings"})
    return RoleplayRoom(name="r", messages=msgs)


@pytest.mark.parametrize("text,last_is_kaito,expected", [
    ("Kaito, what do you think of this?", False, "Kaito Sensei"),
    ("why are you not replying?", True, "Kaito Sensei"),
    ("NOVA, why are you not replying?", True, "NOVA"),
    ("Sensei, hello?", True, "NOVA"),
])
def test_pick_responder_scores_names_and_followups(
        session_factory, text, last_is_kaito, expected):
    nova = Character(id=1, name="NOVA", personality="x")
    kaito = Character(id=2, name="Kaito Sensei", personality="y")
    room = _responder_room(2 if last_is_kaito else 1)

    class _NoRouter:
        pass

    async def go():
        return await roleplay._pick_responder(
            _NoRouter(), None, room, [nova, kaito], text)
    assert _run(go()).name == expected


def _archive_run(session_factory, monkeypatch, *, owner_id, text, sources):
    from app.services import documents

    calls: list[dict] = []

    async def fake_ingest(session, **kw):
        calls.append(kw)

    async def fake_prefetch(*a, **k):
        return "ctx", sources

    monkeypatch.setattr(documents, "ingest", fake_ingest)
    monkeypatch.setattr(roleplay, "_research_prefetch", fake_prefetch)

    async def go():
        async with session_factory() as s:
            sher, _ = _mk_cast(s)
            await s.flush()
            room = RoleplayRoom(name="r", character_ids=[sher.id],
                                tools_enabled=True, owner_id=owner_id,
                                messages=[])
            s.add(room)
            await s.commit()
            rid = room.id
        gw = _FakeGW(["Findings stand [1]."])
        await _collect(roleplay.turn_stream(
            gw, session_factory, rid, kind="talk", text=text))
        return rid
    rid = _run(go())
    return rid, calls


def test_research_reply_is_archived_for_owned_room(session_factory, monkeypatch):
    src = [{"title": "Primary", "url": "https://example.org/primary"}]
    rid, calls = _archive_run(session_factory, monkeypatch, owner_id=7,
                              text="research the baseline please", sources=src)
    assert len(calls) == 1
    kw = calls[0]
    assert kw["owner_id"] == 7 and kw["collection_id"] is None
    assert kw["name"].startswith(f"Roleplay research {rid}-")
    body = kw["data"].decode("utf-8")
    assert "Findings stand" in body and "https://example.org/primary" in body
    assert "research the baseline please" not in body


def test_research_reply_not_archived_for_anonymous_or_plain(session_factory, monkeypatch):
    src = [{"title": "Primary", "url": "https://example.org/primary"}]
    _, anon = _archive_run(session_factory, monkeypatch, owner_id=None,
                           text="research the baseline please", sources=src)
    assert anon == []
    _, plain = _archive_run(session_factory, monkeypatch, owner_id=7,
                            text="hello there", sources=src)
    assert plain == []
