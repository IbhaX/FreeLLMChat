from __future__ import annotations

from app.services.agents import _messages
from app.services.settings_store import custom_instructions_block


class _Cfg:
    system_prompt = "You are a pirate."
    config = {}
    output_format = "plain"


def test_custom_instructions_block_format():
    assert custom_instructions_block("Be terse").startswith("## USER CUSTOM INSTRUCTIONS")
    assert "Be terse" in custom_instructions_block("Be terse")


def test_messages_include_custom_instructions():
    msgs = _messages(_Cfg(), "hi", [], custom="Always cite sources.")
    sys_msg = msgs[0]
    assert sys_msg["role"] == "system"
    assert "You are a pirate." in sys_msg["content"]
    assert "## USER CUSTOM INSTRUCTIONS" in sys_msg["content"]
    assert "Always cite sources." in sys_msg["content"]


def test_messages_without_custom_unchanged():
    msgs = _messages(_Cfg(), "hi", [])
    assert "USER CUSTOM INSTRUCTIONS" not in msgs[0]["content"]
