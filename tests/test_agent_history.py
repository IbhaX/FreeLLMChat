from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from app.services.agents import _to_model_history


def test_history_converts_user_and_assistant_turns_in_order():
    messages = _to_model_history([
        {"role": "user", "content": "My name is Ada."},
        {"role": "assistant", "content": "Hello, Ada."},
        {"role": "user", "content": "What is my name?"},
    ])

    assert len(messages) == 3
    assert isinstance(messages[0], ModelRequest)
    assert isinstance(messages[0].parts[0], UserPromptPart)
    assert messages[0].parts[0].content == "My name is Ada."
    assert isinstance(messages[1], ModelResponse)
    assert isinstance(messages[1].parts[0], TextPart)
    assert messages[1].parts[0].content == "Hello, Ada."
    assert messages[2].parts[0].content == "What is my name?"


def test_history_ignores_tool_roles_and_empty_content():
    messages = _to_model_history([
        {"role": "tool", "content": "tool output"},
        {"role": "assistant", "content": ""},
        {"role": "user", "content": "Continue", "name": "caller"},
    ])

    assert len(messages) == 1
    assert messages[0].parts[0].content == "Continue"


def test_history_accepts_text_fallback_and_skips_invalid_items():
    messages = _to_model_history([
        None,
        {"role": "user", "text": "Earlier context"},
        {"role": "system", "content": "untrusted system turn"},
        {"role": "assistant", "content": 42},
    ])

    assert len(messages) == 1
    assert messages[0].parts[0].content == "Earlier context"
