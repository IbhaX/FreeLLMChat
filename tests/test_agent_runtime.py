import pytest

from app.services.tools import _calculator, _execute_tool_call, _ssrf_blocked


@pytest.mark.asyncio
async def test_calculator_rejects_extreme_or_complex_expressions():
    assert "too large" in await _calculator("10**1001")
    assert "too complex" in await _calculator("+".join(["1"] * 70))


@pytest.mark.asyncio
async def test_calculator_returns_valid_result():
    assert await _calculator("(3 + 4) * 2") == "(3 + 4) * 2 = 14"


@pytest.mark.asyncio
async def test_tool_call_rejects_unknown_tool_and_invalid_arguments():
    call_id, name, args, output = await _execute_tool_call({
        "id": "call-1",
        "function": {"name": "not_a_tool", "arguments": "{}"},
    })
    assert call_id == "call-1"
    assert name == "not_a_tool"
    assert "unknown tool" in output

    _, _, _, output = await _execute_tool_call({
        "id": "call-2",
        "function": {"name": "calculator", "arguments": '{"unexpected": 1}'},
    })
    assert "missing" in output
    assert "unexpected" in output


def test_ssrf_guard_blocks_loopback_and_private_literals():
    assert _ssrf_blocked("http://127.0.0.1/admin")
    assert _ssrf_blocked("http://10.0.0.5/")
    assert _ssrf_blocked("http://[::1]/")
