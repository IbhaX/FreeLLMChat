"""Multi-tenant memory isolation — cross-tenant security contracts.

Uses an in-memory fake of the Astra collection API that applies the
*actual* filter dicts the service passes in, so tests prove the
``user_id`` constraint reaches the query — not just that results are
post-filtered.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest

from app.services import astra_memory as am
from app.services import memory_graph as mg


# ------------------------------------------------------------- fake Astra

class _Res:
    def __init__(self, deleted: int = 0, matched: int = 0, modified: int = 0) -> None:
        self.deleted_count = deleted
        self.matched_count = matched
        self.modified_count = modified


def _match(doc: dict[str, Any], flt: dict[str, Any] | None) -> bool:
    if not flt:
        return True
    for k, v in flt.items():
        if isinstance(v, dict) and "$eq" in v:
            if doc.get(k) != v["$eq"]:
                return False
        elif doc.get(k) != v:
            return False
    return True


class FakeCollection:
    """Minimal astrapy stand-in: stores docs, honours find filters."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}
        self.find_calls: list[dict[str, Any] | None] = []

    def insert_one(self, doc: dict[str, Any]) -> None:
        self.docs[doc["_id"]] = dict(doc)

    def find(self, filter: dict[str, Any] | None = None,
             sort: Any = None, limit: int | None = None,
             projection: Any = None) -> list[dict[str, Any]]:
        self.find_calls.append(filter)
        hits = [d for d in self.docs.values() if _match(d, filter)]
        return hits[: limit or 100]

    def find_one(self, filter: dict[str, Any] | None = None,
                 sort: Any = None, projection: Any = None) -> dict[str, Any] | None:
        hits = self.find(filter=filter, limit=1)
        return hits[0] if hits else None

    def delete_one(self, filter: dict[str, Any]) -> _Res:
        for d in list(self.docs.values()):
            if _match(d, filter):
                del self.docs[d["_id"]]
                return _Res(deleted=1)
        return _Res(deleted=0)

    def update_one(self, filter: dict[str, Any],
                   update: dict[str, Any]) -> _Res:
        for d in self.docs.values():
            if _match(d, filter):
                d.update(update.get("$set", {}))
                return _Res(matched=1, modified=1)
        return _Res(matched=0, modified=0)


@pytest.fixture
def fake_astra(monkeypatch: pytest.MonkeyPatch) -> dict[str, FakeCollection]:
    cols = {am.SHORT: FakeCollection(), am.LONG: FakeCollection()}
    monkeypatch.setattr(am, "_db", lambda: object())
    monkeypatch.setattr(am, "_collection", lambda name: cols.get(name))
    return cols


# ------------------------------------------------------------- tenant key

def test_tenant_key_uses_immutable_id_not_identity() -> None:
    assert am.tenant_key(SimpleNamespace(id=42, email="a@b.c")) == "u42"
    assert am.tenant_key(None) == am.ANON_TENANT


# ------------------------------------------------------------------ writes

def test_store_short_stamps_verified_tenant(fake_astra: dict) -> None:
    am.store_short("u1", "c9", "user", "hello there")
    doc = next(iter(fake_astra[am.SHORT].docs.values()))
    assert doc["user_id"] == "u1" and doc["conversation_id"] == "c9"


def test_store_long_stamps_tenant_and_dedupes_within_tenant(
        fake_astra: dict) -> None:
    am.store_long("u1", "user likes python")
    am.store_long("u1", "user likes python")   # dup within same tenant
    assert len(fake_astra[am.LONG].docs) == 1
    assert next(iter(fake_astra[am.LONG].docs.values()))["user_id"] == "u1"


def test_same_fact_stored_per_tenant(fake_astra: dict) -> None:
    """Dedup is per-tenant: user B keeps their own copy of a fact."""
    am.store_long("u1", "shared sounding fact")
    am.store_long("u2", "shared sounding fact")
    assert len(fake_astra[am.LONG].docs) == 2


@pytest.mark.parametrize("bad", ["", "  ", None])
def test_writes_reject_missing_tenant(fake_astra: dict, bad: Any) -> None:
    am.store_short(bad, "c1", "user", "x")
    am.store_long(bad, "x")
    assert not fake_astra[am.SHORT].docs and not fake_astra[am.LONG].docs


