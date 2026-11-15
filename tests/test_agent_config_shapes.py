"""Agent config must tolerate the shapes the UI and seed data actually store.

Builtin/seeded agents store ``capabilities`` as a plain list of strings and
``tools`` at the top level — ``_tool_names``/``_compile_system_prompt``
crashed on that with ``'list' object has no attribute 'get'`` (group chat
agents all returned the raw error text to users).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.services.agents import _caps, _compile_system_prompt, _tool_names


def _agent(config: Any, enable_tools: bool = False,
           output_format: str = "text") -> SimpleNamespace:
    return SimpleNamespace(
        name="a", system_prompt="sys", config=config,
        enable_tools=enable_tools, output_format=output_format,
    )


def test_list_capabilities_does_not_crash() -> None:
    a = _agent({"capabilities": ["chart selection", "dashboard design"],
                "tools": []}, enable_tools=False)
    assert _tool_names(a) == []
    assert "sys" in _compile_system_prompt(a)


def test_top_level_tools_list_is_used() -> None:
    a = _agent({"capabilities": ["api design"], "tools": ["web_search"]},
               enable_tools=True)
    assert _tool_names(a) == ["web_search"]


def test_wizard_dict_capabilities_still_work() -> None:
    a = _agent({"capabilities": {"tools_enabled": True,
                                 "tools": ["calculator"]}})
    assert _tool_names(a) == ["calculator"]


def test_enabled_with_no_tool_list_falls_back_to_all() -> None:
    assert _tool_names(_agent({}, enable_tools=True)) == ["*"]


def test_non_dict_config_is_safe() -> None:
    for bad in (None, [], "oops"):
        a = _agent(bad, enable_tools=True)
        assert _tool_names(a) == ["*"]
        assert _compile_system_prompt(a).endswith("sys")


def test_caps_only_returns_dicts() -> None:
    assert _caps({"capabilities": ["a", "b"]}) == {}
    assert _caps({"capabilities": {"tools_enabled": True}}) == {"tools_enabled": True}


def test_null_timeout_falls_back_to_default() -> None:
    """Seed agents store "timeout": null — float(None) used to crash the
    whole agent reply with 'float() argument must be ...'."""
    cfg = _agent({"execution": {"timeout": None}})
    execution = cfg.config.get("execution", {})
    timeout_s = max(1.0, min(float(execution.get("timeout") or 120), 600.0))
    assert timeout_s == 120.0
