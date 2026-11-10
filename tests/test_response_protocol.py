"""Tests for the structured response protocol (app.response_protocol).

Covers:
* Envelope validation for every response_type.
* Component allowlist enforcement.
* Per-component schema descriptors.
* ``validate_envelope`` / ``is_valid_envelope`` boundary behavior.
* ``envelope_to_fence`` round-trip with the ``nova-response`` tag.
* The ``/v1/render-protocol`` endpoint contract.
* Backward compatibility: plain markdown responses still classify.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.response_protocol import (
    RESPONSE_FENCE_TAG,
    ChartType,
    ComponentType,
    ResponseEnvelope,
    ResponseType,
    component_schemas,
    envelope_to_fence,
    is_valid_envelope,
    protocol_descriptor,
    validate_envelope,
)

ADMIN = {"X-Admin-Token": "test-admin"}


# ---------- envelope validation ----------

def test_minimal_envelope_is_valid():
    env = validate_envelope({"response_type": "general", "content": "hello"})
    assert env.response_type == ResponseType.GENERAL
    assert env.content == "hello"
    assert env.components == []


def test_every_response_type_accepts_minimal_envelope():
    for rt in ResponseType:
        env = validate_envelope({"response_type": rt.value, "content": "x"})
        assert env.response_type == rt


def test_unknown_response_type_rejected():
    with pytest.raises(Exception):
        validate_envelope({"response_type": "not_a_real_type", "content": "x"})


def test_envelope_with_all_optional_fields():
    payload = {
        "response_type": "data_visualization",
        "content": "Monthly revenue breakdown.",
        "components": [
            {"type": "metric_card", "data": {"title": "Total Revenue", "value": 125000, "unit": "USD"}},
            {"type": "chart", "data": {"chart_type": "bar", "data": [{"month": "Jan", "revenue": 25000}]}},
        ],
        "metadata": {"period": "Q1"},
        "sources": [{"title": "Finance DB", "url": "https://example.com"}],
        "actions": [{"label": "Drill down", "action": "drill"}],
        "suggested_prompts": ["Compare revenue by month", "Show trends"],
        "visualization": {"chart_type": "line", "data": [{"x": 1, "y": 2}]},
        "attachments": [{"kind": "file", "name": "report.pdf"}],
    }
    env = validate_envelope(payload)
    assert len(env.components) == 2
    assert env.components[0].type == ComponentType.METRIC_CARD
    assert env.components[1].data["chart_type"] == "bar"
    assert env.sources[0].title == "Finance DB"
    assert env.suggested_prompts == ["Compare revenue by month", "Show trends"]
    assert env.visualization.chart_type == ChartType.LINE


# ---------- component allowlist ----------

def test_every_component_type_is_allowlisted_and_renderable():
    schemas = component_schemas()
    for ct in ComponentType:
        assert ct.value in schemas, f"missing schema descriptor for {ct.value}"
        assert "required" in schemas[ct.value]
        assert "description" in schemas[ct.value]


def test_unknown_component_type_rejected():
    with pytest.raises(Exception):
        validate_envelope(
            {"response_type": "general", "components": [{"type": "arbitrary_html", "data": {}}]}
        )


def test_component_data_defaults_to_empty_dict():
    env = validate_envelope(
        {"response_type": "general", "components": [{"type": "metric_card"}]}
    )
    assert env.components[0].data == {}


# ---------- is_valid_envelope ----------

def test_is_valid_envelope_true_for_valid():
    assert is_valid_envelope({"response_type": "success", "content": "done"})


def test_is_valid_envelope_false_for_invalid():
    assert not is_valid_envelope({"response_type": "bogus"})
    assert not is_valid_envelope({"components": []})
    assert not is_valid_envelope("not a dict")


# ---------- fence round-trip ----------

def test_envelope_to_fence_uses_correct_tag():
    env = ResponseEnvelope(response_type=ResponseType.SUCCESS, content="ok")
    fence = envelope_to_fence(env)
    assert f"```{RESPONSE_FENCE_TAG}" in fence
    assert fence.endswith("```")
    # The inner JSON must parse back to the envelope.
    inner = fence.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    parsed = json.loads(inner)
    assert parsed["response_type"] == "success"
    assert parsed["content"] == "ok"


def test_fence_tag_constant_is_stable():
    assert RESPONSE_FENCE_TAG == "nova-response"


# ---------- protocol descriptor ----------

def test_protocol_descriptor_lists_all_types():
    d = protocol_descriptor()
    assert set(d["response_types"]) == {rt.value for rt in ResponseType}
    assert set(d["component_types"]) == {ct.value for ct in ComponentType}
    assert set(d["chart_types"]) == {ct.value for ct in ChartType}
    assert d["fence_tag"] == RESPONSE_FENCE_TAG
    assert "envelope_fields" in d


# ---------- /v1/render-protocol endpoint ----------

def test_render_protocol_endpoint_serves_descriptor():
    with TestClient(app) as c:
        r = c.get("/v1/render-protocol")
        assert r.status_code == 200
        body = r.json()
        assert body["fence_tag"] == RESPONSE_FENCE_TAG
        assert "metric_card" in body["components"]
        assert "chart" in body["components"]


def test_render_protocol_prompt_endpoint():
    from app.response_protocol import structured_system_prompt

    with TestClient(app) as c:
        r = c.get("/v1/render-protocol/prompt")
        assert r.status_code == 200
        body = r.json()
        assert "prompt" in body
        assert RESPONSE_FENCE_TAG in body["prompt"]
        assert "response_type" in body["prompt"]
        assert "components" in body["prompt"]


def test_structured_system_prompt_is_nonempty_and_selfcontained():
    from app.response_protocol import structured_system_prompt

    p = structured_system_prompt()
    assert isinstance(p, str)
    assert len(p) > 200
    assert RESPONSE_FENCE_TAG in p
    assert "response_type" in p
    assert "components" in p
    assert "MANDATORY" in p


def test_render_protocol_endpoint_requires_gateway_key_when_set(monkeypatch):
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("GATEWAY_API_KEY", "secret")
    get_settings.cache_clear()
    try:
        with TestClient(app) as c:
            assert c.get("/v1/render-protocol").status_code == 401
            assert c.get(
                "/v1/render-protocol", headers={"Authorization": "Bearer secret"}
            ).status_code == 200
    finally:
        monkeypatch.delenv("GATEWAY_API_KEY", raising=False)
        get_settings.cache_clear()


# ---------- backward compatibility ----------

def test_plain_markdown_response_is_valid_minimal_envelope():
    # A backend that only emits plain text can still produce a valid envelope.
    env = validate_envelope({"response_type": "general", "content": "# Hi\n\nSome **markdown**."})
    assert env.components == []
    assert env.content.startswith("# Hi")


def test_empty_content_envelope_is_valid():
    env = validate_envelope({"response_type": "general"})
    assert env.content == ""
    assert env.components == []


# ---------- architecture / flowchart component protocol ----------

def test_architecture_diagram_is_allowlisted_and_documented():
    env = validate_envelope({
        "response_type": "complex",
        "content": "The system is split into layers.",
        "components": [{
            "type": "architecture_diagram",
            "data": {
                "title": "Agent architecture",
                "layers": [
                    {"title": "API Gateway", "subtitle": "Authentication"},
                    {"title": "Runtime", "nodes": [{"title": "Memory"}, {"title": "Tools"}]},
                ],
            },
        }],
    })
    assert env.components[0].type == ComponentType.ARCHITECTURE_DIAGRAM
    descriptor = protocol_descriptor()
    assert "architecture_diagram" in descriptor["component_types"]
    assert descriptor["components"]["architecture_diagram"]["required"] == ["layers"]


def test_flowchart_is_allowlisted_and_prompt_teaches_when_to_use_it():
    env = validate_envelope({
        "response_type": "complex",
        "components": [{
            "type": "flowchart",
            "data": {
                "nodes": [{"id": "start", "label": "Start", "kind": "start"}],
                "edges": [],
            },
        }],
    })
    assert env.components[0].type == ComponentType.FLOWCHART
    prompt = structured_system_prompt()
    assert "architecture_diagram" in prompt
    assert "flowchart" in prompt
    assert "do not substitute a text/ASCII diagram" in prompt
