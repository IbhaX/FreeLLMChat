"""Subscription entitlements — plan → capability map with partial rollout.

Capabilities are the single source of truth for feature gating. Each one
declares the minimum plan tier and a rollout percentage: at <100% the
feature is granted to a deterministic slice of eligible users, so a plan
gate and a gradual release compose cleanly. Bucketing is stable per
(user, capability) via SHA-256 — a user in the rollout stays in.
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping, Optional

PLAN_ORDER: tuple[str, ...] = ("free", "basic", "plus", "pro", "ultra")

# capability → minimum plan + rollout percent (deterministic per user)
CAPABILITIES: dict[str, dict[str, Any]] = {
    "library_captures":  {"plan": "basic", "rollout": 100},
    "deep_research":     {"plan": "plus",  "rollout": 100},
    "memory_explorer":   {"plan": "plus",  "rollout": 50},
    "custom_agents":     {"plan": "pro",   "rollout": 100},
    "faster_streaming":  {"plan": "pro",   "rollout": 100},
    "ultra_rate_limits": {"plan": "ultra", "rollout": 100},
    "early_access":      {"plan": "ultra", "rollout": 25},
}


def plan_rank(plan: str | None) -> int:
    """Ordinal tier index; unknown plans fall back to free."""
    try:
        return PLAN_ORDER.index(plan or "free")
    except ValueError:
        return 0


def _bucket(user_id: int, capability: str) -> int:
    """Stable 0–99 rollout bucket for a user/capability pair."""
    h = hashlib.sha256(f"{user_id}:{capability}".encode("utf-8"))
    return int(h.hexdigest()[:8], 16) % 100


def has_cap(plan: str | None, capability: str,
            user_id: int = 0, *, is_admin: bool = False) -> bool:
    """Whether a plan+user pair holds a capability right now.

    Admin role bypasses gating. Rollout <100% grants the cap to a stable
    deterministic slice of otherwise-eligible users."""
    if is_admin:
        return True
    spec = CAPABILITIES.get(capability)
    if spec is None:
        return False
    if plan_rank(plan) < plan_rank(spec["plan"]):
        return False
    rollout = int(spec.get("rollout", 100))
    if rollout >= 100:
        return True
    if rollout <= 0:
        return False
    return _bucket(user_id, capability) < rollout


def caps_for(plan: str | None, user_id: int = 0,
             *, is_admin: bool = False) -> dict[str, bool]:
    """Full capability map for a user — drives /api/entitlements."""
    return {cap: has_cap(plan, cap, user_id, is_admin=is_admin)
            for cap in CAPABILITIES}


def upgrade_error(capability: str) -> dict[str, Any]:
    """Standard 403 payload — carries what tier unlocks the feature."""
    spec = CAPABILITIES[capability]
    return {"error": {
        "message": f"This feature requires the {spec['plan']} plan or "
                   "higher",
        "type": "upgrade_required",
        "capability": capability,
        "plan_required": spec["plan"]}}
