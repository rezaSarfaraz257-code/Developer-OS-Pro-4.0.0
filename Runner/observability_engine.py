"""Thread-safe, bounded and secret-aware IDE runner telemetry.

Telemetry is diagnostic metadata, not a storage channel for source files,
credentials, request bodies, or arbitrary application objects.
"""
from __future__ import annotations

import copy
import threading
import time
import uuid
from collections import Counter, deque

MAX_EVENTS = 2_000
MAX_FIELD_TEXT = 500
MAX_NESTING = 5
MAX_COLLECTION_ITEMS = 100

_EVENTS = deque(maxlen=MAX_EVENTS)
_COUNTS = Counter()
_LOCK = threading.RLock()
_SENSITIVE_KEY_PARTS = (
    "authorization",
    "password",
    "passwd",
    "secret",
    "token",
    "credential",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
)


def _is_sensitive_key(key):
    normalized = str(key).strip().lower().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _safe_value(value, key="", depth=0):
    """Return bounded JSON-like metadata with credential-bearing fields redacted."""
    if _is_sensitive_key(key):
        return "[redacted]"
    if depth >= MAX_NESTING:
        return "[max-depth]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:MAX_FIELD_TEXT]
    if isinstance(value, dict):
        safe = {}
        for index, (child_key, child_value) in enumerate(value.items()):
            if index >= MAX_COLLECTION_ITEMS:
                safe["_truncated"] = True
                break
            name = str(child_key)[:100]
            safe[name] = _safe_value(child_value, name, depth + 1)
        return safe
    if isinstance(value, (list, tuple)):
        safe = [_safe_value(item, key, depth + 1) for item in value[:MAX_COLLECTION_ITEMS]]
        if len(value) > MAX_COLLECTION_ITEMS:
            safe.append("[truncated]")
        return safe
    # Do not call repr() on arbitrary objects: it may contain secrets or huge data.
    return f"[{type(value).__name__}]"


def record(event, **fields):
    name = str(event or "unknown").strip().lower().replace(" ", "_")[:80]
    item = {
        "event": name,
        "timestamp": time.time(),
        **{str(key)[:100]: _safe_value(value, str(key)) for key, value in fields.items()},
    }
    with _LOCK:
        _EVENTS.append(item)
        _COUNTS[name] += 1
        return copy.deepcopy(item)


def trace(operation, workspace_id=None, **fields):
    started = time.perf_counter()
    trace_id = f"tr_{uuid.uuid4().hex}"
    record(
        "trace.start",
        operation=operation,
        workspace_id=workspace_id,
        trace_id=trace_id,
        **fields,
    )
    return {"trace_id": trace_id, "started": started}


def finish(trace_state, status="ok", **fields):
    duration_ms = round((time.perf_counter() - trace_state["started"]) * 1000, 2)
    return record(
        "trace.finish",
        trace_id=trace_state["trace_id"],
        status=status,
        duration_ms=duration_ms,
        **fields,
    )


def metric_duration(operation, duration_ms, status="ok", workspace_id=None):
    return record(
        "metric.duration",
        operation=str(operation)[:80],
        duration_ms=round(float(duration_ms), 2),
        status=str(status)[:40],
        workspace_id=workspace_id,
    )


def snapshot():
    with _LOCK:
        events = copy.deepcopy(list(_EVENTS)[-50:])
        counts = dict(_COUNTS)
        size = len(_EVENTS)
    return {
        "status": "healthy",
        "schema_version": "2",
        "event_buffer": {"capacity": MAX_EVENTS, "size": size},
        "counters": counts,
        "recent_events": events,
    }
