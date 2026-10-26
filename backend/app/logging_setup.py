"""Structured JSON logging with run correlation. Never log passwords or full prompts."""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import UTC, datetime

run_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("run_id", default=None)
request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)

_REDACT_KEYS = {"password", "token", "prompt", "cookie", "authorization"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if rid := run_id_var.get():
            data["run_id"] = rid
        if qid := request_id_var.get():
            data["request_id"] = qid
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            for k, v in extra.items():
                data[k] = "[redacted]" if k.lower() in _REDACT_KEYS else v
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def log(logger: logging.Logger, level: int, msg: str, **fields: object) -> None:
    logger.log(level, msg, extra={"fields": fields})
