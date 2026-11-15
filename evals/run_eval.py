"""Eval runner: drives the /v1/research2 API over a JSONL dataset and
scores deterministic metrics. Writes a timestamped report to
evals/reports/.

Usage:
    python -m evals.run_eval --dataset evals/datasets/research_smoke.jsonl \
        --base-url http://localhost:8000 [--token KEY] [--limit 5]

Requires the server running with the v2 job worker enabled.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any, Optional

import httpx

from evals.judges import EvalScores, score_run

_TERMINAL = {"done", "failed", "cancelled", "degraded"}


async def _wait_done(client: httpx.AsyncClient, run_id: str,
                     timeout_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        r = await client.get(f"/v1/research2/runs/{run_id}")
        r.raise_for_status()
        body = r.json()
        if body.get("state") in _TERMINAL:
            return body
        await asyncio.sleep(2.0)
    return {"state": "timeout"}


async def run_item(client: httpx.AsyncClient, item: dict[str, Any],
                   timeout_s: float) -> EvalScores:
    t0 = time.monotonic()
    try:
        r = await client.post("/v1/research2/runs", json={
            "question": item["question"], "depth": item.get("depth"),
        })
        r.raise_for_status()
        run_id = r.json()["id"]
        final = await _wait_done(client, run_id, timeout_s)
        state = final.get("state", "?")

        markdown = ""
        claims: list[dict[str, Any]] = []
        if state in ("done", "degraded"):
            rep = await client.get(f"/v1/research2/runs/{run_id}/report")
            if rep.status_code == 200:
                markdown = rep.json().get("markdown") or ""
            ev = await client.get(f"/v1/research2/runs/{run_id}/evidence")
            if ev.status_code == 200:
                claims = ev.json().get("claims") or []
        wall = time.monotonic() - t0
        return score_run(item["id"], markdown,
                         item.get("required_facts", []),
                         claims=claims, wall_s=wall, state=state)
    except Exception as e:
        return EvalScores(question_id=item["id"], coverage=0.0,
                          state=f"error:{str(e)[:60]}",
                          wall_s=round(time.monotonic() - t0, 1))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--token", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    items = [json.loads(line) for line in
             Path(args.dataset).read_text(encoding="utf-8").splitlines()
             if line.strip()]
    if args.limit:
        items = items[: args.limit]

    headers = {}
    if args.token:
        headers["authorization"] = f"Bearer {args.token}"

    async with httpx.AsyncClient(base_url=args.base_url, headers=headers,
                                 timeout=60) as client:
        scores: list[EvalScores] = []
        for item in items:
            print(f"[{item['id']}] {item['question'][:70]} …", flush=True)
            s = await run_item(client, item, args.timeout)
            scores.append(s)
            print(f"    state={s.state} cov={s.coverage} "
                  f"cite_recall={s.citation_recall} "
                  f"precision={s.claim_quote_precision} {s.wall_s}s")

    n = len(scores) or 1
    summary = {
        "runs": n,
        "coverage": round(sum(s.coverage for s in scores) / n, 4),
        "citation_recall": round(
            sum(s.citation_recall for s in scores) / n, 4),
        "done_rate": round(
            sum(1 for s in scores if s.state in ("done", "degraded")) / n, 4),
    }
    out = args.out or str(
        Path(__file__).parent / "reports"
        / f"eval_{int(time.time())}.json")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps({
        "dataset": args.dataset, "summary": summary,
        "results": [s.__dict__ for s in scores],
    }, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"report → {out}")


if __name__ == "__main__":
    asyncio.run(main())
