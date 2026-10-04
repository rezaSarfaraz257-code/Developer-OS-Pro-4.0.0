"""Developer OS debugger control-plane primitives.

The runner owns debugger lifecycle. This module intentionally does not execute
debuggee code itself; it exposes a small, deterministic session state machine
that can be backed by a DAP adapter when the runtime image provides one.
"""
from __future__ import annotations
import importlib.util
import threading
import time
import uuid

DEBUGPY_AVAILABLE = importlib.util.find_spec("debugpy") is not None
_LOCK = threading.RLock()
_SESSIONS = {}

SUPPORTED_ACTIONS = {
    "start", "continue", "pause", "step_over", "step_into", "step_out",
    "stop", "set_breakpoint", "remove_breakpoint", "evaluate",
    "stack", "variables", "scopes", "watch", "status",
}

def capability():
    return {
        "available": bool(DEBUGPY_AVAILABLE),
        "adapter": "debugpy" if DEBUGPY_AVAILABLE else None,
        "protocol": "DAP" if DEBUGPY_AVAILABLE else None,
        "mode": "adapter-ready" if DEBUGPY_AVAILABLE else "unavailable",
        "reason": None if DEBUGPY_AVAILABLE else "debugpy is not installed in the runner image",
    }

def _session(sid):
    return _SESSIONS.get(sid)

def handle(action, *, session_id="", path="", line=0, column=1,
           condition="", expression="", breakpoints=None):
    action = str(action or "status").strip().lower()
    if action not in SUPPORTED_ACTIONS:
        raise ValueError("Unsupported debugger action.")
    with _LOCK:
        sid = str(session_id or "")
        if action == "start" and not sid:
            sid = uuid.uuid4().hex[:16]
            _SESSIONS[sid] = {
                "id": sid, "state": "paused", "thread_id": 1,
                "started_at": time.time(), "breakpoints": [],
                "path": path, "line": max(1, int(line or 1)),
            }
        item = _SESSIONS.get(sid)
        if not item:
            if action == "status":
                return {"session_id": None, "state": "idle", "breakpoints": [], "stack": [], "scopes": [], "variables": []}
            raise ValueError("Debug session not found.")
        if action == "stop":
            item["state"] = "stopped"
        elif action in {"continue", "step_over", "step_into", "step_out", "pause"}:
            item["state"] = "paused" if action != "continue" else "running"
        elif action == "set_breakpoint":
            bp = {"path": path, "line": max(1, int(line or 1)), "column": max(1, int(column or 1)), "verified": True}
            item["breakpoints"] = [b for b in item["breakpoints"] if not (b["path"] == bp["path"] and b["line"] == bp["line"])]
            item["breakpoints"].append(bp)
        elif action == "remove_breakpoint":
            item["breakpoints"] = [b for b in item["breakpoints"] if not (b["path"] == path and b["line"] == max(1, int(line or 1)))]
        stack = []
        if item["state"] == "paused":
            stack = [{"id": 1, "name": "main", "path": item.get("path") or path, "line": item.get("line") or 1, "column": 1}]
        return {
            "session_id": sid, "state": item["state"], "thread_id": item["thread_id"],
            "frame": stack[0] if stack else None, "stack": stack,
            "scopes": [{"name": "Locals", "variables_reference": 1}] if stack else [],
            "variables": [], "breakpoints": item["breakpoints"],
            "output": "Debugger adapter ready." if DEBUGPY_AVAILABLE else "Debugger control plane ready; runtime adapter is not installed.",
            "diagnostics": ([] if DEBUGPY_AVAILABLE else [{"severity": "info", "message": capability()["reason"]}]),
            "result": {"expression": expression, "value": None} if action == "evaluate" else None,
        }
