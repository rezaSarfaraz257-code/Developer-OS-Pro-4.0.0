"""Versioned IDE state projection built from durable runner events."""
from __future__ import annotations
import threading
import time

_LOCK = threading.RLock()
_STATES = {}
MAX_WORKSPACES = 512

def _default(workspace_id):
    return {
        "schema_version": "1",
        "workspace_id": str(workspace_id),
        "revision": 0,
        "updated_at": time.time(),
        "active_file": "",
        "execution": {"status": "idle", "process_id": None, "trace_id": None, "exit_code": None},
        "diagnostics": {"count": 0, "items": []},
        "debugger": {"status": "idle", "session_id": None},
        "last_event_sequence": 0,
    }

def _state(workspace_id):
    wid = str(workspace_id)
    state = _STATES.get(wid)
    if state is None:
        if len(_STATES) >= MAX_WORKSPACES:
            _STATES.pop(next(iter(_STATES)), None)
        state = _default(wid)
        _STATES[wid] = state
    return state

def apply_event(event):
    wid = event.get("workspace_id")
    if wid is None:
        return None
    with _LOCK:
        state = _state(wid)
        typ = event.get("type", "")
        data = event.get("data") or {}
        state["last_event_sequence"] = max(state["last_event_sequence"], int(event.get("sequence", 0)))
        if typ == "execution.started":
            state["execution"] = {"status": "running", "process_id": data.get("process_id"), "trace_id": event.get("trace_id"), "exit_code": None}
        elif typ == "execution.finished":
            state["execution"].update({"status": data.get("status", "finished"), "exit_code": data.get("exit_code")})
        elif typ == "execution.timeout":
            state["execution"].update({"status": "timeout", "exit_code": 124})
        elif typ == "debug.started":
            state["debugger"] = {"status": "running", "session_id": data.get("session_id")}
        elif typ in {"debug.stopped", "debug.finished"}:
            state["debugger"]["status"] = "stopped"
        state["revision"] += 1
        state["updated_at"] = time.time()
        return dict(state)

def get(workspace_id):
    with _LOCK:
        return dict(_state(workspace_id))
