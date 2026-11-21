"""Worlds layer — catalogue, entry, world state, choices, blueprint."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_worlds_catalogue():
    with TestClient(app) as c:
        r = c.get("/v1/roleplay/worlds")
        assert r.status_code == 200
        worlds = r.json()["worlds"]
    assert len(worlds) >= 5
    w = worlds[0]
    for k in ("id", "title", "premise", "genre", "art", "mode",
              "location", "world_time", "rules", "char_count"):
        assert k in w
    assert w["char_count"] >= 1


def test_enter_world_creates_room_and_cast():
    with TestClient(app) as c:
        r = c.post("/v1/roleplay/worlds/signal-station/enter", json={})
        assert r.status_code == 201, r.text
        room = r.json()
    w = room["world"]
    assert w["title"] == "Signal Station"
    assert w["location"]
    assert w["mode"] == "story"
    assert room["character_ids"], "world entry seeds the cast"
    assert any(e["kind"] == "arrival" for e in w["events"])
    # seeded characters exist with persona fields
    with TestClient(app) as c:
        chars = c.get("/v1/characters").json()
    keeper = next(ch for ch in chars if ch["name"] == "Keeper Mira Senn")
    assert keeper["personality"] and keeper["speaking_style"]


def test_enter_unknown_world_404():
    with TestClient(app) as c:
        assert c.post("/v1/roleplay/worlds/no-such/enter", json={}).status_code == 404


def test_custom_world_from_blueprint():
    bp = {"title": "The Drowned Archive", "premise": "a library under the sea",
          "location": "Reading Hall B", "world_time": "the long ebb",
          "scenario": "Water presses against the glass.",
          "rules": ["books get wet"],
          "characters": [{"name": "Archivist Noll", "emoji": "🦑",
                          "tagline": "keeper of wet tomes",
                          "personality": "fussy",
                          "speaking_style": "terse"}]}
    with TestClient(app) as c:
        r = c.post("/v1/roleplay/worlds/custom/enter", json={"blueprint": bp})
        assert r.status_code == 201, r.text
        room = r.json()
    assert room["world"]["title"] == "The Drowned Archive"
    assert room["world"]["location"] == "Reading Hall B"
    assert room["world"]["rules"] == ["books get wet"]
    assert len(room["character_ids"]) == 1
    assert room["scenario"].startswith("Water presses")


def test_world_patch_merges_not_replaces():
    with TestClient(app) as c:
        room = c.post("/v1/roleplay/worlds/last-human/enter", json={}).json()
        rid = room["id"]
        # patch only location — events/rules must survive
        r = c.patch(f"/v1/roleplay/rooms/{rid}",
                    json={"world": {"location": "The Underdeck"}})
        assert r.status_code == 200
        w = r.json()["world"]
    assert w["location"] == "The Underdeck"
    assert w["rules"], "partial patch must not wipe rules"
    assert any(e["kind"] == "arrival" for e in w["events"])


def test_world_event_logging():
    with TestClient(app) as c:
        room = c.post("/v1/roleplay/worlds/the-interview/enter", json={}).json()
        rid = room["id"]
        r = c.post(f"/v1/roleplay/rooms/{rid}/world/events",
                   json={"text": "The panel went silent.", "kind": "twist"})
        assert r.status_code == 200
        events = r.json()["world"]["events"]
    assert events[-1] == {"text": "The panel went silent.", "kind": "twist"}
    # bad payload rejected
    with TestClient(app) as c:
        assert c.post(f"/v1/roleplay/rooms/{rid}/world/events",
                      json={"text": ""}).status_code == 422


def test_choices_returns_actions():
    with TestClient(app) as c:
        room = c.post("/v1/roleplay/rooms", json={
            "name": "choice-test", "scenario": "a dock at dusk"}).json()
        rid = room["id"]
        r = c.post(f"/v1/roleplay/rooms/{rid}/choices", json={})
        assert r.status_code == 200
        choices = r.json()["choices"]
    assert 2 <= len(choices) <= 5
    assert all(isinstance(x, str) and x for x in choices)


def test_blueprint_fallback_shape():
    with TestClient(app) as c:
        r = c.post("/v1/roleplay/worlds/blueprint", json={
            "concept": "a city inside a glacier", "genre": "fantasy",
            "mood": "eerie", "cast_size": 2})
        assert r.status_code == 200
        bp = r.json()["blueprint"]
    assert bp["title"]
    assert bp["characters"], "blueprint always yields at least one character"
    ch = bp["characters"][0]
    assert "name" in ch and "personality" in ch


def test_relationship_add_via_world_patch():
    with TestClient(app) as c:
        room = c.post("/v1/roleplay/worlds/forgotten-kingdom/enter",
                      json={}).json()
        rid = room["id"]
        cids = room["character_ids"]
        rel = {"from": cids[0], "to": cids[1], "kind": "rivalry",
               "label": "old score"}
        r = c.patch(f"/v1/roleplay/rooms/{rid}",
                    json={"world": {"relationships": [rel]}})
        w = r.json()["world"]
    assert w["relationships"] == [rel]
    # non-list value is ignored, not stored
    with TestClient(app) as c:
        r = c.patch(f"/v1/roleplay/rooms/{rid}",
                    json={"world": {"relationships": "bogus"}})
        assert r.json()["world"]["relationships"] == [rel]
