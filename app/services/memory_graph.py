"""Memory graph builder — turns Astra DB memory collections into a
node/edge structure for the Memory Explorer visualization.

Nodes are real documents from ``mem_short`` (per-turn context) and
``mem_long`` (durable extracted facts). Two edge kinds are emitted:

- ``conversation`` — explicit stored relation: short-term memories that
  share ``conversation_id`` are chained by timestamp.
- ``similarity`` — computed relation: cosine similarity over the stored
  ``$vector`` embeddings (Astra ``$vectorize`` / NV-Embed), kept only for
  the top-K neighbors per node above a threshold. Clearly distinguished
  from stored relations so the UI never presents them as factual links.

No memory content, weights, or relationships are fabricated: when a
document lacks a stored vector it simply gets no similarity edges, and
importance is derived from real signals (degree + recency).
"""

from __future__ import annotations

import time
from typing import Any

from app.services import astra_memory

# Keep the graph bounded — the browser can't reasonably render more,
# and each fetch is a remote Astra call.
MAX_SHORT_DOCS = 140
MAX_LONG_DOCS = 80
# Cosine-similarity pruning: each node keeps at most K neighbors and only
# above this threshold. NV-Embed embeddings of unrelated text typically
# sit well below 0.5, so 0.55 keeps genuinely related pairs.
SIM_TOP_K = 3
SIM_THRESHOLD = 0.55
# In-process cache — a page refresh shouldn't re-scan Astra every time.
_CACHE_TTL = 60.0
_cache: dict[str, Any] = {"ts": 0.0, "graph": None}

_PROJ = {
    "text": 1, "role": 1, "source": 1, "conversation_id": 1,
    "ts": 1, "$vector": 1,
}


def _category(doc: dict[str, Any], collection: str) -> str:
    """Map a stored document to a display category from real fields."""
    if collection == astra_memory.LONG:
        return "knowledge"
    role = (doc.get("role") or "").strip().lower()
    if role == "assistant":
        return "assistant"
    if role == "system":
        return "system"
    return "personal"  # user turns = personal context


def _label(text: str) -> str:
    text = " ".join((text or "").split())
    return text[:48] + ("…" if len(text) > 48 else "") or "(empty)"


def _fetch_docs(col_name: str, limit: int) -> list[dict[str, Any]]:
    col = astra_memory._collection(col_name)  # noqa: SLF001 — same package
    if col is None:
        return []
    try:
        return list(col.find(filter=None, limit=limit, projection=_PROJ))
    except Exception:
        # Some collections lack $vector projection support on old plans —
        # retry without the vector so nodes still appear (edge-less).
        try:
            proj = {k: v for k, v in _PROJ.items() if k != "$vector"}
            return list(col.find(filter=None, limit=limit, projection=proj))
        except Exception:
            return []


def _cosine_edges(vecs: list[list[float]], ids: list[str],
                  top_k: int, threshold: float) -> list[dict[str, Any]]:
    """Top-K cosine-similarity neighbors per node via numpy matmul."""
    import numpy as np

    m = np.asarray(vecs, dtype=np.float32)
    norms = np.linalg.norm(m, axis=1)
    norms[norms == 0] = 1.0
    m = m / norms[:, None]
    sim = m @ m.T
    np.fill_diagonal(sim, -1.0)
    edges: list[dict[str, Any]] = []
    seen: set[frozenset[str]] = set()
    for i in range(len(ids)):
        k = min(top_k, len(ids) - 1)
        if k <= 0:
            break
        top = np.argpartition(sim[i], -k)[-k:]
        for j in top:
            w = float(sim[i, j])
            if w < threshold:
                continue
            key = frozenset((ids[i], ids[int(j)]))
            if key in seen:
                continue
            seen.add(key)
            edges.append({
                "a": ids[i], "b": ids[int(j)],
                "weight": round(w, 4), "kind": "similarity",
            })
    return edges


