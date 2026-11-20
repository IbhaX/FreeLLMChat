"""PydanticAI dependency container shared by all agents and tools."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.gateway import Gateway


@dataclass
class AgentDeps:
    """Runtime dependencies injected into every agent run and tool call.

    ``emit`` receives the app's activity events (``routing``, ``try``,
    ``usage``, ``note``, ``error``) so tool execution and delegation stay
    visible in the existing activity UI.
    """

    gateway: Gateway
    emit: Callable[[dict[str, Any]], None] | None = None
    conversation_id: str | None = None
    model_override: str | None = None
    max_tool_calls: int = 10
    extra: dict[str, Any] = field(default_factory=dict)

    def emit_event(self, event: dict[str, Any]) -> None:
        """Emit an activity event when a consumer is attached."""
        if self.emit is not None:
            self.emit(event)
