# FreeLLM Gateway

One OpenAI-compatible endpoint (`http://localhost:8000/v1`) that unifies the free
tiers of many LLM providers, with multi-key rotation, quota tracking, cooldowns,
and provider failover — so a coding agent never stalls on a rate limit.

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env          # set ADMIN_TOKEN and any provider keys you have
uvicorn app.main:app --port 8000
```

Or one command — starts the server and opens the chat UI in Chrome:

```bash
pip install -e .    # once — installs the `chat` command
chat                # or: python -m chat
```

On Termux (Android), install the opener once: `pkg install termux-api`
(and the [Termux:API](https://f-droid.org/packages/com.termux.api/) app).

On first run an `ENCRYPTION_KEY` is generated and stored in `data/.encryption_key`
(printed once to stderr). API keys are Fernet-encrypted at rest; only `key_hint`
(last 4 chars) is ever exposed.

- OpenAI-compatible API: `http://localhost:8000/v1`
- Dashboard: `http://localhost:8000/` (enter `ADMIN_TOKEN`)
- OpenAPI docs: `http://localhost:8000/docs`

With Docker:

```bash
docker compose up --build
```

## Model addressing

| Model string | Behavior |
|---|---|
| `groq/openai/gpt-oss-120b` | Explicit provider + model |
| `llama-3.3-70b-versatile` | Any provider that serves that model id |
| `auto` | Fallback chain across all healthy providers |
| `auto:coding` / `auto:fast` | Task-tuned chains (edit `providers.yaml`) |

## Connecting coding agents

Point the agent's OpenAI-compatible base URL at the gateway:

| Tool | Setting |
|---|---|
| **Cline** | Provider: OpenAI Compatible → Base URL `http://localhost:8000/v1`, API key any value (or `GATEWAY_API_KEY` if set), model `auto` |
| **Continue** | `config.json`: `"provider": "openai", "apiBase": "http://localhost:8000/v1", "model": "auto"` |
| **Aider** | `aider --openai-api-base http://localhost:8000/v1 --openai-api-key x --model openai/auto` |
| **OpenCode** | Provider `openai` with `baseURL: http://localhost:8000/v1` |
| **OpenAI SDK** | `OpenAI(base_url="http://localhost:8000/v1", api_key="x")` |

If `GATEWAY_API_KEY` is set, the agent's API key must match it.

## Adding keys

- **Startup**: set env vars (`GROQ_API_KEY`, `GEMINI_API_KEY`, …; comma-separate
  multiples) — imported automatically for providers with no keys.
- **Dashboard**: paste keys per provider at `/`.
- **API**:
  ```bash
  curl -X POST localhost:8000/admin/keys -H "X-Admin-Token: $ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"provider":"groq","keys":["gsk_a","gsk_b"],"label_prefix":"acct"}'
  ```
- **Bulk**: `POST /admin/keys/import-csv` (CSV: `provider,key,label,quota_group`),
  `POST /admin/keys/import-env` (`{"groq":"GROQ_API_KEY"}`).

`quota_group`: keys that share a server-side quota (e.g. several keys in the same
Google project) belong to one pool — cooldowns and rotation apply group-wide.

## Rotation, cooldowns, failover

- Strategies per provider: `least_used`, `round_robin`, `weighted`, `priority_failover`.
- On 429/401/403/5xx the key enters cooldown (Retry-After honored, else exponential
  backoff + jitter; auth failures cool until the provider's daily reset —
  Gemini = midnight Pacific, Cloudflare = 00:00 UTC). The request transparently
  retries on the next key, then the next provider.
- Local sliding-window RPM/RPD/TPM/TPD tracking skips keys *before* they'd 429.
- Circuit breaker per provider (opens after N failures, half-open probes).
- Streams: failures before the first token retry transparently; mid-stream
  failures emit a clean SSE error event.
- Debug headers on every response: `X-Gateway-Provider`, `X-Gateway-Key-Label`,
  `X-Gateway-Model`.

## Adding a provider

Add a block to `providers.yaml` (or `POST /admin/providers`) — no code needed if
it's OpenAI-compatible. Custom adapters live in `app/providers/`; register in
`app/providers/registry.py::ADAPTERS`.

## Getting a free key per provider

| Provider | Where | Notes |
|---|---|---|
| Gemini | aistudio.google.com → API key | Per-project limits; use `quota_group` for multi-key setups |
| Groq | console.groq.com → API keys | Generous free tier, very fast |
| OpenRouter | openrouter.ai → Keys | Only `:free` models are free (20 RPM / 50 req/day) |
| Mistral | console.mistral.ai → API keys | Free tier may require data-sharing opt-in |
| Cloudflare | dash.cloudflare.com → Workers AI + API token | Needs account ID too (`CLOUDFLARE_ACCOUNT_ID`) |
| NVIDIA | build.nvidia.com → API key | Prototyping only, not for production |
| Cohere | dashboard.cohere.com → API keys | Trial: non-commercial, ~1k calls/month |
| SambaNova | cloud.sambanova.ai → API | Very low daily cap |
| Cerebras | cloud.cerebras.ai | $5 credits / 30 days, needs verified card |

## Free-tier caveats

- Free tiers change **without notice** — check `verified_on` / `docs_url` in
  `providers.yaml` and update `limits` there (never hardcoded in code).
- Some providers (Cohere trial, NVIDIA) prohibit commercial/production use.
- Free models may be deprioritized, log prompts for training, or disappear.
- Per-*account* quotas mean extra keys don't multiply capacity — use `quota_group`.
- Don't point two gateway instances at the same keys; quota tracking is local.

## Memory (Astra DB — optional)

Set these env vars to give chats and agents vector memory on DataStax Astra DB
(no embedding model needed — uses Astra's built-in NVIDIA `$vectorize`):

```bash
ASTRA_DB_APPLICATION_TOKEN=AstraCS:...   # Database Administrator token
ASTRA_DB_API_ENDPOINT=https://<db>-<region>.apps.astra.datastax.com
ASTRA_DB_KEYSPACE=default_keyspace       # optional
```

Two collections are auto-created: `mem_short` (per-conversation turns,
recalled by similarity to the latest message) and `mem_long` (durable facts
extracted from each reply, recalled into every chat/agent/orchestrator
prompt). Without the env vars memory is simply off; a dead Astra never
breaks chat. Tune hits via `MEMORY_SHORT_LIMIT` / `MEMORY_LONG_LIMIT`.

## Development

```bash
make install   # dev deps
make test      # pytest (respx-mocked providers)
make lint      # ruff
make dev       # uvicorn --reload
alembic upgrade head   # migrations (app also auto-creates tables on boot)
```

## Layout

```
app/
  main.py config.py db.py models.py schemas.py crypto.py logging_config.py
  gateway.py                    # orchestration: resolve → pick key → call → rotate
  providers/  base.py openai_compat.py gemini.py cloudflare.py registry.py
  routing/    rotator.py quota.py circuit_breaker.py fallback.py
  api/        openai_routes.py admin_routes.py deps.py
  services/   keys.py
  static/     dashboard.html
providers.yaml  alembic/  tests/  Dockerfile  docker-compose.yml
```
