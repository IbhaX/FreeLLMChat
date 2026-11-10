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
