"""Structured JSON logging with API-key redaction."""

from __future__ import annotations

import json
import logging
import re
import sys
import uuid
from contextvars import ContextVar
from typing import Any

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")

_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|AIza[0-9A-Za-z_\-]{20,}|[A-Za-z0-9_\-]{32,})")


def redact(text: str) -> str:
    return _SECRET_RE.sub("***", text)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
            "request_id": getattr(record, "request_id", request_id_ctx.get()),
        }
        for key in ('tool_name', 'duration_ms', 'success'):
            if key in record.__dict__:
                value = record.__dict__[key]
                if isinstance(value, (str, int, float, bool)):
                    payload[key] = redact(value) if isinstance(value, str) else value
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload)


class TerminalFormatter(JsonFormatter):
    """Human-readable line built from the redacted JSON payload."""

    def format(self, record: logging.LogRecord) -> str:
        payload = json.loads(super().format(record))
        fields: list[str] = []
        if "tool_name" in payload:
            fields.append(f"tool={payload['tool_name']}")
        if "duration_ms" in payload:
            fields.append(f"duration={payload['duration_ms']}ms")
        if "success" in payload:
            fields.append("outcome=" + ("ok" if payload["success"] else "failed"))
        if payload.get("request_id") not in (None, "-"):
            fields.append(f"request={payload['request_id']}")
        line = f"[{payload['logger']}] {payload['msg']}"
        if fields:
            line += "  " + " ".join(fields)
        if "exc" in payload:
            line += "\n" + payload["exc"]
        return line


def setup_logging(level: str = "INFO") -> None:
    if sys.stderr.isatty():
        from rich.console import Console
        from rich.logging import RichHandler

        handler: logging.Handler = RichHandler(
            console=Console(stderr=True), markup=False, show_path=False)
        handler.setFormatter(TerminalFormatter())
    else:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def new_request_id() -> str:
    rid = uuid.uuid4().hex[:16]
    request_id_ctx.set(rid)
    return rid
