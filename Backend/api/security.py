"""Production security primitives shared by Developer OS API endpoints.

The module keeps authorization decisions close to the data model, normalizes
API errors, adds distributed-friendly fixed-window abuse controls, and
validates outbound URLs against common SSRF targets.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
import time
from urllib.parse import urlparse

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse
from rest_framework.exceptions import APIException
from rest_framework.views import exception_handler as drf_exception_handler

from .models import AuditLog, Project

logger = logging.getLogger("developer_os.security")

# Public probes are deliberately excluded. All other API traffic gets a
# conservative fixed-window limit before it reaches application code.
RATE_WINDOWS = {
    "auth": (60, 10),
    "email_verification": (3600, 5),
    "password": (3600, 8),
    "mfa": (3600, 20),
    "billing": (60, 30),
    "ai": (60, 30),
    "runner": (60, 20),
    "upload": (60, 20),
    "invite": (60, 20),
    "referral": (3600, 12),
    "api": (60, 120),
}


def client_ip(request):
    # Only trust X-Forwarded-For when the deployment explicitly opts in.
    if getattr(settings, "TRUST_PROXY_HEADERS", False):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or "unknown"


def rate_bucket(path: str) -> str:
    p = path.lower()
    if "/auth/resend-verification/" in p or "/auth/verify-email/" in p:
        return "email_verification"
    if "password-reset" in p:
        return "password"
    if any(x in p for x in ("/token/", "/register/", "/login/", "/logout/")):
        return "auth"
    if "/mfa/" in p:
        return "mfa"
    if "/billing/" in p or "/subscription/" in p:
        return "billing"
    if "/ai/" in p or "/assistant/" in p:
        return "ai"
    if "/ide/" in p:
        return "runner"
    if "/profile/" in p or "/upload" in p:
        return "upload"
    if "/invites" in p or "/invite" in p:
        return "invite"
    if "/referrals/" in p:
        return "referral"
    return "api"


def _principal(request):
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return f"u:{user.pk}"
    return f"ip:{client_ip(request)}"


def consume_rate_limit(request):
    if not request.path.startswith("/api/") or request.path in {"/api/health/", "/api/health/ready/", "/api/status/"}:
        return True, None
    bucket = rate_bucket(request.path)
    window, limit = RATE_WINDOWS[bucket]
    now = int(time.time())
    slot = now // window
    key = f"dos:rl:{bucket}:{_principal(request)}:{slot}"
    try:
        created = cache.add(key, 1, timeout=window + 2)
        if not created:
            count = cache.incr(key)
        else:
            count = 1
        if count > limit:
            return False, max(1, window - (now % window))
    except Exception:
        # Rate limiting must fail closed for sensitive buckets and fail open
        # only for the broad API bucket if Redis is temporarily unavailable.
        if bucket != "api":
            return False, 30
    return True, None


class ApiRateLimitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        allowed, retry_after = consume_rate_limit(request)
        if not allowed:
            response = JsonResponse({
                "error": {
                    "code": "rate_limited",
                    "message": "Too many requests. Please retry later.",
                    "request_id": getattr(request, "request_id", ""),
                }
            }, status=429)
            if retry_after:
                response["Retry-After"] = str(retry_after)
            return response
        return self.get_response(request)


def api_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    request = context.get("request")
    request_id = getattr(request, "request_id", "") if request else ""
    if response is not None:
        data = response.data
        if isinstance(data, dict) and "detail" in data and len(data) == 1:
            message = str(data["detail"])
        elif isinstance(data, dict):
            message = "Validation failed."
        else:
            message = str(data)
        response.data = {
            "error": {
                "code": getattr(exc, "default_code", "api_error"),
                "message": message,
                "details": data if isinstance(data, dict) and "detail" not in data else None,
                "request_id": request_id,
            }
        }
        return response

    # Never expose exception text, SQL, stack traces, tokens or filesystem
    # paths to clients. Keep the full diagnostic server-side with correlation.
    logger.exception("Unhandled API exception request_id=%s path=%s", request_id, getattr(request, "path", ""), exc_info=exc)
    return JsonResponse({
        "error": {
            "code": "internal_error",
            "message": "An unexpected error occurred.",
            "request_id": request_id,
        }
    }, status=500)


def visible_projects(user):
    return Project.objects.filter(owner=user) | Project.objects.filter(collaborators=user)


def project_queryset_for(user):
    return Project.objects.filter(owner=user).union(Project.objects.filter(collaborators=user))


def can_access_project(project: Project, user) -> bool:
    return bool(project and user and user.is_authenticated and (
        project.owner_id == user.id or project.collaborators.filter(pk=user.pk).exists()
    ))


def can_manage_project(project: Project, user) -> bool:
    return bool(project and user and user.is_authenticated and project.owner_id == user.pk)


def require_project_access(project, user, *, manage=False):
    allowed = can_manage_project(project, user) if manage else can_access_project(project, user)
    if not allowed:
        raise PermissionError("project access denied")
    return project


_AUDIT_SECRET_KEYS = {
    "token", "password", "passwd", "secret", "authorization", "proxyauthorization",
    "apikey", "accesstoken", "refreshtoken", "clientsecret", "clienttoken",
    "cookie", "setcookie", "privatekey", "credential", "credentials",
}


def _sanitize_audit_value(value, *, depth=0):
    """Return bounded JSON-safe audit metadata with secrets removed recursively."""
    if depth > 6:
        return "[truncated]"
    if isinstance(value, dict):
        safe = {}
        for raw_key, item in list(value.items())[:100]:
            key = str(raw_key)[:80]
            normalized = "".join(char.lower() for char in key if char.isalnum())
            if (
                normalized in _AUDIT_SECRET_KEYS
                or "secret" in normalized
                or "authorization" in normalized
                or normalized.endswith(("password", "passwd", "token", "credential", "credentials"))
                or normalized.endswith(("apikey", "privatekey", "authorization", "cookie"))
            ):
                continue
            safe[key] = _sanitize_audit_value(item, depth=depth + 1)
        return safe
    if isinstance(value, (list, tuple)):
        return [_sanitize_audit_value(item, depth=depth + 1) for item in value[:50]]
    if isinstance(value, str):
        return value[:500]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:500]


def audit_security_event(user, action, *, organization=None, target_type="", target_id="", metadata=None):
    # Metadata may contain nested request/provider objects. Redact recursively
    # before persistence so secrets cannot leak through an innocent outer key.
    safe_meta = _sanitize_audit_value(metadata if isinstance(metadata, dict) else {})
    return AuditLog.objects.create(
        user=user if getattr(user, "is_authenticated", False) else None,
        organization=organization,
        action=str(action)[:120],
        target_type=str(target_type)[:80],
        target_id=str(target_id or "")[:120],
        metadata=safe_meta,
    )


def validate_remote_url(value: str, *, allow_hosts=None, allow_private=False) -> str:
    """Validate an outbound URL and reject unsafe destinations before use.

    Callers must still prevent DNS rebinding by pinning the validated address
    at connection time; a validation-time DNS lookup alone cannot guarantee
    that a later HTTP client connects to the same address.
    """
    raw = str(value or "").strip()
    if not raw or any(ord(char) < 32 or ord(char) == 127 for char in raw):
        raise ValueError("URL is empty or contains control characters.")
    parsed = urlparse(raw)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only absolute http(s) URLs are allowed.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URLs containing embedded credentials are not allowed.")
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname.rstrip(".").lower()
    if not hostname or "%" in hostname:
        raise ValueError("Destination hostname is invalid.")
    if allow_hosts and hostname not in {str(host).rstrip(".").lower() for host in allow_hosts}:
        raise ValueError("Destination host is not allowed.")
    try:
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise ValueError("Destination port is invalid.") from exc
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)}
    except (socket.gaierror, OSError) as exc:
        raise ValueError("Destination host could not be resolved.") from exc
    if not addresses:
        raise ValueError("Destination host could not be resolved.")
    if not allow_private:
        for address in addresses:
            try:
                ip = ipaddress.ip_address(address)
            except ValueError as exc:
                raise ValueError("Destination resolved to an invalid IP address.") from exc
            # IPv4-mapped IPv6 addresses must be judged by their embedded IPv4
            # address, not by the outer IPv6 address classification.
            if getattr(ip, "ipv4_mapped", None) is not None:
                ip = ip.ipv4_mapped
            if not ip.is_global:
                raise ValueError("Private or non-global destinations are not allowed.")
    return parsed.geturl()


def require_safe_url(value: str, **kwargs):
    try:
        return validate_remote_url(value, **kwargs)
    except ValueError as exc:
        raise APIException(detail=str(exc), code="unsafe_url") from exc
