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

def metric_duration(operation, duration_ms, status="ok", workspace_id=None):
    return record("metric.duration", operation=str(operation)[:80], duration_ms=round(float(duration_ms), 2),
                  status=str(status)[:40], workspace_id=workspace_id)

HEALTH_WINDOW_SECONDS = 300
SLO_TARGETS = {"sync_conflict_rate": 0.05, "reconnect_rate": 0.20, "operation_latency_ms": 1500.0}
_CIRCUIT = {"state": "closed", "opened_at": 0.0, "failures": 0}
_CIRCUIT_LOCK = threading.RLock()

def health_score():
    now = time.time()
    with _LOCK:
        recent = [e for e in _EVENTS if now - float(e.get("timestamp", now)) <= HEALTH_WINDOW_SECONDS]
    syncs = sum(e.get("event") == "collab.sync" for e in recent)
    conflicts = sum(e.get("event") == "collab.conflict" for e in recent)
    reconnects = sum(e.get("event") == "collab.reconnect" for e in recent)
    connections = sum(e.get("event") == "collab.connection" for e in recent)
    durations = [float(e["duration_ms"]) for e in recent if e.get("event") == "metric.duration" and e.get("operation") == "collab.sync" and isinstance(e.get("duration_ms"), (int,float))]
    conflict_rate = conflicts / max(1, syncs)
    reconnect_rate = reconnects / max(1, connections)
    latency = sum(durations) / len(durations) if durations else 0.0
    penalties = min(100.0, conflict_rate / SLO_TARGETS["sync_conflict_rate"] * 30 + reconnect_rate / SLO_TARGETS["reconnect_rate"] * 30 + latency / SLO_TARGETS["operation_latency_ms"] * 40)
    score = round(max(0.0, min(100.0, 100.0 - penalties)), 1)
    status = "healthy" if score >= 90 else "degraded" if score >= 70 else "critical"
    return {"score": score, "status": status, "window_seconds": HEALTH_WINDOW_SECONDS,
            "signals": {"sync_conflict_rate": round(conflict_rate,4), "reconnect_rate": round(reconnect_rate,4), "sync_latency_ms": round(latency,2)}}

def circuit_state():
    with _CIRCUIT_LOCK:
        return dict(_CIRCUIT)

def circuit_allow(priority="normal"):
    with _CIRCUIT_LOCK:
        if _CIRCUIT["state"] != "open":
            return True
        if priority in {"critical", "operation", "lock"}:
            return True
        if time.time() - _CIRCUIT["opened_at"] >= 30:
            _CIRCUIT["state"] = "half_open"
            return True
        return False

def circuit_record_failure():
    with _CIRCUIT_LOCK:
        _CIRCUIT["failures"] += 1
        if _CIRCUIT["failures"] >= 5:
            _CIRCUIT["state"] = "open"
            _CIRCUIT["opened_at"] = time.time()
            record("collab.circuit_open", failures=_CIRCUIT["failures"])

def circuit_record_success():
    with _CIRCUIT_LOCK:
        _CIRCUIT["failures"] = 0
        _CIRCUIT["state"] = "closed"

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
