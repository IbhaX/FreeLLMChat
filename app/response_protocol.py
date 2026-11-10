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

import json
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
    PERSON_CARD = "person_card"
    ARCHITECTURE_DIAGRAM = "architecture_diagram"
    FLOWCHART = "flowchart"


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
        ComponentType.ARCHITECTURE_DIAGRAM.value: {
            "required": ["layers"],
            "optional": ["title", "direction", "caption"],
            "description": "A layered architecture diagram with connected service/component cards.",
        },
        ComponentType.FLOWCHART.value: {
            "required": ["nodes"],
            "optional": ["title", "edges", "direction"],
            "description": "A process flowchart with steps, decisions and labeled connections.",
        },
        ComponentType.PERSON_CARD.value: {
            "required": ["name"],
            "optional": ["description", "extract", "thumbnail", "url", "source"],
            "description": "A person profile card with photo, bio and source link.",
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


# Per-component guidance for the system prompt: when to use it and one
# minimal valid example. The catalog itself (names + schemas) is generated
# from component_schemas() so it never drifts; these maps only add the
# usage hint and example payload.
_COMPONENT_GUIDE: dict[str, dict[str, Any]] = {
    "metric_card": {"when": "ONE key number (revenue, users, score)", "example": {"title": "Revenue", "value": "1.2M", "unit": "USD", "delta": "+8%"}},
    "stat_grid": {"when": "several key metrics at a glance", "example": {"title": "Q3 KPIs", "stats": [{"label": "Users", "value": "48k", "delta": "+12%"}, {"label": "Churn", "value": "2.1%"}]}},
    "gauge": {"when": "one value against a max (progress, score, capacity)", "example": {"value": 72, "label": "Goal progress", "max": 100}},
    "sparkline": {"when": "tiny trend over a few datapoints inside prose", "example": {"data": [3, 5, 4, 8, 7, 12], "label": "Weekly signups"}},
    "chart": {"when": "2+ datapoints over time/categories (bar, line, pie, donut, area, scatter)", "example": {"chart_type": "bar", "title": "Monthly signups", "data": [{"month": "Jan", "count": 10}, {"month": "Feb", "count": 14}]}},
    "table": {"when": "comparisons or structured records with columns", "example": {"title": "Plan comparison", "columns": ["Feature", "Free", "Pro"], "rows": [["API calls", "100/day", "unlimited"], ["Support", "community", "priority"]]}},
    "comparison": {"when": "head-to-head comparison with highlighted differences", "example": {"title": "A vs B", "columns": ["Aspect", "A", "B"], "rows": [["Price", "$0", "$20/mo"], ["Speed", "fast", "faster"]]}},
    "list": {"when": "bullet or numbered items (features, pros/cons, steps as plain list)", "example": {"title": "Pros", "items": ["Fast setup", "Free tier", "Open source"]}},
    "instructions": {"when": "ordered steps the user should follow", "example": {"title": "Setup", "steps": ["Install the CLI", "Run init", "Add your key"]}},
    "timeline": {"when": "events in chronological order with dates", "example": {"title": "Roadmap", "events": [{"date": "2026 Q1", "title": "Launch", "description": "Public beta"}]}},
    "person_card": {"when": "a specific person is discussed", "example": {"name": "Ada Lovelace", "description": "Mathematician", "url": "https://en.wikipedia.org/wiki/Ada_Lovelace"}},
    "search_result": {"when": "web search hits or source list", "example": {"query": "latest AI news", "results": [{"title": "Article", "url": "https://example.com", "snippet": "..."}]}},
    "quote": {"when": "a notable quote with attribution", "example": {"text": "Simplicity is the soul of efficiency.", "author": "Austin Freeman"}},
    "callout": {"when": "warnings, tips, or key takeaways (variant: info|tip|important|warning)", "example": {"variant": "tip", "title": "Note", "message": "Free tiers change often; verify limits."}},
    "keyvalue": {"when": "spec sheets / property lists", "example": {"pairs": [{"key": "Context", "value": "128K"}, {"key": "License", "value": "Apache 2.0"}]}},
    "badge": {"when": "status labels (free, beta, deprecated) as pills", "example": {"badges": [{"label": "free tier", "variant": "success"}, {"label": "beta", "variant": "info"}]}},
    "code": {"when": "any code snippet", "example": {"language": "python", "code": "print('hi')"}},
    "math": {"when": "a mathematical formula", "example": {"expression": "E = mc^2", "display": True}},
    "json_viewer": {"when": "raw JSON/API payload the user asked to inspect", "example": {"title": "API response", "data": {"ok": True, "count": 3}}},
    "image_gallery": {"when": "multiple images to show", "example": {"images": [{"url": "https://example.com/a.png", "caption": "A"}]}},
    "video": {"when": "a single video to embed", "example": {"url": "https://example.com/v.mp4", "title": "Demo"}},
    "audio": {"when": "a single audio clip", "example": {"url": "https://example.com/a.mp3", "title": "Podcast"}},
    "file_preview": {"when": "downloadable files", "example": {"files": [{"name": "report.pdf", "url": "https://example.com/r.pdf", "mime_type": "application/pdf", "size": 1024}]}},
    "progress": {"when": "a single completion percentage", "example": {"value": 65, "label": "Processing", "status": "running"}},
    "weather": {"when": "current weather for a location", "example": {"location": "Paris", "temperature": 18, "condition": "Cloudy", "unit": "C"}},
    "financial_summary": {"when": "stock/portfolio figures with metrics", "example": {"title": "Portfolio", "currency": "USD", "metrics": [{"label": "Total", "value": "$12,400"}]}},
    "product_card": {"when": "product recommendations", "example": {"products": [{"name": "Widget Pro", "price": "$29", "description": "Fast and small"}]}},
    "business_card": {"when": "local business recommendations", "example": {"businesses": [{"name": "Cafe Luna", "rating": 4.5, "address": "1 Main St"}]}},
    "interactive": {"when": "the user must pick options before you can answer", "example": {"title": "Choose a plan", "fields": [{"name": "plan", "type": "radio", "options": ["free", "pro"]}]}},
    "error": {"when": "something failed; explain + recovery", "example": {"title": "Rate limited", "message": "Too many requests.", "recovery": "Wait 60s and retry."}},
    "warning": {"when": "a caution the user must notice", "example": {"message": "This deletes all data permanently."}},
    "success": {"when": "confirming an action completed", "example": {"message": "Settings saved."}},
    "diff": {"when": "showing before/after text changes", "example": {"title": "Rename", "before": "old_name", "after": "new_name"}},
    "heatmap": {"when": "intensity across a grid (2D numbers 0-1)", "example": {"title": "Activity", "data": [[0.1, 0.8], [0.4, 0.2]]}},
    "tagcloud": {"when": "weighted topics/keywords", "example": {"tags": [{"label": "AI", "weight": 5}, {"label": "APIs", "weight": 3}]}},
    "divider": {"when": "separating sections inside a long answer", "example": {}},
    "architecture_diagram": {"when": "system architecture, service boundaries, layers or data flow; prefer this over ASCII diagrams", "example": {"title": "Agent architecture", "layers": [{"title": "API Gateway", "subtitle": "Authentication and validation"}, {"title": "Agent Orchestrator", "nodes": [{"title": "Planner", "subtitle": "Intent and task planning"}, {"title": "Router", "subtitle": "Model selection"}]}, {"title": "Runtime", "nodes": [{"title": "Memory Engine"}, {"title": "Tool Runtime"}, {"title": "Model Router"}]}, {"title": "Persistence", "subtitle": "Database, traces and metrics"}]}},
    "flowchart": {"when": "decision trees, request lifecycles and multi-step processes with branches", "example": {"title": "Request flow", "nodes": [{"id": "start", "label": "Request", "kind": "start"}, {"id": "valid", "label": "Valid?", "kind": "decision"}, {"id": "run", "label": "Execute", "kind": "process"}, {"id": "end", "label": "Respond", "kind": "end"}], "edges": [{"from": "start", "to": "valid"}, {"from": "valid", "to": "run", "label": "Yes"}, {"from": "run", "to": "end"}] }},
}


# Response-depth rules: how thorough answers should be by default. Kept as a
# named constant so the wording can be tweaked in one place. The component
# contract below references these rules; both are injected into the system
# prompt of every structured chat call.
RESPONSE_DEPTH = """## RESPONSE DEPTH (apply to every answer)

1. Obey the user's selected response length exactly: SHORT = concise, normally 1-3 sentences; MEDIUM = a normal answer of about 2 paragraphs; LARGE = a detailed answer of 5-8 substantive paragraphs.
2. Always lead with the direct answer. Do not add filler, repeat points, or inflate a short answer.
3. Use headings, examples, trade-offs, edge cases and next steps only when they add value at the selected length.
4. Tables and visual components supplement the explanation; never let a table replace the answer.
5. For technical/design questions, prioritize actionable reasoning, trade-offs, implementation details and failure modes within the selected length.
6. The selected length controls prose depth, not whether rich components are used.
"""


def structured_system_prompt() -> str:
    """Mandatory structured-response system prompt.

    Every non-trivial reply MUST be a ``nova-response`` envelope with at
    least one component. The component catalog is generated from
    :func:`component_schemas` so it can never drift from what the frontend
    actually renders; per-type usage hints/examples live in
    ``_COMPONENT_GUIDE``.
    """
    schemas = component_schemas()
    lines = []
    for name in sorted(schemas):
        s = schemas[name]
        guide = _COMPONENT_GUIDE.get(name, {})
        when = guide.get("when", s["description"])
        ex = guide.get("example")
        ex_json = ", ".join(
            f'"{k}": {json.dumps(v, ensure_ascii=False)}' for k, v in (ex or {}).items()
        )
        lines.append(
            f"- `{name}` — {when}. required: {', '.join(s['required']) or '(none)'}."
            + (f" e.g. {{{ex_json}}}" if ex_json else "")
        )
    catalog = "\n".join(lines)
    return f"""## COMPONENT OUTPUT CONTRACT (MANDATORY — overrides any other style instruction)

{RESPONSE_DEPTH}
DEPTH + COMPONENTS — depth rules above apply WITHIN the envelope. `content` carries the conversational explanation; components carry structured, scannable or visual information. The content must still read naturally when components are hidden.

RULE 0 — UNIVERSAL REQUIREMENT: Every response from chat, every specialist agent, group agent, research synthesizer and roleplay character MUST use the `nova-response` envelope and include at least ONE relevant component. Never return a plain-text/Markdown-only answer except a transport error. For narrative or roleplay, preserve immersive prose in `content` and use a fitting component (usually `quote` for spoken dialogue, `timeline` for scene beats, or `callout` for an in-world note); do not force irrelevant charts or tables.

OUTPUT FORMAT — wrap the WHOLE reply in one fenced block tagged `{RESPONSE_FENCE_TAG}` containing ONE JSON envelope (never bare markdown, never multiple fences):

```{RESPONSE_FENCE_TAG}
{{"response_type": "<most specific type>", "content": "<direct-answer lead: 1-2 sentences, longer overview allowed for complex questions>", "components": [{{"type": "<type>", "data": {{...}}}}], "sources": [{{"title": "...", "url": "..."}}], "suggested_prompts": ["..."]}}
```

SELECTION GUIDE — pick by content, not habit:
- key metrics / KPIs -> `stat_grid` or `metric_card` (one number) or `gauge` (vs a max)
- comparisons or records -> `table` (raw data) or `comparison` (head-to-head)
- steps to follow -> `instructions`; events over time -> `timeline`
- pros/cons or features -> `list` (two lists: one "Pros", one "Cons")
- person/entity -> `person_card`; search hits / citations -> `search_result` + `sources`
- warnings/caveats -> `callout` (variant warning) or `warning`; key takeaway -> `callout` (tip)
- code -> `code`; formulas -> `math`; raw JSON -> `json_viewer`
- status labels -> `badge`; spec sheets -> `keyvalue`; quote -> `quote`
- system architecture / layered services -> `architecture_diagram`; decision logic / execution paths -> `flowchart`

COMPOSITION RULE — a good answer = direct `content` + 1 to 3 relevant components + explanation at the selected length + sources when research is used. Prefer the most useful component types; do not add decorative or irrelevant components just to increase count. For architecture questions, include one `architecture_diagram` as the primary visual; for branching workflows, use `flowchart`. Keep labels concise, never invent data, and omit unknown values.

FEW-SHOT EXAMPLES

User: "How do I deploy this app?"
```{RESPONSE_FENCE_TAG}
{{"response_type": "instructions", "content": "Three steps:", "components": [{{"type": "instructions", "data": {{"title": "Deploy", "steps": ["Build the image", "Push to registry", "Roll out"]}}}}]}}
```

User: "Compare free tiers of Groq and Gemini"
```{RESPONSE_FENCE_TAG}
{{"response_type": "comparison", "content": "Side by side:", "components": [{{"type": "comparison", "data": {{"title": "Free tiers", "columns": ["Aspect", "Groq", "Gemini"], "rows": [["RPM", "30", "15"], ["Context", "128K", "1M"]]}}}}, {{"type": "callout", "data": {{"variant": "tip", "message": "Gemini's free tier has the larger context."}}}}], "sources": [{{"title": "Groq docs", "url": "https://groq.com"}}, {{"title": "AI Studio", "url": "https://aistudio.google.com"}}]}}
```

User: "What were today's biggest AI announcements?" (research answer)
```{RESPONSE_FENCE_TAG}
{{"response_type": "search_results", "content": "Three stories stood out today:", "components": [{{"type": "search_result", "data": {{"query": "AI news today", "results": [{{"title": "Model X released", "url": "https://example.com/1", "snippet": "..."}}, {{"title": "Startup Y raises $100M", "url": "https://example.com/2", "snippet": "..."}}]}}}}, {{"type": "timeline", "data": {{"title": "Timeline", "events": [{{"date": "09:00", "title": "Model X launch", "description": "Open weights"}}]}}}}], "sources": [{{"title": "TechCrunch", "url": "https://techcrunch.com"}}]}}
```

COMPONENT CATALOG (type — when to use — required fields — minimal example):
{catalog}

OUTPUT RULES
- Never emit raw tool-call markup, XML, or JSON outside this envelope format.
- `response_type` + `content` are required; choose the most specific response_type.
- `sources` is REQUIRED whenever the answer uses web/research data; cite every factual claim with [n] matching a sources entry.
- `suggested_prompts`: 2-3 short follow-ups, only when they add value.
- If you truly cannot use components (pure one-line answer), reply with plain text — anything longer MUST use the envelope.
"""
