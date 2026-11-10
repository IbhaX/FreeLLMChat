"""Structured AI response protocol — typed envelope + component schemas.

The gateway is a passthrough: it does not rewrite model output. Models that
want rich rendering emit a fenced code block whose language tag is
``nova-response`` containing a JSON document conforming to
:class:`ResponseEnvelope`. The frontend classifier detects this tag and
renders the structured components; plain Markdown continues to render
unchanged (backward compatibility).

This module is the single source of truth for the protocol on the backend.
It is used by:

* ``/v1/render-protocol`` — a self-describing endpoint the frontend and
  agents consult to learn the supported component schemas.
* :func:`validate_envelope` — defensive validation at boundary layers
  (agents, tools) that choose to emit structured responses directly.
* The test suite — to assert the contract.

Design rules (see global engineering rules):
* Every public model declares field types explicitly; ``Any`` only where the
  payload is intentionally free-form (``Component.data``).
* Component schemas are intentionally permissive on optional metadata so the
  frontend can degrade gracefully when a field is absent.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, ValidationError, model_validator

# The fenced-code language tag the frontend classifier looks for. Keeping it
# in one place avoids drift between backend docs and frontend detection.
RESPONSE_FENCE_TAG = "nova-response"


class ResponseType(str, Enum):
    """Top-level response categories the frontend knows how to render."""

    GENERAL = "general"
    CODE = "code"
    TABLE = "table"
    LIST = "list"
    MATH = "math"
    DATA_VISUALIZATION = "data_visualization"
    STATISTICS = "statistics"
    SEARCH_RESULTS = "search_results"
    PRODUCT_RECOMMENDATIONS = "product_recommendations"
    BUSINESS_RECOMMENDATIONS = "business_recommendations"
    WEATHER = "weather"
    FINANCIAL = "financial"
    INSTRUCTIONS = "instructions"
    COMPARISON = "comparison"
    API_RESPONSE = "api_response"
    ERROR = "error"
    WARNING = "warning"
    SUCCESS = "success"
    FILE_ATTACHMENT = "file_attachment"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    PROGRESS = "progress"
    INTERACTIVE_QUESTION = "interactive_question"
    COMPLEX = "complex"


class ChartType(str, Enum):
    BAR = "bar"
    LINE = "line"
    PIE = "pie"
    DONUT = "donut"
    AREA = "area"
    SCATTER = "scatter"


class ComponentType(str, Enum):
    """Allowlisted component types the frontend registry may render.

    The frontend rejects any component ``type`` not in this allowlist — the
    model never ships arbitrary executable HTML/JS, only validated schemas.
    """

    METRIC_CARD = "metric_card"
    CHART = "chart"
    TABLE = "table"
    LIST = "list"
    CODE = "code"
    SEARCH_RESULT = "search_result"
    PRODUCT_CARD = "product_card"
    BUSINESS_CARD = "business_card"
    WEATHER = "weather"
    FINANCIAL_SUMMARY = "financial_summary"
    JSON_VIEWER = "json_viewer"
    IMAGE_GALLERY = "image_gallery"
    VIDEO = "video"
    AUDIO = "audio"
    FILE_PREVIEW = "file_preview"
    PROGRESS = "progress"
    INSTRUCTIONS = "instructions"
    COMPARISON = "comparison"
    INTERACTIVE = "interactive"
    MATH = "math"
    ERROR = "error"
    WARNING = "warning"
    SUCCESS = "success"
    TIMELINE = "timeline"
    STAT_GRID = "stat_grid"
    CALLOUT = "callout"
    GAUGE = "gauge"
    SPARKLINE = "sparkline"
    KEYVALUE = "keyvalue"
    BADGE = "badge"
    QUOTE = "quote"
    DIVIDER = "divider"
    DIFF = "diff"
    HEATMAP = "heatmap"
    TAGCLOUD = "tagcloud"


class Component(BaseModel):
    """A single renderable component within an envelope.

    ``data`` is intentionally ``dict[str, Any]`` because each component type
    has its own shape; the frontend validates per-type using the schema
    descriptors exposed by :func:`component_schemas`. This keeps the protocol
    extensible without a backend redeploy for every new visual tweak, while
    the allowlisted ``type`` still prevents arbitrary component injection.
    """

    type: ComponentType
    data: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}


class Source(BaseModel):
    """A citation / provenance entry attached to a response."""

    title: str
    url: Optional[str] = None
    source: Optional[str] = None
    favicon: Optional[str] = None
    thumbnail: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MessageAction(BaseModel):
    """A suggested action button the frontend may render."""

    label: str
    action: str
    payload: dict[str, Any] = Field(default_factory=dict)


class Attachment(BaseModel):
    """A file/media attachment descriptor."""

    kind: str  # file | image | video | audio
    name: str
    url: Optional[str] = None
    mime_type: Optional[str] = None
    size: Optional[int] = None
    thumbnail: Optional[str] = None


class Visualization(BaseModel):
    """A top-level visualization hint, in addition to component-level charts."""

    chart_type: ChartType
    title: Optional[str] = None
    data: list[dict[str, Any]] = Field(default_factory=list)
    labels: Optional[dict[str, str]] = None
    options: dict[str, Any] = Field(default_factory=dict)


class ResponseEnvelope(BaseModel):
    """The standardized response envelope between backend and frontend.

    Backward compatible: only ``response_type`` and ``content`` are required.
    Every other field is optional, so a plain conversational reply is a valid
    envelope with no components.
    """

    response_type: ResponseType
    content: str = ""
    components: list[Component] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    sources: list[Source] = Field(default_factory=list)
    actions: list[MessageAction] = Field(default_factory=list)
    suggested_prompts: list[str] = Field(default_factory=list)
    visualization: Optional[Visualization] = None
    attachments: list[Attachment] = Field(default_factory=list)

    model_config = {"extra": "allow"}

    @model_validator(mode="after")
    def _check_components_allowlist(self) -> "ResponseEnvelope":
        # Pydantic already constrains ``type`` to the ComponentType enum, but
        # we validate defensively in case a caller bypasses parsing.
        for c in self.components:
            try:
                ComponentType(c.type)
            except ValueError as exc:  # pragma: no cover - enum guards this
                raise ValueError(f"unsupported component type: {c.type}") from exc
        return self


def validate_envelope(payload: dict[str, Any]) -> ResponseEnvelope:
    """Validate an arbitrary dict against the response protocol.

    Raises ``pydantic.ValidationError`` on invalid input. Boundary layers
    (agents, tools) that emit structured responses should call this before
    serializing to SSE so malformed envelopes never reach the frontend.
    """

    return ResponseEnvelope.model_validate(payload)


def is_valid_envelope(payload: dict[str, Any]) -> bool:
    """Boolean variant of :func:`validate_envelope` for non-raising checks."""

    try:
        validate_envelope(payload)
        return True
    except ValidationError:
        return False


def envelope_to_fence(envelope: ResponseEnvelope) -> str:
    """Serialize an envelope into the ``nova-response`` fenced block the
    frontend classifier detects.

    Used by agents/tools that want to embed a structured response inside a
    normal completion stream. The returned string is intended to be appended
    to (or be the entirety of) the model's text content.
    """

    import json

    return (
        f"```{RESPONSE_FENCE_TAG}\n"
        + json.dumps(envelope.model_dump(mode="json"), ensure_ascii=False)
        + "\n```"
    )


def component_schemas() -> dict[str, dict[str, Any]]:
    """Human/machine-readable schema descriptors for each component type.

    Exposed via ``/v1/render-protocol`` so agents and the frontend share one
    contract definition. Descriptors are intentionally lightweight (name +
    required fields + a short description) rather than full JSON Schema, to
    keep the payload small and stable.
    """

    return {
        ComponentType.METRIC_CARD.value: {
            "required": ["title", "value"],
            "optional": ["unit", "label", "delta", "delta_unit", "icon", "trend"],
            "description": "A single important numerical metric with optional trend.",
        },
        ComponentType.CHART.value: {
            "required": ["chart_type", "data"],
            "optional": ["title", "labels", "options"],
            "description": "A chart visualization (bar/line/pie/donut/area/scatter).",
        },
        ComponentType.TABLE.value: {
            "required": ["rows"],
            "optional": ["columns", "title", "caption"],
            "description": "A structured table; rows is a list of row objects.",
        },
        ComponentType.LIST.value: {
            "required": ["items"],
            "optional": ["ordered", "title"],
            "description": "A structured (optionally ordered) list.",
        },
        ComponentType.CODE.value: {
            "required": ["language", "code"],
            "optional": ["filename", "caption"],
            "description": "A syntax-highlighted code block.",
        },
        ComponentType.SEARCH_RESULT.value: {
            "required": ["results"],
            "optional": ["title", "query"],
            "description": "A list of search result cards.",
        },
        ComponentType.PRODUCT_CARD.value: {
            "required": ["products"],
            "optional": ["title"],
            "description": "A list of product recommendation cards.",
        },
        ComponentType.BUSINESS_CARD.value: {
            "required": ["businesses"],
            "optional": ["title"],
            "description": "A list of business recommendation cards.",
        },
        ComponentType.WEATHER.value: {
            "required": ["location", "temperature", "condition"],
            "optional": ["unit", "icon", "humidity", "wind", "forecast", "feels_like"],
            "description": "A weather information widget.",
        },
        ComponentType.FINANCIAL_SUMMARY.value: {
            "required": ["title", "metrics"],
            "optional": ["chart", "period", "currency"],
            "description": "A financial summary with metrics and optional chart.",
        },
        ComponentType.JSON_VIEWER.value: {
            "required": ["data"],
            "optional": ["title", "collapsed"],
            "description": "A collapsible, syntax-highlighted JSON viewer.",
        },
        ComponentType.IMAGE_GALLERY.value: {
            "required": ["images"],
            "optional": ["title", "columns"],
            "description": "A responsive image gallery.",
        },
        ComponentType.VIDEO.value: {
            "required": ["url"],
            "optional": ["title", "thumbnail", "duration"],
            "description": "An embedded video preview card.",
        },
        ComponentType.AUDIO.value: {
            "required": ["url"],
            "optional": ["title", "duration", "waveform"],
            "description": "An audio player card.",
        },
        ComponentType.FILE_PREVIEW.value: {
            "required": ["files"],
            "optional": ["title"],
            "description": "File preview cards (name, size, type, download).",
        },
        ComponentType.PROGRESS.value: {
            "required": ["value"],
            "optional": ["label", "status", "total"],
            "description": "A progress indicator (0-100).",
        },
        ComponentType.INSTRUCTIONS.value: {
            "required": ["steps"],
            "optional": ["title"],
            "description": "A numbered instructional layout.",
        },
        ComponentType.COMPARISON.value: {
            "required": ["rows"],
            "optional": ["columns", "title"],
            "description": "A comparison table with highlighted differences.",
        },
        ComponentType.INTERACTIVE.value: {
            "required": ["fields"],
            "optional": ["title", "submit_label", "action"],
            "description": "A dynamic form (dropdowns, checkboxes, inputs, radios).",
        },
        ComponentType.MATH.value: {
            "required": ["expression"],
            "optional": ["display", "label"],
            "description": "A KaTeX/MathJax mathematical expression.",
        },
        ComponentType.ERROR.value: {
            "required": ["message"],
            "optional": ["title", "recovery", "code"],
            "description": "A contextual error component with recovery options.",
        },
        ComponentType.WARNING.value: {
            "required": ["message"],
            "optional": ["title", "actions"],
            "description": "A warning component.",
        },
        ComponentType.SUCCESS.value: {
            "required": ["message"],
            "optional": ["title", "actions"],
            "description": "A success confirmation component.",
        },
        ComponentType.TIMELINE.value: {
            "required": ["events"],
            "optional": ["title"],
            "description": "A vertical timeline of events (date, title, description, icon).",
        },
        ComponentType.STAT_GRID.value: {
            "required": ["stats"],
            "optional": ["title"],
            "description": "A grid of statistics cards (label, value, delta, unit, icon).",
        },
        ComponentType.CALLOUT.value: {
            "required": ["message"],
            "optional": ["title", "variant", "icon"],
            "description": "A highlighted callout box (variant: info, tip, important, quote).",
        },
        ComponentType.GAUGE.value: {
            "required": ["value"],
            "optional": ["label", "max", "unit", "color"],
            "description": "A circular gauge indicator (0 to max, default max=100).",
        },
        ComponentType.SPARKLINE.value: {
            "required": ["data"],
            "optional": ["label", "color", "type"],
            "description": "A small inline sparkline chart (data: list of numbers, type: line|bar|area).",
        },
        ComponentType.KEYVALUE.value: {
            "required": ["pairs"],
            "optional": ["title", "columns"],
            "description": "A key-value property list (pairs: [{key, value, highlight}]).",
        },
        ComponentType.BADGE.value: {
            "required": ["badges"],
            "optional": ["title"],
            "description": "A row of badges/pills (badges: [{label, variant, icon}]).",
        },
        ComponentType.QUOTE.value: {
            "required": ["text"],
            "optional": ["author", "source", "image"],
            "description": "A blockquote with attribution.",
        },
        ComponentType.DIFF.value: {
            "required": ["before", "after"],
            "optional": ["title", "language"],
            "description": "A side-by-side or inline diff view of two text blocks.",
        },
        ComponentType.HEATMAP.value: {
            "required": ["data"],
            "optional": ["title", "x_labels", "y_labels", "color_scale"],
            "description": "A heatmap grid (data: 2D array of numbers 0-1).",
        },
        ComponentType.TAGCLOUD.value: {
            "required": ["tags"],
            "optional": ["title"],
            "description": "A weighted tag cloud (tags: [{label, weight, color}]).",
        },
        ComponentType.DIVIDER.value: {
            "required": [],
            "optional": ["label"],
            "description": "A subtle horizontal section divider with optional label.",
        },
    }


def protocol_descriptor() -> dict[str, Any]:
    """Full self-describing descriptor for ``/v1/render-protocol``."""

    return {
        "fence_tag": RESPONSE_FENCE_TAG,
        "response_types": [t.value for t in ResponseType],
        "chart_types": [c.value for c in ChartType],
        "component_types": [c.value for c in ComponentType],
        "components": component_schemas(),
        "envelope_fields": {
            "response_type": "ResponseType (required)",
            "content": "str — markdown conversational text (default '')",
            "components": "list[Component] — structured renderable parts",
            "metadata": "dict — free-form metadata",
            "sources": "list[Source] — citations / provenance",
            "actions": "list[MessageAction] — suggested action buttons",
            "suggested_prompts": "list[str] — follow-up suggestions",
            "visualization": "Visualization | None — top-level chart hint",
            "attachments": "list[Attachment] — file/media descriptors",
        },
    }


def structured_system_prompt() -> str:
    """Compact system prompt for the structured response envelope.

    Slimmed down (~700 tokens vs ~2,950) to cut per-call input cost while
    keeping the full contract: envelope schema, rules, component names with
    required fields, and one example. Injected into every chat completion
    and structured agent run; also exposed via
    ``/v1/render-protocol/prompt``.
    """
    schemas = component_schemas()
    comp_list = "\n".join(
        f"- `{n}`: required={s['required']}" for n, s in sorted(schemas.items())
    )
    return f"""## Structured Response Protocol (MANDATORY)

