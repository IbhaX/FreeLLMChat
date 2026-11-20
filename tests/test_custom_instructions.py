from __future__ import annotations

from app.agents.factory import compose_system_prompt
from app.services.settings_store import custom_instructions_block


class _Cfg:
    system_prompt = "You are a pirate."
    config = {}
    output_format = "plain"


def test_custom_instructions_block_format():
    assert custom_instructions_block("Be terse").startswith("## USER CUSTOM INSTRUCTIONS")
    assert "Be terse" in custom_instructions_block("Be terse")


def test_prompt_includes_custom_instructions():
    prompt = compose_system_prompt(_Cfg(), "Always cite sources.")
    assert "You are a pirate." in prompt
    assert "## USER CUSTOM INSTRUCTIONS" in prompt
    assert "Always cite sources." in prompt


def test_prompt_without_custom_unchanged():
    prompt = compose_system_prompt(_Cfg())
    assert "USER CUSTOM INSTRUCTIONS" not in prompt
