"""Dependency-free structured logging primitives.

This module intentionally imports only the Python standard library so Django can
configure logging during settings initialization without importing the Django
application registry or ORM.
"""
from __future__ import annotations

import json
import logging
import re


_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)(bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)(\b(?:password|passwd|secret|token|api[_-]?key|access[_-]?token|refresh[_-]?token)\s*[=:]\s*)['\"]?[^\s,'\";]+"),
    re.compile(r"(?i)(\b(?:sk|rk)-[A-Za-z0-9_-]{8,})"),
)


class JsonLogFormatter(logging.Formatter):
    """Emit compact JSON logs with bounded correlation data and secret redaction."""

    @staticmethod
    def _redact(value: str) -> str:
        redacted = value
        for pattern in _SECRET_PATTERNS:
            redacted = pattern.sub(lambda match: f"{match.group(1) if match.lastindex else ''}[REDACTED]", redacted)
        return redacted

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": self._redact(record.getMessage()),
        }
        for key in ("request_id", "trace_id", "span_id"):
            value = getattr(record, key, None)
            if value:
                payload[key] = str(value)[:128]
        if record.exc_info:
            payload["exception"] = self._redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
