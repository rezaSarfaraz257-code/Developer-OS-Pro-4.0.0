"""Validation helpers for untrusted IDE collaboration messages."""
from __future__ import annotations

import json
import re

MAX_FILE_BYTES = 1_000_000
MAX_WORKSPACE_BYTES = 50_000_000
MAX_WORKSPACE_FILES = 2_000
MAX_PRESENCE_BYTES = 2_048
MAX_WEBSOCKET_MESSAGE_BYTES = 64_000
MAX_CRDT_INSERT_BYTES = 16_000
_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,96}$")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def validate_workspace_path(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 500:
        raise ValueError("Invalid workspace path.")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Invalid workspace path.")
    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or _WINDOWS_DRIVE_RE.match(normalized):
        raise ValueError("Absolute paths are not allowed.")
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} or part.lower() == ".git" for part in parts):
        raise ValueError("Unsafe workspace path.")
    if any(":" in part for part in parts):
        raise ValueError("Unsafe workspace path.")
    return normalized


def validate_workspace_text(value: object) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise ValueError("Workspace files must contain plain text.")
    try:
        size = len(value.encode("utf-8", errors="strict"))
    except UnicodeError as exc:
        raise ValueError("Workspace text must be valid UTF-8.") from exc
    if size > MAX_FILE_BYTES:
        raise ValueError("Workspace file exceeds the 1 MB limit.")
    return value


def validate_workspace_total(files: dict) -> int:
    if not isinstance(files, dict) or len(files) > MAX_WORKSPACE_FILES:
        raise ValueError("Workspace file count limit exceeded.")
    total = 0
    for path, content in files.items():
        validate_workspace_path(path)
        validate_workspace_text(content)
        total += len(content.encode("utf-8"))
        if total > MAX_WORKSPACE_BYTES:
            raise ValueError("Workspace exceeds the 50 MB limit.")
    return total


def bounded_json_object(value: object) -> dict:
    if not isinstance(value, dict):
        return {}
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        return {}
    if len(encoded) > MAX_PRESENCE_BYTES:
        return {}
    return value


def validate_client_id(value: object) -> str:
    if not isinstance(value, str) or not _CLIENT_ID_RE.fullmatch(value):
        raise ValueError("Invalid collaboration client id.")
    return value


def validate_websocket_message(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    try:
        size = len(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError, UnicodeError):
        return False
    return size <= MAX_WEBSOCKET_MESSAGE_BYTES
