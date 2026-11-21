#!/usr/bin/env python
"""AstraDB memory-tenancy migration — audit, backup, trace, assign, quarantine.

Adds the mandatory ``user_id`` field to pre-tenancy documents so the
application-enforced filters (see ``app/services/astra_memory.py``) can
scope every read/write to the verified tenant.

Rules (never guess ownership):

* Docs that already carry ``user_id`` are left untouched and counted
  ``verified``.
* Evidence docs are traced through ``run_id`` → ``ResearchRunV2.owner_id``:
  a real owner id becomes ``u<id>``; a run that exists but has no owner is
  legacy single-tenant data → ``anon``; an unresolvable run → quarantined.
* ``mem_short``/``mem_long`` docs have no owner-traceable linkage (the
  Conversation model carries no owner) → quarantined as ``__legacy__``.
  Quarantined docs match no real tenant filter: users never see them, but
  the data is preserved for audit rather than deleted.

Modes:
    --dry-run          audit + report only, no writes   (default)
    --apply            backup then write user_id stamps
    --rollback FILE    restore the pre-apply state recorded in FILE
    --collection NAME  limit to one collection (repeatable)

Writes require ``--apply`` plus ``NOVA_MIGRATION_ACK=1`` — belt and braces
against accidental execution. Reports land under ``docs/migration/``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.services import astra_memory  # noqa: E402

REPORT_DIR = Path("docs/migration")


def _scan(col: Any) -> list[dict[str, Any]]:
    """Full scan of a collection. Bounded by design — migrations are
    operator-run, not request-path."""
    return list(col.find(filter=None, limit=10_000))


async def _run_owner(run_id: str) -> tuple[bool, Optional[int]]:
    """Resolve a research run's owner from SQLite.

    Returns ``(run_exists, owner_id)`` — owner_id None means legacy
    single-tenant data; run_exists False means unresolvable."""
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.db import get_session_factory
    from app.models import ResearchRunV2

    try:
        async with get_session_factory()() as s:  # type: AsyncSession
            run = await s.get(ResearchRunV2, run_id)
    except Exception:
        return False, None
    if run is None:
        return False, None
    return True, getattr(run, "owner_id", None)


async def _plan_doc(col_name: str, doc: dict[str, Any]) -> tuple[str, str]:
    """Return (target_tenant, verdict) for one doc."""
    if doc.get("user_id"):
        return doc["user_id"], "verified"
    evidence_col = get_settings().astra_evidence_collection
    if col_name == evidence_col and doc.get("run_id"):
        exists, owner = await _run_owner(str(doc["run_id"]))
        if owner is not None:
            return f"u{owner}", "traced"
        if exists:
            return astra_memory.ANON_TENANT, "traced_anon"
    return astra_memory.LEGACY_TENANT, "quarantine"


async def audit(collections: list[str]) -> dict[str, Any]:
    """Scan every target collection and classify each doc."""
    db = astra_memory.astra_db()
    if db is None:
        return {"error": "Astra unavailable — check ASTRA_DB_* settings"}
    report: dict[str, Any] = {"generated_at": time.time(),
                              "collections": {}, "docs": []}
    for name in collections:
        try:
            col = db.get_collection(name)
        except Exception as e:
            report["collections"][name] = {"error": str(e)}
            continue
        docs = _scan(col)
        entry = {"total": len(docs), "verified": 0, "traced": 0,
                 "traced_anon": 0, "quarantine": 0}
        for doc in docs:
            tenant, verdict = await _plan_doc(name, doc)
            entry[verdict] = entry.get(verdict, 0) + 1
            report["docs"].append({
                "collection": name, "_id": doc.get("_id"),
                "tenant": tenant, "verdict": verdict})
        report["collections"][name] = entry
    return report


def _write_reports(report: dict[str, Any], tag: str) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"tenancy_{tag}_{int(time.time())}.json"
    path.write_text(json.dumps(report, indent=2, default=str),
                    encoding="utf-8")
    return path


def _backup(docs: list[dict[str, Any]]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"tenancy_backup_{int(time.time())}.json"
    path.write_text(json.dumps(docs, default=str), encoding="utf-8")
    return path


async def apply(collections: list[str]) -> dict[str, Any]:
    report = await audit(collections)
    if "error" in report:
        return report
    db = astra_memory.astra_db()
    raw = _scan_docs_for_backup(db, collections)
    backup = _backup(raw)
    report["backup"] = str(backup)
    writes, failures = 0, []
    for d in report["docs"]:
        if d["verdict"] == "verified":
            continue
        try:
            col = db.get_collection(d["collection"])
            col.update_one({"_id": d["_id"]},
                           {"$set": {"user_id": d["tenant"],
                                     "migrated_at": time.time()}})
            writes += 1
        except Exception as e:
            failures.append({"_id": d["_id"], "error": str(e)[:200]})
    report["writes"] = writes
    report["failures"] = failures
    return report


def _scan_docs_for_backup(db: Any, collections: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name in collections:
        try:
            col = db.get_collection(name)
        except Exception:
            continue
        for doc in _scan(col):
            doc = dict(doc)
            doc.pop("$vector", None)  # embeddings are huge; ids suffice
            doc["_collection"] = name
            out.append(doc)
    return out


async def rollback(backup_file: str) -> dict[str, Any]:
    """Restore the pre-apply tenancy state recorded in a backup file."""
    db = astra_memory.astra_db()
    if db is None:
        return {"error": "Astra unavailable"}
    docs = json.loads(Path(backup_file).read_text(encoding="utf-8"))
    restored, failures = 0, []
    for doc in docs:
        try:
            col = db.get_collection(doc["_collection"])
            if "user_id" in doc:
                # was already stamped — restore the original value
                col.update_one({"_id": doc["_id"]},
                               {"$set": {"user_id": doc["user_id"]}})
            else:
                # had no user_id — remove the migration stamp entirely
                col.update_one({"_id": doc["_id"]},
                               {"$unset": {"user_id": "", "migrated_at": ""}})
            restored += 1
        except Exception as e:
            failures.append({"_id": doc.get("_id"), "error": str(e)[:200]})
    return {"restored": restored, "failures": failures}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True)
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--rollback", metavar="BACKUP_JSON")
    p.add_argument("--collection", action="append", dest="collections")
    args = p.parse_args()

    s = get_settings()
    collections = args.collections or [
        astra_memory.SHORT, astra_memory.LONG, s.astra_evidence_collection]

    if args.rollback:
        result = asyncio.run(rollback(args.rollback))
        path = _write_reports({"rollback": result}, "rollback")
        print(f"rollback → {path}\n{json.dumps(result, indent=2)}")
        return 0 if not result.get("failures") else 1

    if args.apply:
        if os.environ.get("NOVA_MIGRATION_ACK") != "1":
            print("refusing: set NOVA_MIGRATION_ACK=1 to apply writes")
            return 2
        result = asyncio.run(apply(collections))
        path = _write_reports(result, "apply")
        print(f"applied → {path}\n{json.dumps(result['collections'], indent=2)}")
        print(f"writes={result.get('writes')} "
              f"failures={len(result.get('failures', []))} "
              f"backup={result.get('backup')}")
        return 0 if not result.get("failures") else 1

    result = asyncio.run(audit(collections))
    path = _write_reports(result, "audit")
    print(f"audit → {path}\n{json.dumps(result['collections'], indent=2)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
