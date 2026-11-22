"""User-specific connectors — per-account credentials for external services.

Each connector is a named spec (fields + docs). Users store their own
credentials via ``PUT /v1/connectors/<service>``; the whole config blob is
Fernet-encrypted at rest. Connector-backed tools resolve the *calling* user's
row at run time through the artifact/request contextvar (``owner_id``), so
one tenant's credentials can never leak into another user's run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from app.crypto import decrypt_key, encrypt_key, key_hint

_HTTP_TIMEOUT = 20.0


@dataclass(frozen=True)
class ConnectorField:
    key: str
    label: str
    secret: bool = True
    required: bool = True
    placeholder: str = ""
    default: str = ""


@dataclass(frozen=True)
class ConnectorSpec:
    name: str
    label: str
    icon: str
    description: str
    docs: str
    fields: tuple[ConnectorField, ...]


SPECS: tuple[ConnectorSpec, ...] = (
    ConnectorSpec(
        "github", "GitHub", "🐙",
        "Repos, issues and code search with your personal access token.",
        "github.com → Settings → Developer settings → Personal access tokens",
        (ConnectorField("token", "Personal access token",
                        placeholder="ghp_… / github_pat_…"),)),
    ConnectorSpec(
        "slack", "Slack", "💬",
        "Post messages and list channels with your bot token.",
        "api.slack.com/apps → your app → OAuth & Permissions → Bot User OAuth Token",
        (ConnectorField("token", "Bot token", placeholder="xoxb-…"),)),
    ConnectorSpec(
        "notion", "Notion", "📝",
        "Search and read pages with your internal integration token.",
        "notion.so/my-integrations → New integration → Internal Integration Secret",
        (ConnectorField("token", "Integration token", placeholder="ntn_… / secret_…"),)),
    ConnectorSpec(
        "linear", "Linear", "📐",
        "List and create issues with your personal API key.",
        "linear.app → Settings → API → Personal API keys",
        (ConnectorField("api_key", "API key", placeholder="lin_api_…"),)),
    ConnectorSpec(
        "jira", "Jira", "🗂️",
        "Search and create issues on your Atlassian site.",
        "id.atlassian.com → Security → API tokens → Create API token",
        (ConnectorField("domain", "Site domain", secret=False,
                        placeholder="yourteam.atlassian.net"),
         ConnectorField("email", "Account email", secret=False,
                        placeholder="you@company.com"),
         ConnectorField("api_token", "API token"),)),
    ConnectorSpec(
        "telegram", "Telegram", "✈️",
        "Send messages to a chat via your bot.",
        "@BotFather → /newbot for the token; message the bot then read chat_id from getUpdates",
        (ConnectorField("bot_token", "Bot token", placeholder="123456:ABC-…"),
         ConnectorField("chat_id", "Chat ID", secret=False, placeholder="123456789"),)),
    ConnectorSpec(
        "discord", "Discord", "🎮",
        "Post to a channel through a webhook URL.",
        "Channel settings → Integrations → Webhooks → New Webhook → Copy URL",
        (ConnectorField("webhook_url", "Webhook URL",
                        placeholder="https://discord.com/api/webhooks/…"),)),
    ConnectorSpec(
        "smtp", "Email (SMTP)", "📧",
        "Send email through your own SMTP account or app password.",
        "e.g. Gmail: myaccount.google.com/apppasswords (16-char app password)",
        (ConnectorField("host", "SMTP host", secret=False,
                        placeholder="smtp.gmail.com"),
         ConnectorField("port", "Port", secret=False, required=False,
                        default="587", placeholder="587"),
         ConnectorField("username", "Username / email", secret=False),
         ConnectorField("password", "Password / app password"),)),
    ConnectorSpec(
        "database", "Database", "🗄️",
        "Run read-only SQL against your own Postgres or SQLite database.",
        "e.g. postgresql://user:pass@host/db or sqlite:///path/to.db",
        (ConnectorField("url", "Connection URL",
                        placeholder="postgresql://… / sqlite:///…"),)),
    ConnectorSpec(
        "webhook", "Generic webhook", "🪝",
        "POST JSON to any URL you control (IFTTT, Zapier, internal APIs…).",
        "Any HTTPS endpoint that accepts a JSON POST",
        (ConnectorField("url", "Webhook URL", placeholder="https://…"),
         ConnectorField("headers", "Extra headers (JSON)",
                        required=False, placeholder='{"Authorization":"Bearer …"}'),)),
    ConnectorSpec(
        "tavily", "Tavily", "🔎",
        "AI-optimized web search with your Tavily API key.",
        "tavily.com → API keys",
        (ConnectorField("api_key", "API key", placeholder="tvly-…"),)),
)

SPEC_BY_NAME: dict[str, ConnectorSpec] = {s.name: s for s in SPECS}


def spec_dicts() -> list[dict[str, Any]]:
    """Public spec view — field metadata only, never user values."""
    return [
        {
            "name": s.name, "label": s.label, "icon": s.icon,
            "description": s.description, "docs": s.docs,
            "fields": [
                {"key": f.key, "label": f.label, "secret": f.secret,
                 "required": f.required, "placeholder": f.placeholder,
                 "default": f.default}
                for f in s.fields
            ],
        }
        for s in SPECS
    ]


# ---------- credential store ----------

async def save(user_id: int, service: str, values: dict[str, Any]) -> dict[str, Any]:
    """Validate + encrypt + upsert one connector for ``user_id``."""
    from sqlalchemy import select

    from app.db import get_session_factory
    from app.models import UserConnector

    spec = SPEC_BY_NAME.get(service)
    if spec is None:
        raise ValueError(f"unknown connector '{service}'")
    cfg: dict[str, str] = {}
    for f in spec.fields:
        v = str(values.get(f.key) or "").strip() or f.default
        if f.required and not v:
            raise ValueError(f"'{f.label}' is required")
        if v:
            cfg[f.key] = v
    blob = encrypt_key(json.dumps(cfg))
    async with get_session_factory()() as s:
        row = (await s.execute(
            select(UserConnector).where(
                UserConnector.user_id == user_id,
                UserConnector.service == service))
        ).scalars().first()
        if row is None:
            s.add(UserConnector(user_id=user_id, service=service,
                                config_enc=blob))
        else:
            row.config_enc = blob
        await s.commit()
    return cfg


async def remove(user_id: int, service: str) -> bool:
    from sqlalchemy import delete

    from app.db import get_session_factory
    from app.models import UserConnector

    async with get_session_factory()() as s:
        res = await s.execute(
            delete(UserConnector).where(
                UserConnector.user_id == user_id,
                UserConnector.service == service))
        await s.commit()
        return bool(res.rowcount)


async def configured(user_id: int) -> dict[str, Any]:
    """Field hints + timestamps for the user's connected services —
    raw secret values never leave this layer."""
    from sqlalchemy import select

    from app.db import get_session_factory
    from app.models import UserConnector

    async with get_session_factory()() as s:
        rows = (await s.execute(
            select(UserConnector).where(UserConnector.user_id == user_id))
        ).scalars().all()
    out: dict[str, Any] = {}
    for r in rows:
        try:
            cfg = json.loads(decrypt_key(r.config_enc))
        except Exception:
            cfg = {}
        out[r.service] = {
            "connected": bool(cfg),
            "fields": {
                k: ("…" + key_hint(v) if any(f.key == k and f.secret
                                             for f in SPEC_BY_NAME[r.service].fields)
                    else v)
                for k, v in cfg.items()
            } if r.service in SPEC_BY_NAME else {},
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
    return out


async def config_for_user(user_id: int, service: str) -> dict[str, str] | None:
    """Decrypted config for an explicit user — used by the Test endpoint."""
    from sqlalchemy import select

    from app.db import get_session_factory
    from app.models import UserConnector

    async with get_session_factory()() as s:
        row = (await s.execute(
            select(UserConnector).where(
                UserConnector.user_id == user_id,
                UserConnector.service == service))
        ).scalars().first()
    if row is None:
        return None
    try:
        return json.loads(decrypt_key(row.config_enc))
    except Exception:
        return None


async def _config_for(service: str) -> dict[str, str] | None:
    """Decrypted config for the *calling* user (request contextvar)."""
    from app.services.artifacts import current_context

    uid = current_context().get("owner_id")
    if uid is None:
        return None
    return await config_for_user(uid, service)


async def _need(service: str) -> dict[str, str] | str:
    """Resolve credentials or return a user-facing error string."""
    spec = SPEC_BY_NAME[service]
    cfg = await _config_for(service)
    if cfg is None:
        return (f"error: {spec.label} is not connected — add your credentials "
                f"on the Connectors page (/connectors). Connector credentials "
                f"are per-user, so connect it under your own account.")
    return cfg


# ---------- connection probes ----------

async def probe(service: str, cfg: dict[str, str]) -> str:
    """Lightweight live check used by the Test button. Returns a short
    human string ('ok — user: octocat') or raises with a useful error."""
    if service == "github":
        r = await httpx.AsyncClient(timeout=_HTTP_TIMEOUT).get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {cfg['token']}",
                     "Accept": "application/vnd.github+json"})
        r.raise_for_status()
        return f"ok — logged in as {r.json().get('login')}"
    if service == "slack":
        r = await httpx.AsyncClient(timeout=_HTTP_TIMEOUT).post(
            "https://slack.com/api/auth.test",
            headers={"Authorization": f"Bearer {cfg['token']}"})
        j = r.json()
        if not j.get("ok"):
            raise ValueError(j.get("error", "auth.test failed"))
        return f"ok — team {j.get('team')} as {j.get('user')}"
    if service == "notion":
        r = await httpx.AsyncClient(timeout=_HTTP_TIMEOUT).get(
            "https://api.notion.com/v1/users/me",
            headers={"Authorization": f"Bearer {cfg['token']}",
                     "Notion-Version": "2022-06-28"})
        r.raise_for_status()
        return f"ok — bot {r.json().get('name')}"
    if service == "linear":
        r = await httpx.AsyncClient(timeout=_HTTP_TIMEOUT).post(
            "https://api.linear.app/graphql",
            headers={"Authorization": cfg["api_key"]},
            json={"query": "{ viewer { name email } }"})
        r.raise_for_status()
        v = r.json().get("data", {}).get("viewer") or {}
        if not v:
            raise ValueError(str(r.json())[:200])
        return f"ok — {v.get('name')} ({v.get('email')})"
    if service == "jira":
        r = await httpx.AsyncClient(timeout=_HTTP_TIMEOUT).get(
            f"https://{cfg['domain']}/rest/api/3/myself",
            auth=(cfg["email"], cfg["api_token"]))
        r.raise_for_status()
        return f"ok — {r.json().get('displayName')}"
    if service == "telegram":
        r = await httpx.AsyncClient(timeout=_HTTP_TIMEOUT).get(
            f"https://api.telegram.org/bot{cfg['bot_token']}/getMe")
        j = r.json()
        if not j.get("ok"):
            raise ValueError(j.get("description", "getMe failed"))
        return f"ok — @{j['result'].get('username')}"
    if service == "discord":
        r = await httpx.AsyncClient(timeout=_HTTP_TIMEOUT).get(cfg["webhook_url"])
        r.raise_for_status()
        return f"ok — webhook {r.json().get('name', '?')}"
    if service == "smtp":
        import aiosmtplib

        smtp = aiosmtplib.SMTP(hostname=cfg["host"],
                               port=int(cfg.get("port") or 587),
                               start_tls=True, timeout=15)
        await smtp.connect()
        try:
            await smtp.login(cfg["username"], cfg["password"])
        finally:
            await smtp.quit()
        return f"ok — {cfg['username']}@{cfg['host']}"
    if service == "database":
        await _db_run(cfg["url"], "SELECT 1")
        return "ok — query succeeded"
    if service == "webhook":
        r = await httpx.AsyncClient(timeout=_HTTP_TIMEOUT).get(cfg["url"])
        return f"ok — endpoint responded {r.status_code}"
    if service == "tavily":
        r = await httpx.AsyncClient(timeout=_HTTP_TIMEOUT).post(
            "https://api.tavily.com/search",
            json={"api_key": cfg["api_key"], "query": "ping",
                  "max_results": 1})
        r.raise_for_status()
        return "ok — key accepted"
    return "ok — saved"


# ---------- database connector ----------

async def _db_run(url: str, sql: str) -> list[dict[str, Any]]:
    """Run a read-only query against sqlite/postgres URLs."""
    if url.startswith("sqlite:///"):
        import aiosqlite

        async with aiosqlite.connect(url[len("sqlite:///"):]) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(sql)
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
    if url.startswith(("postgres://", "postgresql://")):
        import asyncpg

        conn = await asyncpg.connect(url)
        try:
            rows = await conn.fetch(sql)
            return [dict(r) for r in rows]
        finally:
            await conn.close()
    raise ValueError("unsupported database URL — use postgresql:// or sqlite:///")


_READ_ONLY_SQL = ("select", "with", "explain", "pragma", "show")


# ---------- tool implementations ----------

def _fmt_rows(rows: list[dict[str, Any]], limit: int = 50) -> str:
    if not rows:
        return "(no rows)"
    head = rows[:limit]
    cols = list(head[0].keys())
    lines = ["\t".join(cols)]
    lines += ["\t".join(str(r.get(c)) for c in cols) for r in head]
    if len(rows) > limit:
        lines.append(f"…({len(rows) - limit} more)")
    return "\n".join(lines)


async def _github_repos(query: str = "", limit: int = 10) -> str:
    cfg = await _need("github")
    if isinstance(cfg, str):
        return cfg
    h = {"Authorization": f"Bearer {cfg['token']}",
         "Accept": "application/vnd.github+json"}
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
        if query.strip():
            r = await c.get("https://api.github.com/search/repositories",
                            headers=h, params={"q": query, "per_page": min(limit, 30)})
            items = r.json().get("items", [])
        else:
            r = await c.get("https://api.github.com/user/repos", headers=h,
                            params={"per_page": min(limit, 50),
                                    "sort": "updated"})
            items = r.json()
        if r.status_code != 200:
            return f"error: github {r.status_code}: {r.text[:200]}"
    return "\n".join(
        f"{i['full_name']} ★{i.get('stargazers_count', 0)} "
        f"— {(i.get('description') or '')[:80]} ({i.get('html_url')})"
        for i in items) or "(no repos)"


async def _github_repo(repo: str) -> str:
    cfg = await _need("github")
    if isinstance(cfg, str):
        return cfg
    h = {"Authorization": f"Bearer {cfg['token']}",
         "Accept": "application/vnd.github+json"}
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
        r = await c.get(f"https://api.github.com/repos/{repo}", headers=h)
        if r.status_code != 200:
            return f"error: {r.status_code} {r.text[:200]}"
        d = r.json()
        issues = await c.get(
            f"https://api.github.com/repos/{repo}/issues",
            headers=h, params={"state": "open", "per_page": 10})
        iss = [f"#{i['number']} {i['title']}" for i in issues.json()
               if "pull_request" not in i]
    return (f"{d['full_name']} ({d.get('language') or '?'}, "
            f"★{d.get('stargazers_count', 0)}, forks {d.get('forks_count', 0)})\n"
            f"{d.get('description') or ''}\n"
            f"default branch: {d.get('default_branch')} · "
            f"open issues: {d.get('open_issues_count', 0)}\n"
            + ("\n".join(iss) if iss else "no open issues"))


async def _github_create_issue(repo: str, title: str, body: str = "") -> str:
    cfg = await _need("github")
    if isinstance(cfg, str):
        return cfg
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
        r = await c.post(
            f"https://api.github.com/repos/{repo}/issues",
            headers={"Authorization": f"Bearer {cfg['token']}",
                     "Accept": "application/vnd.github+json"},
            json={"title": title, "body": body})
    if r.status_code not in (200, 201):
        return f"error: {r.status_code} {r.text[:200]}"
    d = r.json()
    return f"created {d.get('html_url')} (#{d.get('number')})"


async def _slack_channels(limit: int = 50) -> str:
    cfg = await _need("slack")
    if isinstance(cfg, str):
        return cfg
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
        r = await c.get("https://slack.com/api/conversations.list",
                        headers={"Authorization": f"Bearer {cfg['token']}"},
                        params={"limit": min(limit, 100)})
    j = r.json()
    if not j.get("ok"):
        return f"error: {j.get('error')}"
    return "\n".join(f"#{ch['name']} ({ch['id']}) — {ch.get('num_members',0)} members"
                     for ch in j.get("channels", [])) or "(no channels)"


async def _slack_post(channel: str, text: str) -> str:
    cfg = await _need("slack")
    if isinstance(cfg, str):
        return cfg
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
        r = await c.post("https://slack.com/api/chat.postMessage",
                         headers={"Authorization": f"Bearer {cfg['token']}"},
                         json={"channel": channel.lstrip("#"), "text": text})
    j = r.json()
    if not j.get("ok"):
        return f"error: {j.get('error')} — use a channel id/name the bot can see"
    return f"posted to #{channel.lstrip('#')} (ts {j.get('ts')})"


async def _notion_search(query: str = "", limit: int = 10) -> str:
    cfg = await _need("notion")
    if isinstance(cfg, str):
        return cfg
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
        r = await c.post("https://api.notion.com/v1/search",
                         headers={"Authorization": f"Bearer {cfg['token']}",
                                  "Notion-Version": "2022-06-28"},
                         json={"query": query, "page_size": min(limit, 50)})
    if r.status_code != 200:
        return f"error: {r.status_code} {r.text[:200]}"
    out = []
    for it in r.json().get("results", []):
        title = ""
        for prop in (it.get("properties") or {}).values():
            if prop.get("type") == "title" and prop.get("title"):
                title = "".join(t.get("plain_text", "") for t in prop["title"])
        if not title and it.get("object") == "database":
            title = "".join(t.get("plain_text", "")
                            for t in it.get("title", []))
        out.append(f"[{it.get('object')}] {title or '(untitled)'} — {it.get('id')}")
    return "\n".join(out) or "(nothing found)"


async def _notion_page(page_id: str) -> str:
    cfg = await _need("notion")
    if isinstance(cfg, str):
        return cfg
    pid = page_id.rsplit("/", 1)[-1].rsplit("-", 1)[-1]
    h = {"Authorization": f"Bearer {cfg['token']}",
         "Notion-Version": "2022-06-28"}
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
        r = await c.get(f"https://api.notion.com/v1/pages/{pid}", headers=h)
        if r.status_code != 200:
            return f"error: {r.status_code} {r.text[:200]}"
        blocks = await c.get(
            f"https://api.notion.com/v1/blocks/{pid}/children",
            headers=h, params={"page_size": 30})
    texts = []
    for b in blocks.json().get("results", []):
        t = b.get(b.get("type", ""), {})
        rich = t.get("rich_text") if isinstance(t, dict) else None
        if rich:
            texts.append("".join(x.get("plain_text", "") for x in rich))
    d = r.json()
    return f"page {d.get('url')}\n\n" + "\n".join(texts)[:3000]


async def _linear_issues(query: str = "", limit: int = 20) -> str:
    cfg = await _need("linear")
    if isinstance(cfg, str):
        return cfg
    gql = ("{ issues(first: %d%s) { nodes { identifier title state { name } "
           "url } } }") % (
        min(limit, 50),
        f', filter: {{ title: {{ containsIgnoreCase: "{query}" }} }}'
        if query.strip() else "")
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
        r = await c.post("https://api.linear.app/graphql",
                         headers={"Authorization": cfg["api_key"]},
                         json={"query": gql})
    nodes = r.json().get("data", {}).get("issues", {}).get("nodes", [])
    return "\n".join(
        f"{i['identifier']} [{i['state']['name']}] {i['title']} — {i['url']}"
        for i in nodes) or "(no issues)"


async def _linear_create_issue(title: str, description: str = "",
                               team: str = "") -> str:
    cfg = await _need("linear")
    if isinstance(cfg, str):
        return cfg
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
        tr = await c.post("https://api.linear.app/graphql",
                          headers={"Authorization": cfg["api_key"]},
                          json={"query": "{ teams { nodes { id name key } } }"})
        teams = tr.json().get("data", {}).get("teams", {}).get("nodes", [])
        tid = next((t["id"] for t in teams
                    if team.lower() in (t["name"].lower(), t["key"].lower())),
                   teams[0]["id"] if teams else None)
        if not tid:
            return "error: no Linear team found"
        r = await c.post(
            "https://api.linear.app/graphql",
            headers={"Authorization": cfg["api_key"]},
            json={"query": "mutation($i: IssueCreateInput!){ issueCreate(input:$i){ success issue{ identifier url } } }",
                  "variables": {"i": {"title": title, "description": description,
                                      "teamId": tid}}})
    d = r.json().get("data", {}).get("issueCreate", {})
    if not d.get("success"):
        return f"error: {json.dumps(r.json())[:200]}"
    return f"created {d['issue']['identifier']} — {d['issue']['url']}"


async def _jira_search(jql: str = "", limit: int = 20) -> str:
    cfg = await _need("jira")
    if isinstance(cfg, str):
        return cfg
    q = jql.strip() or "order by updated DESC"
    if not q.lower().startswith(("order", "project", "assignee", "status",
                                 "type", "reporter", "text", "key", "label",
                                 "priority", "sprint", "resolution", "created",
                                 "updated", "component", "fixversion",
                                 "description", "summary", "issuetype")):
        q = f'text ~ "{q}" order by updated DESC'
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
        r = await c.get(
            f"https://{cfg['domain']}/rest/api/3/search",
            auth=(cfg["email"], cfg["api_token"]),
            params={"jql": q, "maxResults": min(limit, 50),
                    "fields": "summary,status,priority"})
    if r.status_code != 200:
        return f"error: {r.status_code} {r.text[:200]}"
    base = f"https://{cfg['domain']}/browse/"
    return "\n".join(
        f"{i['key']} [{i['fields']['status']['name']}] "
        f"{i['fields']['summary']} — {base}{i['key']}"
        for i in r.json().get("issues", [])) or "(no issues)"


async def _jira_create_issue(project: str, summary: str,
                             description: str = "",
                             issue_type: str = "Task") -> str:
    cfg = await _need("jira")
    if isinstance(cfg, str):
        return cfg
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
        r = await c.post(
            f"https://{cfg['domain']}/rest/api/3/issue",
            auth=(cfg["email"], cfg["api_token"]),
            json={"fields": {
                "project": {"key": project.upper()},
                "summary": summary,
                "issuetype": {"name": issue_type},
                "description": {"type": "doc", "version": 1, "content": [
                    {"type": "paragraph", "content": [
                        {"type": "text", "text": description or summary}]}]},
            }})
    if r.status_code not in (200, 201):
        return f"error: {r.status_code} {r.text[:300]}"
    key = r.json().get("key")
    return f"created {key} — https://{cfg['domain']}/browse/{key}"


async def _telegram_send(text: str) -> str:
    cfg = await _need("telegram")
    if isinstance(cfg, str):
        return cfg
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
        r = await c.post(
            f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage",
            json={"chat_id": cfg["chat_id"], "text": text})
    j = r.json()
    if not j.get("ok"):
        return f"error: {j.get('description')}"
    return f"sent message {j['result'].get('message_id')}"


async def _discord_send(text: str) -> str:
    cfg = await _need("discord")
    if isinstance(cfg, str):
        return cfg
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
        r = await c.post(cfg["webhook_url"], json={"content": text[:2000]})
    if r.status_code not in (200, 204):
        return f"error: {r.status_code} {r.text[:200]}"
    return "sent to discord"


async def _send_email(to: str, subject: str, body: str) -> str:
    cfg = await _need("smtp")
    if isinstance(cfg, str):
        return cfg
    import aiosmtplib
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"] = cfg["username"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        await aiosmtplib.send(
            msg, hostname=cfg["host"], port=int(cfg.get("port") or 587),
            username=cfg["username"], password=cfg["password"],
            start_tls=True, timeout=30)
    except Exception as exc:
        return f"error: smtp send failed: {exc}"
    return f"sent '{subject}' to {to}"


async def _db_query(sql: str) -> str:
    cfg = await _need("database")
    if isinstance(cfg, str):
        return cfg
    cleaned = sql.strip().rstrip(";")
    if not cleaned.lower().startswith(_READ_ONLY_SQL):
        return ("error: read-only connector — only SELECT/WITH/EXPLAIN/"
                "PRAGMA/SHOW queries are allowed")
    try:
        rows = await _db_run(cfg["url"], cleaned)
    except Exception as exc:
        return f"error: query failed: {exc}"
    out = _fmt_rows(rows)
    return out[:4000] + ("\n…(truncated)" if len(out) > 4000 else "")


async def _webhook_post(payload: str) -> str:
    cfg = await _need("webhook")
    if isinstance(cfg, str):
        return cfg
    headers: dict[str, str] = {}
    if cfg.get("headers"):
        try:
            headers = json.loads(cfg["headers"])
        except json.JSONDecodeError:
            pass
    try:
        body = json.loads(payload)
    except json.JSONDecodeError:
        body = {"text": payload}
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
        r = await c.post(cfg["url"], json=body, headers=headers)
    return f"POST {cfg['url']} → {r.status_code} {r.text[:200]}"


async def _tavily_search(query: str, limit: int = 5) -> str:
    cfg = await _need("tavily")
    if isinstance(cfg, str):
        return cfg
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as c:
        r = await c.post("https://api.tavily.com/search",
                         json={"api_key": cfg["api_key"], "query": query,
                               "max_results": min(limit, 10)})
    if r.status_code != 200:
        return f"error: {r.status_code} {r.text[:200]}"
    return "\n".join(
        f"{x.get('title')} — {x.get('url')}\n  {(x.get('content') or '')[:160]}"
        for x in r.json().get("results", [])) or "(no results)"


def tool_specs() -> dict[str, Any]:
    """Connector-backed tools. Registered into the global TOOLS map by
    tools.py; each resolves the calling user's credentials at run time and
    returns a connect-now hint when the connector isn't configured."""
    from app.services.tools import Tool

    def p(props: dict[str, Any], req: list[str]) -> dict[str, Any]:
        return {"type": "object", "properties": props, "required": req}

    def s(desc: str, t: str = "string") -> dict[str, Any]:
        return {"type": t, "description": desc}

    return {
        "github_repos": Tool(
            "github_repos",
            "List your GitHub repos, or search GitHub with a query. Requires the GitHub connector.",
            p({"query": s("optional search query"), "limit": s("max results", "integer")}, []),
            _github_repos, "🐙"),
        "github_repo": Tool(
            "github_repo",
            "Details + open issues for a GitHub repo ('owner/name'). Requires the GitHub connector.",
            p({"repo": s("full repo name, e.g. 'octocat/hello-world'")}, ["repo"]),
            _github_repo, "🐙"),
        "github_create_issue": Tool(
            "github_create_issue",
            "Create a GitHub issue in a repo you can write to. Requires the GitHub connector.",
            p({"repo": s("'owner/name'"), "title": s("issue title"),
               "body": s("issue body")}, ["repo", "title"]),
            _github_create_issue, "🐙"),
        "slack_channels": Tool(
            "slack_channels",
            "List Slack channels visible to your bot. Requires the Slack connector.",
            p({"limit": s("max channels", "integer")}, []),
            _slack_channels, "💬"),
        "slack_post": Tool(
            "slack_post",
            "Post a message to a Slack channel. Requires the Slack connector.",
            p({"channel": s("channel name or id"), "text": s("message text")},
              ["channel", "text"]),
            _slack_post, "💬"),
        "notion_search": Tool(
            "notion_search",
            "Search your Notion workspace for pages/databases. Requires the Notion connector.",
            p({"query": s("search query"), "limit": s("max results", "integer")}, []),
            _notion_search, "📝"),
        "notion_page": Tool(
            "notion_page",
            "Read a Notion page's content by id or URL. Requires the Notion connector.",
            p({"page_id": s("Notion page id or URL")}, ["page_id"]),
            _notion_page, "📝"),
        "linear_issues": Tool(
            "linear_issues",
            "List Linear issues, optionally filtered by title. Requires the Linear connector.",
            p({"query": s("title filter"), "limit": s("max issues", "integer")}, []),
            _linear_issues, "📐"),
        "linear_create_issue": Tool(
            "linear_create_issue",
            "Create a Linear issue. Requires the Linear connector.",
            p({"title": s("issue title"), "description": s("description"),
               "team": s("team name or key (first team if empty)")}, ["title"]),
            _linear_create_issue, "📐"),
        "jira_search": Tool(
            "jira_search",
            "Search Jira issues with JQL or plain text. Requires the Jira connector.",
            p({"jql": s("JQL query or plain text"),
               "limit": s("max issues", "integer")}, []),
            _jira_search, "🗂️"),
        "jira_create_issue": Tool(
            "jira_create_issue",
            "Create a Jira issue. Requires the Jira connector.",
            p({"project": s("project key, e.g. 'ENG'"), "summary": s("issue summary"),
               "description": s("description"),
               "issue_type": s("issue type (Task, Bug, Story…)")}, ["project", "summary"]),
            _jira_create_issue, "🗂️"),
        "telegram_send": Tool(
            "telegram_send",
            "Send a Telegram message to your configured chat. Requires the Telegram connector.",
            p({"text": s("message text")}, ["text"]),
            _telegram_send, "✈️"),
        "discord_send": Tool(
            "discord_send",
            "Post a message to your Discord webhook. Requires the Discord connector.",
            p({"text": s("message text")}, ["text"]),
            _discord_send, "🎮"),
        "send_email": Tool(
            "send_email",
            "Send an email via your configured SMTP account. Requires the Email connector.",
            p({"to": s("recipient address"), "subject": s("subject line"),
               "body": s("email body")}, ["to", "subject", "body"]),
            _send_email, "📧"),
        "db_query": Tool(
            "db_query",
            "Run a read-only SQL query (SELECT/WITH/EXPLAIN) against your connected Postgres or SQLite database. Requires the Database connector.",
            p({"sql": s("a single read-only SQL statement")}, ["sql"]),
            _db_query, "🗄️"),
        "webhook_post": Tool(
            "webhook_post",
            "POST JSON to your configured webhook endpoint. Requires the Webhook connector.",
            p({"payload": s("JSON string or plain text")}, ["payload"]),
            _webhook_post, "🪝"),
        "tavily_search": Tool(
            "tavily_search",
            "AI-optimized web search via your Tavily key. Requires the Tavily connector.",
            p({"query": s("search query"), "limit": s("max results", "integer")},
              ["query"]),
            _tavily_search, "🔎"),
    }
