"""Dependency-free structured logging primitives.

This module intentionally imports only the Python standard library so Django can
configure logging during settings initialization without importing the Django
application registry or ORM.
"""
from __future__ import annotations

import json
import logging


class JsonLogFormatter(logging.Formatter):
    """Emit compact JSON logs safe for ingestion by modern log platforms."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("request_id", "trace_id", "span_id"):
            value = getattr(record, key, None)
            if value:
                payload[key] = str(value)[:128]
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
