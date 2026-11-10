import asyncio

from app.services import renderer
from app.services.component_enforce import has_renderable_components

ENV = '```nova-response\n{"response_type":"general","content":"Hi","components":[{"type":"list","data":{"items":["a"]}}]}\n```'
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
    assert sent["model"] == renderer.renderer_model()
    assert sent["stream"] is False and sent["temperature"] == 0
    assert "tools" not in sent
    # conversation shape: system, assistant original, user instruction last
    assert sent["messages"][-2]["role"] == "assistant"
    assert sent["messages"][-1]["content"].startswith("Rewrite your previous answer")


def test_convert_rejects_non_components_and_errors():
    assert asyncio.run(renderer.convert_to_components(FakeGateway(MD), None, MD)) is None
    assert asyncio.run(renderer.convert_to_components(FakeGateway(RuntimeError("x")), None, MD)) is None
    assert asyncio.run(renderer.convert_to_components(FakeGateway(ENV), None, "")) is None


def test_convert_preserves_history():
    gw = FakeGateway(ENV)
    asyncio.run(renderer.convert_to_components(
        gw, None, MD,
        history=[{"role": "user", "content": "q?"}, {"role": "system", "content": "skip me"}],
    ))
    roles = [m["role"] for m in gw.calls[0]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]


def test_rendered_output_is_renderable():
    assert has_renderable_components(ENV)
