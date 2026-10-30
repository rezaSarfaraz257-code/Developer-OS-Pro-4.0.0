"""Authentication transport for IDE WebSockets.

JWTs must not be placed in URLs, where reverse-proxy and access logs can retain
them. The browser sends the JWT in a WebSocket subprotocol instead.
"""
import re

APP_SUBPROTOCOL = "developer-os"
_AUTH_PREFIX = "auth."
_TOKEN_RE = re.compile(r"^[A-Za-z0-9._~-]+$")


def extract_websocket_token(scope):
    """Return exactly one bounded JWT from offered WebSocket subprotocols."""
    protocols = scope.get("subprotocols") or []
    candidates = [
        item[len(_AUTH_PREFIX):]
        for item in protocols
        if isinstance(item, str) and item.startswith(_AUTH_PREFIX)
    ]
    if len(candidates) != 1:
        return None
    token = candidates[0]
    if not token or len(token) > 8192 or not _TOKEN_RE.fullmatch(token):
        return None
    return token
