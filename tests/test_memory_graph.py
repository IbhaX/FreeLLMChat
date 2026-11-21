"""Memory graph builder contracts — no live Astra needed; _fetch_docs
is patched so node/edge assembly is tested on fixed vectors."""

from __future__ import annotations

import math

import app.services.memory_graph as mg


def _doc(i, text, role=None, conv=None, vec=None, ts=1.0):
    d = {"_id": f"id{i}", "text": text, "ts": ts,
         "role": role, "source": None, "conversation_id": conv}
    if vec is not None:
        d["$vector"] = vec
    return d


def _patch_docs(monkeypatch, short, long):
    def fake(col, tenant, limit):
        return short if col == mg.astra_memory.SHORT else long
    monkeypatch.setattr(mg, "_fetch_docs", fake)
    monkeypatch.setattr(mg.astra_memory, "enabled", lambda: True)


def test_disabled_returns_empty(monkeypatch):
    monkeypatch.setattr(mg.astra_memory, "enabled", lambda: False)
    g = mg.build_graph("u1")
    assert g["enabled"] is False and g["nodes"] == [] and g["edges"] == []


def test_conversation_edges_chain_by_ts(monkeypatch):
    _patch_docs(monkeypatch, [
        _doc("a", "first", role="user", conv="c1", ts=1.0),
        _doc("b", "second", role="assistant", conv="c1", ts=2.0),
        _doc("c", "third", role="user", conv="c1", ts=3.0),
        _doc("d", "other conv", role="user", conv="c2", ts=4.0),
    ], [])
    g = mg.build_graph("u1")
    conv = {(e["a"], e["b"]) for e in g["edges"] if e["kind"] == "conversation"}
    assert conv == {("ida", "idb"), ("idb", "idc")}
    assert all(e["weight"] == 1.0 for e in g["edges"]
               if e["kind"] == "conversation")


def test_similarity_edges_labelled_and_thresholded(monkeypatch):
    identical = [1.0] + [0.0] * 5
    near = [math.sqrt(0.8), math.sqrt(0.2)] + [0.0] * 4  # cos ≈ .894
    far = [0.0, 1.0] + [0.0] * 4                        # cos 0
    _patch_docs(monkeypatch, [
        _doc("a", "same fact", conv=None, vec=identical),
        _doc("b", "same fact", conv=None, vec=identical),
        _doc("c", "close fact", conv=None, vec=near),
        _doc("d", "unrelated", conv=None, vec=far),
    ], [])
    g = mg.build_graph("u1")
    sim = [e for e in g["edges"] if e["kind"] == "similarity"]
    pairs = {frozenset((e["a"], e["b"])) for e in sim}
    assert frozenset(("ida", "idb")) in pairs          # identical → 1.0
    assert all(e["weight"] >= mg.SIM_THRESHOLD for e in sim)
    # Every node keeps at most TOP_K similarity neighbours.
    deg = {}
    for e in sim:
        deg[e["a"]] = deg.get(e["a"], 0) + 1
        deg[e["b"]] = deg.get(e["b"], 0) + 1
    assert all(v <= mg.SIM_TOP_K for v in deg.values())


def test_nodes_carry_real_fields_and_degree(monkeypatch):
    _patch_docs(monkeypatch, [
        _doc("a", "remember this", role="user", conv="c9", ts=5.0),
        _doc("b", "reply", role="assistant", conv="c9", ts=6.0),
    ], [_doc("z", "durable fact", ts=7.0)])
    g = mg.build_graph("u1")
    by_id = {n["id"]: n for n in g["nodes"]}
    assert by_id["ida"]["category"] == "personal"
    assert by_id["idb"]["category"] == "assistant"
    assert by_id["idz"]["category"] == "knowledge"
    assert by_id["ida"]["degree"] == 1 and by_id["idz"]["degree"] == 0
    assert "_vec" not in by_id["ida"]  # never leaks the raw embedding
    assert g["stats"]["memories"] == 3
    assert g["stats"]["conversation_edges"] == 1