def test_writes_reject_legacy_quarantine(fake_astra: dict) -> None:
    """Nothing may write into the __legacy__ quarantine bucket."""
    am.store_long(am.LEGACY_TENANT, "sneaky")
    assert not fake_astra[am.LONG].docs


def test_forged_user_id_from_payload_is_not_special(fake_astra: dict) -> None:
    """A caller passing another user's id only gets that tenant's own docs —
    the service treats the arg as authoritative, so routes must pass the
    *verified* subject (which they do); what matters is a forged value can
    never widen access beyond its own namespace."""
    am.store_long("u1", "alice secret")
    out = am.recall_long("u2", "alice secret")
    assert out == []


# ------------------------------------------------------------------ recall

def test_recall_short_filters_tenant_and_conversation(
        fake_astra: dict) -> None:
    am.store_short("u1", "c1", "user", "a turn")
    am.store_short("u2", "c1", "user", "other user's turn")
    am.store_short("u1", "c2", "user", "different conv")
    hits = am.recall_short("u1", "a turn", "c1")
    assert hits == ["a turn"]
    # The tenant filter must reach the query itself.
    assert all(f and f.get("user_id") == "u1"
               for f in fake_astra[am.SHORT].find_calls)


def test_recall_long_never_crosses_tenants(fake_astra: dict) -> None:
    am.store_long("u1", "alice is a pilot")
    am.store_long("u2", "bob is a baker")
    assert am.recall_long("u1", "baker pilot") == ["alice is a pilot"]
    assert am.recall_long("u2", "baker pilot") == ["bob is a baker"]
    assert all(f and f.get("user_id") for f
               in fake_astra[am.LONG].find_calls)


def test_recall_rejects_empty_tenant(fake_astra: dict) -> None:
    am.store_long("u1", "secret")
    assert am.recall_long("", "secret") == []
    assert am.recall_short(None, "q", "c1") == []
    assert am.recall_text("q", "") == ""


# ------------------------------------------------------------------- CRUD

def test_cross_tenant_delete_is_impossible(fake_astra: dict) -> None:
    am.store_long("u1", "keep me")
    did = next(iter(fake_astra[am.LONG].docs))
    assert am.delete_doc("u2", am.LONG, did) == 0
    assert did in fake_astra[am.LONG].docs            # untouched
    assert am.delete_doc("u1", am.LONG, did) == 1
    assert did not in fake_astra[am.LONG].docs


def test_cross_tenant_update_is_impossible(fake_astra: dict) -> None:
    am.store_long("u1", "original")
    did = next(iter(fake_astra[am.LONG].docs))
    assert am.update_doc_text("u2", am.LONG, did, "hijack") is False
    assert fake_astra[am.LONG].docs[did]["text"] == "original"
    assert am.update_doc_text("u1", am.LONG, did, "mine") is True


def test_cross_tenant_get_returns_none(fake_astra: dict) -> None:
    """IDOR guard: the doc id alone is never enough."""
    am.store_long("u1", "private")
    did = next(iter(fake_astra[am.LONG].docs))
    assert am.get_doc("u2", am.LONG, did) is None
    assert am.get_doc("u1", am.LONG, did) is not None
    assert am.get_doc("", am.LONG, did) is None


def test_delete_and_update_reject_missing_tenant(fake_astra: dict) -> None:
    am.store_long("u1", "x")
    did = next(iter(fake_astra[am.LONG].docs))
    assert am.delete_doc("", am.LONG, did) == 0
    assert am.update_doc_text(None, am.LONG, did, "y") is False


# ------------------------------------------------------------- extract bg

@pytest.mark.asyncio
async def test_extract_and_store_fails_closed_without_tenant() -> None:
    class _GW:
        async def chat(self, *a: Any, **k: Any) -> Any:  # pragma: no cover
            raise AssertionError("gateway must never be reached")

    gw = _GW()
    await am.extract_and_store(gw, "", "u" * 30, "a" * 60)
    await am.extract_and_store(gw, am.LEGACY_TENANT, "u" * 30, "a" * 60)


# ------------------------------------------------------------ graph scope

