from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def _char(name: str, **kw) -> dict:
    return {
        "name": name,
        "emoji": "🧙",
        "tagline": "tag",
        "description": "desc",
        "personality": "calm",
        "style": " terse ",
        "greeting": "hello",
        **kw,
    }


def test_character_sync_roundtrip():
    with TestClient(app) as c:
        c.put("/v1/characters", json={"characters": []})  # clear any seeded rows
        assert c.get("/v1/characters").json() == []

        r = c.put("/v1/characters", json={"characters": [_char("Gandalf"), _char("Sherlock")]})
        assert r.status_code == 200, r.text
        rows = r.json()
        assert [x["name"] for x in rows] == ["Gandalf", "Sherlock"]
        assert all(x["id"] for x in rows)
        gandalf_id = rows[0]["id"]

        # Edit one, drop the other: ids are preserved for the survivor.
        edited = _char("Gandalf", personality="wise", id=gandalf_id)
        r = c.put("/v1/characters", json={"characters": [edited]})
        rows = r.json()
        assert len(rows) == 1
        assert rows[0]["id"] == gandalf_id
        assert rows[0]["personality"] == "wise"

        # Unknown id is treated as a new character, not an error.
        r = c.put("/v1/characters", json={"characters": [_char("New", id=9999)]})
        assert r.json()[0]["name"] == "New"

        # Empty sync clears the collection.
        assert c.put("/v1/characters", json={"characters": []}).json() == []
        assert c.get("/v1/characters").json() == []


def test_starter_pack():
    with TestClient(app) as c:
        # Existing user character with a predefined name must not be overwritten.
        c.put("/v1/characters", json={"characters": [
            _char("Sherlock Holmes", description="my own sherlock"),
        ]})
        r = c.post("/v1/characters/starter")
        assert r.status_code == 200, r.text
        rows = r.json()
        names = [x["name"] for x in rows]
        assert len(rows) == 8  # 7 new + the existing Sherlock
        assert names.count("Sherlock Holmes") == 1
        sherlock = next(x for x in rows if x["name"] == "Sherlock Holmes")
        assert sherlock["description"] == "my own sherlock"
        # Idempotent: second call adds nothing.
        assert len(c.post("/v1/characters/starter").json()) == 8


def test_person_lookup(monkeypatch):
    async def fake(name: str):
        if name.lower() == "satya nadella":
            return {"name": "Satya Nadella", "description": "CEO of Microsoft",
                    "extract": "Satya Narayana Nadella is an Indian-American business executive.",
                    "thumbnail": "https://upload.wikimedia.org/x.jpg",
                    "url": "https://en.wikipedia.org/wiki/Satya_Nadella",
                    "source": "wikipedia"}
        return None

    monkeypatch.setattr("app.api.person_routes.lookup_person", fake)
    with TestClient(app) as c:
        r = c.get("/v1/person/lookup?name=Satya%20Nadella")
        assert r.json()["found"] is True
        assert r.json()["person"]["name"] == "Satya Nadella"
        r = c.get("/v1/person/lookup?name=zzzznotaperson")
        assert r.json() == {"found": False, "person": None}
