"""Bridge the app's ``TOOLS`` registry into a pydantic-ai toolset.

The registry (``app.services.tools.TOOLS``) stays the single source of
truth for tool specs and execution; this module only adapts it to
``AbstractToolset`` so agents invoke tools natively — no more
hand-rolled tool-call loops.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Literal

from pydantic_ai.tools import RunContext, ToolDefinition
from pydantic_ai.toolsets import AbstractToolset, ToolsetTool

_AllowPartial = bool | Literal["off", "on", "trailing-strings"]

from app.agents.deps import AgentDeps
from app.services import tools as registry

_TOOL_TIMEOUT_S = 30.0


class _PassthroughArgsValidator:
    """``SchemaValidatorProt``-compatible validator.

    Argument validation already happens inside ``registry``'s executor
    (missing/unexpected keys become tool errors); pydantic-core schema
    compilation per tool would duplicate that work for no gain.
    """

    def validate_python(
        self,
        obj: Any,
        *,
        allow_partial: _AllowPartial = False,
        **_kwargs: Any,
    ) -> Any:
        return obj

    def validate_json(
        self,
        data: str | bytes | bytearray,
        *,
        allow_partial: _AllowPartial = False,
        **_kwargs: Any,
    ) -> Any:
        import json
        return json.loads(data)

    def validate_assignment(self, _field: str, obj: Any, _f: Any = None) -> Any:
        return obj


class RegistryToolset(AbstractToolset[AgentDeps]):
    """Expose a subset of the app tool registry as a pydantic-ai toolset."""

    def __init__(
        self,
        names: list[str] | None,
        *,
        output_hook: Callable[[str, str], None] | None = None,
        parent_task_id: str | None = None,
        actor: str | None = None,
    ) -> None:
        super().__init__()
        if names is True or names in (None, [], "*"):
            names = ["*"]
        if names == ["*"]:
            self._names = list(registry.TOOLS)
        else:
            self._names = [n for n in names if n in registry.TOOLS]
        self._output_hook = output_hook
        self._parent_task_id = parent_task_id
        self._actor = actor

    @property
    def id(self) -> str:
        return "app-registry"

    async def get_tools(
        self, ctx: RunContext[AgentDeps]
    ) -> dict[str, ToolsetTool[AgentDeps]]:
        out: dict[str, ToolsetTool[AgentDeps]] = {}
        for name in self._names:
            tool = registry.TOOLS[name]
            out[name] = ToolsetTool(
                toolset=self,
                tool_def=ToolDefinition(
                    name=name,
                    description=tool.description,
                    parameters_json_schema=tool.parameters,
                ),
                max_retries=1,
                args_validator=_PassthroughArgsValidator(),
            )
        return out

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[AgentDeps],
        tool: ToolsetTool[AgentDeps],
    ) -> Any:
        """Execute one registered tool with activity visibility."""
        import asyncio

        spec = registry.TOOLS.get(name)
        label = (spec.description or name).split(".")[0] if spec else name
        scope: dict[str, Any] = {}
        if self._parent_task_id:
            scope["task_id"] = self._parent_task_id
            scope["parent_task_id"] = self._parent_task_id
        if self._actor:
            scope["actor"] = self._actor
        ctx.deps.emit_event({
            "event": "tool", "kind": "tool", "status": "running",
            "title": f"{label}…", "tool": name,
            "args": {k: str(v)[:120] for k, v in tool_args.items()},
            **scope,
        })
        started = time.perf_counter()
        try:
            output = await asyncio.wait_for(
                registry.run_tool(name, tool_args), timeout=_TOOL_TIMEOUT_S)
            ok = not str(output).lower().startswith((
                "error:", "tool error:", "fetch error:"))
            ctx.deps.emit_event({
                "event": "tool", "kind": "tool",
                "status": "done" if ok else "failed",
                "title": label, "tool": name,
                "duration_s": round(time.perf_counter() - started, 2),
                **scope,
            })
            if self._output_hook is not None:
                try:
                    self._output_hook(name, str(output))
                except Exception:
                    pass
            return str(output)[:12000]
        except TimeoutError:
            ctx.deps.emit_event({
                "event": "tool", "kind": "tool", "status": "failed",
                "title": f"{label} timed out", "tool": name,
            })
            return "tool error: execution timed out"
        except Exception as exc:  # tool failures become model-visible text
            ctx.deps.emit_event({
                "event": "tool", "kind": "tool", "status": "failed",
                "title": label, "tool": name,
            })
            return f"tool error: {type(exc).__name__}: {exc}"


def build_toolset(
    names: list[str] | None,
    *,
    output_hook: Callable[[str, str], None] | None = None,
    parent_task_id: str | None = None,
    actor: str | None = None,
) -> RegistryToolset | None:
    """Toolset for the given registry names; ``None`` when nothing applies."""
    ts = RegistryToolset(names, output_hook=output_hook,
                         parent_task_id=parent_task_id, actor=actor)
    return ts if ts._names else None