def test_fetch_docs_applies_tenant_filter(monkeypatch: pytest.MonkeyPatch,
                                          fake_astra: dict) -> None:
    fake_astra[am.SHORT].docs["a"] = {
        "_id": "a", "user_id": "u1", "text": "x", "ts": 1.0}
    fake_astra[am.SHORT].docs["b"] = {
        "_id": "b", "user_id": "u2", "text": "y", "ts": 2.0}
    docs = mg._fetch_docs(am.SHORT, "u1", 10)
    assert [d["_id"] for d in docs] == ["a"]
    assert all(f and f.get("user_id") == "u1"
               for f in fake_astra[am.SHORT].find_calls)


def test_build_graph_empty_tenant_returns_empty(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(am, "enabled", lambda: True)
    g = mg.build_graph("")
    assert g["nodes"] == [] and g["edges"] == []


def test_graph_cache_is_keyed_per_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(am, "enabled", lambda: True)
    calls: list[str] = []

    def fake_fetch(col: str, tenant: str, limit: int) -> list[dict[str, Any]]:
        calls.append(tenant)
        return [{"_id": f"{tenant}-{col}", "text": t, "ts": 1.0}
                for t in ([tenant] if tenant == "u1" else [])]

    monkeypatch.setattr(mg, "_fetch_docs", fake_fetch)
    g1 = mg.graph_cached("u1")
    g1b = mg.graph_cached("u1")          # cache hit — no new fetch
    g2 = mg.graph_cached("u2")
    assert all(n["id"].startswith("u1-") for n in g1["nodes"])
    assert len(g1["nodes"]) == 2         # one doc per collection
    assert g1b["nodes"] == g1["nodes"]
    assert g2["nodes"] == []             # u2 never sees u1's cached nodes
    assert calls == ["u1", "u1", "u2", "u2"]  # per-tenant cache miss only


def test_similarity_never_links_across_tenants(
        monkeypatch: pytest.MonkeyPatch, fake_astra: dict) -> None:
    """Identical embeddings in different tenants must produce no edge —
    nodes never even co-exist in one graph build."""
    monkeypatch.setattr(am, "enabled", lambda: True)
    vec = [1.0] + [0.0] * 7
    fake_astra[am.LONG].docs["a"] = {
        "_id": "a", "user_id": "u1", "text": "same", "ts": 1.0,
        "$vector": vec}
    fake_astra[am.LONG].docs["b"] = {
        "_id": "b", "user_id": "u2", "text": "same", "ts": 1.0,
        "$vector": vec}
    g1 = mg.build_graph("u1")
    g2 = mg.build_graph("u2")
    assert [n["id"] for n in g1["nodes"]] == ["a"]
    assert [n["id"] for n in g2["nodes"]] == ["b"]
    assert not g1["edges"] and not g2["edges"]


# -------------------------------------------------------- SQL Memory rows

@pytest.mark.asyncio
async def test_get_memory_text_scopes_persistent_memories(
        engine: Any) -> None:
    """The SQL Memory table is filtered by the verified owner."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.models import Memory

    factory = async_sessionmaker(engine, class_=AsyncSession,
                                 expire_on_commit=False)
    async with factory() as s:
        s.add_all([
            Memory(owner_id=7, content="alice's durable note", source="user"),
            Memory(owner_id=9, content="bob's durable note", source="user"),
            Memory(content="unowned legacy note", source="user"),
        ])
        await s.commit()

    import app.db as db
    import app.services.agents as agents_mod

    # get_memory_text opens its own session — point it at the test engine.
    orig = db.get_session_factory
    db.get_session_factory = lambda: factory  # type: ignore[assignment]
    try:
        # query terms must lexically overlap content (_memory_relevance > 0)
        text_a = await agents_mod.get_memory_text(
            "durable note", user=SimpleNamespace(id=7))
        text_anon = await agents_mod.get_memory_text("durable note", user=None)
    finally:
        db.get_session_factory = orig

    assert "alice's durable note" in text_a
    assert "bob's durable note" not in text_a
    assert "unowned legacy note" not in text_a
    assert "unowned legacy note" in text_anon
    assert "alice's durable note" not in text_anon
