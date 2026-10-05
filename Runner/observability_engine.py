"""Developer OS IDE observability primitives."""
from __future__ import annotations
import time, threading, uuid
from collections import Counter, deque

MAX_EVENTS = 2000
_EVENTS = deque(maxlen=MAX_EVENTS)
_COUNTS = Counter()
_LOCK = threading.RLock()
_SENSITIVE_KEYS = {"authorization", "token", "secret", "password", "api_key", "apikey", "credential"}

def _safe_value(key, value):
    if str(key).lower() in _SENSITIVE_KEYS:
        return "[redacted]"
    if isinstance(value, str):
        return value[:500]
    return value

def record(event, **fields):
    name=str(event or "unknown").strip().lower().replace(" ","_")[:80]
    item={"event":name,"timestamp":time.time(),**{str(k):_safe_value(k,v) for k,v in fields.items()}}
    with _LOCK:
        _EVENTS.append(item)
        _COUNTS[name]+=1
    return item

def trace(operation, workspace_id=None, **fields):
    started=time.perf_counter()
    trace_id=f"tr_{uuid.uuid4().hex[:20]}"
    item=record("trace.start",operation=operation,workspace_id=workspace_id,trace_id=trace_id,**fields)
    return {"trace_id":trace_id,"started":started}

def finish(trace_state, status="ok", **fields):
    duration_ms=round((time.perf_counter()-trace_state["started"])*1000,2)
    return record("trace.finish",trace_id=trace_state["trace_id"],status=status,
                  duration_ms=duration_ms,**fields)

def snapshot():
    with _LOCK:
        events=list(_EVENTS)[-50:]
        counts=dict(_COUNTS)
    return {
        "status":"healthy",
        "schema_version":"2",
        "service":"developer-os-runner",
        "event_buffer":{"capacity":MAX_EVENTS,"size":len(_EVENTS)},
        "counters":counts,
        "recent_events":events,
    }
