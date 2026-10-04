"""AI Engineering Agent control-plane contracts.

The agent is deterministic at the control-plane boundary: it builds bounded
context and an explicit plan. Model execution remains an external provider
concern, while patches are validated before they can be applied.
"""
from __future__ import annotations
import re

MAX_FILES = 24
MAX_FILE_CHARS = 12000
ALLOWED_ACTIONS = {"explain", "fix", "refactor", "test", "build"}

def _safe_path(path):
    value=str(path or "").replace("\\","/").strip().lstrip("/")
    if not value or ".." in value.split("/"):
        raise ValueError("Invalid workspace path.")
    return value

def build_context(files, paths=None, active_file=""):
    files=files or {}
    requested=list(paths or [])
    if active_file and active_file not in requested:
        requested.insert(0, active_file)
    selected=requested or list(files)[:MAX_FILES]
    selected=selected[:MAX_FILES]
    context=[]
    for path in selected:
        safe=_safe_path(path)
        if safe not in files:
            continue
        value=str(files[safe] or "")
        context.append({"path":safe,"content":value[:MAX_FILE_CHARS],
                        "truncated":len(value)>MAX_FILE_CHARS})
    return context

def plan(request, files):
    action=str(request.get("action") or "fix").strip().lower()
    if action not in ALLOWED_ACTIONS:
        raise ValueError("Unsupported AI engineering action.")
    context=build_context(files, request.get("paths"), request.get("active_file"))
    return {
        "status":"ready",
        "action":action,
        "goal":str(request.get("goal") or "").strip()[:1000],
        "context":context,
        "workflow":["context","plan","patch","diff","validate","apply-or-reject"],
        "apply_policy":{"requires_explicit_approval":True,"max_files":MAX_FILES},
    }

def validate_patch(patch, files):
    files=files or {}
    if not isinstance(patch,list) or len(patch)>MAX_FILES:
        raise ValueError("Patch exceeds the allowed file limit.")
    checked=[]
    for item in patch:
        path=_safe_path(item.get("path"))
        operation=str(item.get("operation") or "update").lower()
        if operation not in {"create","update","delete"}:
            raise ValueError("Unsupported patch operation.")
        if operation=="delete" and path not in files:
            raise ValueError("Cannot delete a missing file.")
        content=str(item.get("content") or "")
        if len(content)>MAX_FILE_CHARS*4:
            raise ValueError("Patch content is too large.")
        checked.append({"path":path,"operation":operation,"content":content})
    return checked
