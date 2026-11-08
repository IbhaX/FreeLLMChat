"""Per-provider circuit breaker. In-memory: this gateway runs as a single
local process; state is cacheable/disposable per design rules."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from app.config import get_settings


class State(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class _Breaker:
    failures: int = 0
    state: State = State.CLOSED
    opened_at: float = 0.0
    half_open_in_flight: bool = field(default=False)


class CircuitBreaker:
    def __init__(self, threshold: int | None = None, reset_s: float | None = None) -> None:
        s = get_settings()
        self.threshold = threshold or s.circuit_breaker_failures
        self.reset_s = reset_s or s.circuit_breaker_reset_s
        self._breakers: dict[str, _Breaker] = {}

    def _get(self, provider: str) -> _Breaker:
        return self._breakers.setdefault(provider, _Breaker())

    def allow(self, provider: str) -> bool:
        b = self._get(provider)
        if b.state == State.CLOSED:
            return True
        if b.state == State.OPEN:
            if time.monotonic() - b.opened_at >= self.reset_s:
                b.state = State.HALF_OPEN
                b.half_open_in_flight = False
            else:
                return False
        # half-open: allow a single probe
        if b.half_open_in_flight:
            return False
        b.half_open_in_flight = True
        return True

    def success(self, provider: str) -> None:
        b = self._get(provider)
        b.failures = 0
        b.state = State.CLOSED
        b.half_open_in_flight = False

    def failure(self, provider: str) -> None:
        b = self._get(provider)
        b.failures += 1
        b.half_open_in_flight = False
        if b.state == State.HALF_OPEN or b.failures >= self.threshold:
            b.state = State.OPEN
            b.opened_at = time.monotonic()

    def status(self) -> dict[str, dict[str, object]]:
        return {
            name: {"state": b.state.value, "failures": b.failures}
            for name, b in self._breakers.items()
        }
