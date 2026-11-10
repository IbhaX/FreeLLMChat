# Nova response quality benchmark

This benchmark evaluates Nova against a consistent task set. It is designed for repeatable, blind human review and automated protocol checks. It does **not** claim parity with ChatGPT, Claude, Perplexity or Manus until measured results support that conclusion.

## Benchmark procedure

1. Run every prompt against Nova and the comparison products using the same task wording, available context, tool permissions and response-length setting.
2. Record product/model/version, date, latency, token usage, tool calls and whether sources were actually retrieved.
3. Remove product identifiers and have at least two reviewers score outputs independently.
4. Validate every Nova structured response against `/v1/render-protocol`; capture renderer errors and unsupported components.
5. Repeat each prompt at least three times for stochastic tasks. Do not compare a tool-enabled answer with a tool-disabled answer.

## Task suite

| ID | Capability | Test prompt |
|---|---|---|
| G01 | General knowledge | Explain why seasons occur. Correct the common misconception that seasons are caused by Earth's distance from the Sun. |
| G02 | Concise answer | In no more than three sentences, explain the difference between authentication and authorization. |
| C01 | Coding | Write a production-ready asynchronous FastAPI endpoint that accepts a job, validates input, queues work, returns a job ID, and exposes status. Include tests and failure handling. |
| C02 | Debugging | Diagnose an intermittent HTTP 401 caused by missing Authorization headers on reused async HTTP connections. Give a reproducible investigation and robust fix. |
| R01 | Reasoning | A service has 99.9% monthly availability. Calculate the approximate monthly downtime for a 30-day month and explain the assumptions. |
| S01 | Research | Research the current official Python support status for a specified Python release. Use primary sources, dates and citations; distinguish current facts from assumptions. |
| X01 | Comparison | Compare PostgreSQL and MongoDB for a multi-tenant SaaS application with relational reporting, flexible metadata and high write throughput. Recommend based on explicit assumptions. |
| D01 | Data visualization | Given a supplied monthly revenue dataset, calculate quarter-over-quarter growth and present a chart plus a concise interpretation. Do not invent missing data. |
| A01 | Architecture | Design a multi-agent AI platform with a gateway, orchestrator, runtime, model gateway, memory, tools, persistence and observability. Show dependencies and data flow in an architecture diagram. |
| F01 | Flow / instructions | Explain a safe deployment workflow with approval, health checks, rollback and a decision branch for failed validation. Render a flowchart. |
| P01 | Roleplay | Continue a detective scene in a restrained noir voice. Preserve the established character facts and scene continuity; do not break immersion with meta commentary. |
| L01 | Long-form | Explain how to migrate a legacy synchronous Python service to async safely, including phased rollout, compatibility, observability and rollback. |
| L02 | Length control | Run the same authentication-vs-authorization question in Short, Medium and Large modes. Verify the requested 1–3 sentences, ~2 paragraphs and 5–8 substantive paragraphs respectively. |

For S01, use a dated question and record the exact date of the run. Use a fixed dataset for D01 so every product receives identical input.

## Scoring rubric

Score each dimension from 1 (poor) to 5 (excellent).

| Dimension | What reviewers assess |
|---|---|
| Factual correctness | Claims, calculations, dates and technical details are correct |
| Task completion | The answer directly satisfies all explicit requirements |
| Depth and reasoning | Important assumptions, trade-offs, edge cases and alternatives are covered |
| Clarity and organization | Direct answer, coherent structure, readable language, no repetition |
| Source integrity | Sources are real, relevant, current and support the claims; no fabricated citations |
| Tool correctness | Tools are used only when helpful; results are accurately represented |
| Component usefulness | Components match the task and add value rather than decorate or duplicate |
| Component validity | Envelope and component data validate; every component renders without errors |
| Length compliance | Output follows the selected Short / Medium / Large definition |
| Role and style fidelity | Tone, persona, audience and conversation continuity are preserved |

## Release gates

- No fabricated citations, tool actions, data points or test claims in reviewed samples.
- All structured envelopes validate and all emitted component types have a registered renderer.
- No renderer exceptions across the task suite; malformed components degrade safely.
- At least 90% of non-trivial responses include a relevant component, with no forced charts or irrelevant widgets.
- At least 90% compliance with selected response length.
- Mean human score of at least 4/5 for factual correctness, task completion and clarity.
- For any competitor parity claim, compare blind scores by task category and publish sample size, variance and limitations. Averages must not hide a weak category.
- Re-run the suite after changes to prompts, model routing, renderer, tools or response protocol.

## Operational metrics

Track per request: selected model/provider, route (direct/tools/research/agent/roleplay), response length, time to first token, total latency, input/output tokens, tool calls, retries, component repair/fallback usage, component types emitted, validation failures and frontend render failures.

A high component rate is not a quality goal by itself. Optimize for useful, correct answers first; rich UI should make the answer easier to understand and act on.
