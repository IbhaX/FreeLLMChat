# Agent Runtime 2.0

## Runtime guarantees implemented

- **Conversation continuity:** direct Pydantic AI execution receives validated prior user/assistant turns.
- **Bounded context:** history is filtered to supported roles, limited to the newest 40 turns and a 48,000-character budget. The latest user request is kept separately.
- **Relevant memory:** SQL memory retrieval ranks recent records by lexical overlap instead of injecting an arbitrary oldest-first batch. Astra short-term recall receives the optional `conversation_id`.
- **Execution deadlines:** direct agent runs have a configurable timeout (`config.execution.timeout`, default 120 seconds, capped at 600). Tool execution has per-call timeouts.
- **Tool safety:** tool names and argument object shape are checked, unknown arguments are rejected, calculator input has complexity/numeric bounds, and outbound HTTP checks DNS results and revalidates redirects.
- **Structured output:** existing response-envelope validation remains in place.

## Agent configuration

Example:

```json
{
  "execution": {
    "timeout": 120,
    "max_retries": 5
  },
  "capabilities": {
    "tools_enabled": true,
    "tools": ["calculator", "web_search"]
  }
}
```

Pass a stable `conversation_id` in `POST /v1/agents/{agent_id}/chat` to enable conversation-scoped Astra short-term recall. The API remains backwards compatible when it is omitted.

## Operational hardening still recommended before production

1. Put outbound HTTP behind an egress proxy/firewall with private, link-local, loopback and metadata ranges denied. Application DNS checks reduce risk but cannot fully eliminate DNS-rebinding races without connection pinning.
2. Add OpenTelemetry spans and metrics export (request, provider, model, memory retrieval, tool execution, retry/fallback) with trace IDs propagated through the gateway.
3. Add durable resumable jobs and cancellation for long-running agent tasks; do not keep long workflows tied to a client HTTP connection.
4. Add explicit human approval for side-effecting tools, plus per-agent and per-user budgets.
5. Add evaluation datasets, prompt/version tracking and regression gates in CI.
6. Add MCP adapters only behind the same permission, schema-validation, timeout and audit layer as built-in tools.
7. Add multi-agent planning/collaboration only after the single-agent runtime is observable and recoverable.

## Validation

Run `make test`, `make lint` and `make typecheck` locally or in CI. This GitHub editing session cannot execute the repository's Python environment, so passing tests must be confirmed by CI before merging.
