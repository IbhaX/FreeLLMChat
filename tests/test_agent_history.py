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



def test_bounded_history_keeps_recent_valid_turns_within_budget():
    from app.services.agents import _bounded_history

    history = [
        {"role": "system", "content": "ignore"},
        {"role": "user", "content": "old" * 20},
        {"role": "assistant", "content": "recent answer"},
        {"role": "user", "content": "latest question"},
        None,
    ]
    bounded = _bounded_history(history, max_messages=2, max_chars=30)

    assert [item["role"] for item in bounded] == ["assistant", "user"]
    assert bounded[-1]["content"] == "latest question"
    assert sum(len(item["content"]) for item in bounded) <= 30


def test_memory_relevance_ranks_matching_context():
    from app.services.agents import _memory_relevance

    assert _memory_relevance("User builds Python FastAPI services", "Python API") > 0
    assert _memory_relevance("User likes hiking", "Python API") == 0
