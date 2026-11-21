#!/usr/bin/env python
"""Owner-filtered memory search benchmark.

Measures real Astra round-trips for the tenant-scoped query shapes the
application now issues: filtered $vectorize recall, filtered plain find
(graph scan), and filtered delete. Uses a throwaway ``__bench__`` tenant
namespace — writes a handful of docs, times, then deletes them.

Usage:  python -X utf8 scripts/benchmark_memory.py [--iterations 5]
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import astra_memory  # noqa: E402

TENANT = "__bench__"


def _time(label: str, fn, n: int) -> dict:
    samples = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    return {"op": label, "n": n, "min_ms": round(min(samples), 1),
            "median_ms": round(statistics.median(samples), 1),
            "max_ms": round(max(samples), 1)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iterations", type=int, default=5)
    args = ap.parse_args()

    if not astra_memory.enabled():
        print("Astra unavailable — set ASTRA_DB_* env vars")
        return 1
    astra_memory.ensure_collections()
    col = astra_memory._collection(astra_memory.SHORT)  # noqa: SLF001
    if col is None:
        print("collection unavailable")
        return 1

    ids = []
    try:
        for i in range(8):
            doc_id = uuid.uuid4().hex
            ids.append(doc_id)
            col.insert_one({
                "_id": doc_id, "$vectorize": f"benchmark memory {i}",
                "user_id": TENANT, "conversation_id": "bench",
                "role": "user", "text": f"benchmark memory {i}",
                "ts": time.time()})

        results = [
            _time("filtered_vector_recall",
                  lambda: list(col.find(
                      filter={"user_id": TENANT},
                      sort={"$vectorize": "benchmark query"},
                      limit=6)), args.iterations),
            _time("filtered_scan_find",
                  lambda: list(col.find(
                      filter={"user_id": TENANT}, limit=100,
                      projection={"text": 1})), args.iterations),
            _time("filtered_delete_one",
                  lambda: col.delete_one(
                      {"_id": "nonexistent", "user_id": TENANT}),
                  args.iterations),
        ]
        for r in results:
            print(f"{r['op']:28} n={r['n']}  "
                  f"min={r['min_ms']}ms  median={r['median_ms']}ms  "
                  f"max={r['max_ms']}ms")
        return 0
    finally:
        for doc_id in ids:
            try:
                col.delete_one({"_id": doc_id, "user_id": TENANT})
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