def _conversation_edges(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Chain same-conversation short-term memories by timestamp —
    an explicit stored relation (shared ``conversation_id``)."""
    by_conv: dict[str, list[dict[str, Any]]] = {}
    for n in nodes:
        cid = n.get("conversation_id")
        if cid:
            by_conv.setdefault(cid, []).append(n)
    edges: list[dict[str, Any]] = []
    for members in by_conv.values():
        members.sort(key=lambda n: n.get("ts") or 0)
        for a, b in zip(members, members[1:]):
            edges.append({"a": a["id"], "b": b["id"],
                          "weight": 1.0, "kind": "conversation"})
    return edges


def build_graph() -> dict[str, Any]:
    """Fetch both collections and assemble nodes + edges + stats."""
    if not astra_memory.enabled():
        return {"enabled": False, "nodes": [], "edges": [],
                "stats": {}, "fetched_at": time.time()}

    nodes: list[dict[str, Any]] = []
    for col_name, limit in ((astra_memory.SHORT, MAX_SHORT_DOCS),
                            (astra_memory.LONG, MAX_LONG_DOCS)):
        for doc in _fetch_docs(col_name, limit):
            vec = doc.get("$vector")
            nodes.append({
                "id": str(doc.get("_id") or ""),
                "label": _label(doc.get("text", "")),
                "text": doc.get("text") or "",
                "category": _category(doc, col_name),
                "collection": col_name,
                "role": doc.get("role"),
                "source": doc.get("source"),
                "conversation_id": doc.get("conversation_id"),
                "ts": doc.get("ts"),
            })
            # astrapy returns DataAPIVector (not a `list`, and its truth
            # value is ambiguous) — coerce so downstream numpy sees
            # plain float lists; has_vector derives from that.
            try:
                nodes[-1]["_vec"] = ([float(x) for x in vec]
                                     if vec is not None else None)
            except (TypeError, ValueError):
                nodes[-1]["_vec"] = None
            nodes[-1]["has_vector"] = nodes[-1]["_vec"] is not None

    edges = _conversation_edges(nodes)
    vecs: list[list[float]] = []
    vec_ids: list[str] = []
    for n in nodes:
        v = n.pop("_vec", None)
        if v:
            vecs.append(v)
            vec_ids.append(n["id"])
    if len(vecs) >= 2:
        try:
            edges.extend(_cosine_edges(vecs, vec_ids, SIM_TOP_K, SIM_THRESHOLD))
        except Exception:
            pass

    # Degree = number of incident edges — the only honest "importance"
    # signal available; surfaced to the UI as `degree`, not a fake weight.
    degree: dict[str, int] = {}
    for e in edges:
        degree[e["a"]] = degree.get(e["a"], 0) + 1
        degree[e["b"]] = degree.get(e["b"], 0) + 1
    for n in nodes:
        n["degree"] = degree.get(n["id"], 0)

    sim_weights = [e["weight"] for e in edges if e["kind"] == "similarity"]
    cats: dict[str, int] = {}
    for n in nodes:
        cats[n["category"]] = cats.get(n["category"], 0) + 1
    by_degree = sorted(nodes, key=lambda n: -n["degree"])
    stats = {
        "memories": len(nodes),
        "relationships": len(edges),
        "similarity_edges": len(sim_weights),
        "conversation_edges": sum(1 for e in edges
                                  if e["kind"] == "conversation"),
        "avg_similarity": (round(sum(sim_weights) / len(sim_weights), 3)
                           if sim_weights else None),
        "categories": cats,
        "most_connected": [
            {"id": n["id"], "label": n["label"], "degree": n["degree"]}
            for n in by_degree[:3] if n["degree"] > 0],
        "recent": [
            {"id": n["id"], "label": n["label"], "ts": n["ts"]}
            for n in sorted(nodes, key=lambda n: -(n["ts"] or 0))[:3]],
    }
    return {"enabled": True, "nodes": nodes, "edges": edges,
            "stats": stats, "fetched_at": time.time()}


def graph_cached() -> dict[str, Any]:
    """TTL-cached graph — the build is a remote Astra scan + matmul."""
    now = time.time()
    if _cache["graph"] is not None and now - _cache["ts"] < _CACHE_TTL:
        return _cache["graph"]
    graph = build_graph()
    _cache["graph"] = graph
    _cache["ts"] = now
    return graph