Always wrap your response in a fenced block whose language tag is literally `{RESPONSE_FENCE_TAG}` (not `json`) containing ONE JSON object — never bare text/markdown:

```json
{{"response_type": "<general|code|table|list|math|data_visualization|statistics|search_results|product_recommendations|business_recommendations|weather|financial|instructions|comparison|api_response|error|warning|success|file_attachment|image|video|audio|progress|interactive_question|complex>",
 "content": "<1-3 sentence markdown intro — REQUIRED>",
 "components": [{{"type": "<component_type>", "data": {{...}}}}],
 "metadata": {{}}, "sources": [{{"title": "...", "url": "...", "source": "..."}}],
 "actions": [{{"label": "...", "action": "..."}}],
 "suggested_prompts": ["..."], "visualization": null, "attachments": []}}
```

Rules:
- `response_type` + `content` required; pick the most specific response_type.
- Prefer visual components over prose: numbers/KPIs -> `stat_grid`/`metric_card`/`gauge`; 2+ datapoints -> `chart`; steps -> `instructions`; compare -> `comparison`; tables -> `table`; JSON -> `json_viewer`; events -> `timeline`; highlights -> `callout`; quotes -> `quote`; weather -> `weather`; search hits -> `search_result`.
- 0 components feels empty; add one when real data exists. Never invent data.
- `suggested_prompts` only when they add value.

Component data schemas:

{comp_list}

Example:

```{RESPONSE_FENCE_TAG}
{{
  "response_type": "code",
  "content": "Here is a function that chunks text:",
  "components": [
    {{"type": "code", "data": {{"language": "python", "code": "def chunk(t, n=500):\\n    return [t[i:i+n] for i in range(0, len(t), n)]"}}}}
  ],
  "suggested_prompts": ["Explain this code", "Add unit tests"]
}}
```"""
