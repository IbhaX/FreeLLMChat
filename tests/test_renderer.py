import asyncio

from app.services import renderer
from app.services.component_enforce import has_renderable_components

ENV = (
    '```nova-response\n{"response_type":"general","content":"Hi",'
    '"components":[{"type":"list","data":{"items":["a"]}}]}\n```'
)
MD = "## Q\n\n- one\n- two\n"


def _res(text):
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


class FakeGateway:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    async def chat(self, session, payload, **kw):
        self.calls.append(payload)
        if isinstance(self.reply, Exception):
            raise self.reply
        return _res(self.reply), None, None, "m"


def test_convert_success_uses_fast_model_without_tools():
    gw = FakeGateway(ENV)
    out = asyncio.run(renderer.convert_to_components(gw, None, MD))
    assert out == ENV
    sent = gw.calls[0]
    # first try uses the first fast-role candidate, never bare "auto"
    from app.services.model_policy import stage_models
    assert sent["model"] == stage_models("auto:fast", role="fast")[0]
    assert not sent["model"].startswith("auto")
    assert sent["stream"] is False and sent["temperature"] == 0
    assert "tools" not in sent and "tool_choice" not in sent
    # dedicated renderer agent: system prompt + one user prompt carrying
    # the source answer and the convert instruction
    assert [m["role"] for m in sent["messages"]] == ["system", "user"]
    assert "Rewrite your previous answer" in sent["messages"][-1]["content"]


def test_convert_rejects_non_components_and_errors():
    assert asyncio.run(renderer.convert_to_components(FakeGateway(MD), None, MD)) is None
    gw = FakeGateway(RuntimeError("x"))
    assert asyncio.run(renderer.convert_to_components(gw, None, MD)) is None
    assert asyncio.run(renderer.convert_to_components(FakeGateway(ENV), None, "")) is None


def test_convert_preserves_history():
    gw = FakeGateway(ENV)
    asyncio.run(renderer.convert_to_components(
        gw, None, MD,
        history=[{"role": "user", "content": "q?"}, {"role": "system", "content": "skip me"}],
    ))
    msgs = gw.calls[0]["messages"]
    assert [m["role"] for m in msgs] == ["system", "user"]
    # prior user/assistant turns fold into the prompt; system turns don't
    assert "q?" in msgs[-1]["content"]
    assert "skip me" not in msgs[-1]["content"]


def test_rendered_output_is_renderable():
    assert has_renderable_components(ENV)
