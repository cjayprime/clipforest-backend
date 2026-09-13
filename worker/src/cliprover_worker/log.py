"""Structured JSON logging with correlation context (PRD §19.1).

Every log line carries the bound pipeline identifiers (userId, videoId, renderId,
queueName, jobId, attempt, pipelineVersion, correlationId). Secrets and signed
URLs must never be passed to the logger.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import sys
import time
from typing import Any, Iterator

_context: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar("log_context", default={})

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        payload.update(_context.get())
        for k, v in record.__dict__.items():
            if k not in _RESERVED and not k.startswith("_"):
                payload[k] = v
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("botocore", "boto3", "urllib3", "s3transfer", "httpx", "httpcore", "httpx2", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


@contextlib.contextmanager
def bind(**fields: Any) -> Iterator[None]:
    """Adds correlation fields to every log line emitted inside the block (task-local)."""
    current = dict(_context.get())
    current.update({k: v for k, v in fields.items() if v is not None})
    token = _context.set(current)
    try:
        yield
    finally:
        _context.reset(token)
