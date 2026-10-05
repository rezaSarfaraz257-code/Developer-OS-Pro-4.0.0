"""Unified, bounded IDE event bus for Runner and future IDE integrations."""
from __future__ import annotations
import time
import threading
import uuid
from collections import deque

MAX_BUS_EVENTS = 4000
_MAX_FIELD_TEXT = 2000
_LOCK = threading.RLock()
_EVENTS = deque(maxlen=MAX_BUS_EVENTS)
_SEQUENCE = 0

def _clean(value):
    if isinstance(value, str):
        return value[:_MAX_FIELD_TEXT]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k)[:80]: _clean(v) for k, v in list(value.items())[:50]}
    if isinstance(value, list):
        return [_clean(v) for v in value[:50]]
    return str(value)[:_MAX_FIELD_TEXT]

def publish(event_type: str, *, source: str, workspace_id=None, trace_id=None, **data):
    global _SEQUENCE
    with _LOCK:
        _SEQUENCE += 1
        event = {
            "schema_version": "1",
            "sequence": _SEQUENCE,
            "event_id": f"evt_{uuid.uuid4().hex[:20]}",
            "timestamp": time.time(),
            "type": str(event_type)[:80],
            "source": str(source)[:80],
            "workspace_id": str(workspace_id) if workspace_id is not None else None,
            "trace_id": str(trace_id) if trace_id else None,
            "data": _clean(data),
        }
        _EVENTS.append(event)
        return dict(event)

def since(sequence: int = 0, limit: int = 100):
    limit = min(500, max(1, int(limit)))
    with _LOCK:
        return [dict(e) for e in _EVENTS if e["sequence"] > int(sequence)][-limit:]

def snapshot():
    with _LOCK:
        return {
            "schema_version": "1",
            "capacity": MAX_BUS_EVENTS,
            "size": len(_EVENTS),
            "latest_sequence": _SEQUENCE,
        }
