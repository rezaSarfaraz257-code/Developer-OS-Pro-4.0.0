"""Crash recovery and self-healing primitives for Developer OS."""
from __future__ import annotations
import time
from collections import deque

MAX_SESSIONS = 512
_SESSIONS = {}
_EVENTS = deque(maxlen=1000)

def _event(kind, session_id, **fields):
    item={"event":kind,"session_id":session_id,"timestamp":time.time(),**fields}
    _EVENTS.append(item)
    return item

def checkpoint(session_id, workspace_id, files=None, state=None):
    sid=str(session_id or "").strip()
    if not sid:
        raise ValueError("Session id is required.")
    if len(_SESSIONS) >= MAX_SESSIONS and sid not in _SESSIONS:
        oldest=next(iter(_SESSIONS))
        _SESSIONS.pop(oldest,None)
    snapshot={"session_id":sid,"workspace_id":workspace_id,
              "files":dict(files or {}),"state":dict(state or {}),
              "updated_at":time.time()}
    _SESSIONS[sid]=snapshot
    _event("checkpoint.created",sid,workspace_id=workspace_id)
    return {"status":"checkpointed","session_id":sid}

def recover(session_id, reason="unknown"):
    sid=str(session_id or "").strip()
    snapshot=_SESSIONS.get(sid)
    if not snapshot:
        return {"status":"unrecoverable","session_id":sid,"reason":"checkpoint_missing"}
    _event("recovery.started",sid,reason=str(reason)[:200])
    return {
        "status":"recovered",
        "session_id":sid,
        "workspace_id":snapshot["workspace_id"],
        "files":snapshot["files"],
        "state":snapshot["state"],
        "recovery":{"strategy":"last-known-good-checkpoint","verified":False},
    }

def mark_verified(session_id):
    sid=str(session_id or "").strip()
    if sid not in _SESSIONS:
        return {"status":"unknown"}
    _event("recovery.verified",sid)
    return {"status":"verified","session_id":sid}

def status():
    return {"status":"ready","sessions":len(_SESSIONS),
            "recent_events":list(_EVENTS)[-50:]}
