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


class JsonLogFormatter(logging.Formatter):
    """Small dependency-free JSON formatter for production logs."""
    def format(self, record):
        import json
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None)
        if request_id:
            payload["request_id"] = request_id
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)

# Public probes are deliberately excluded. All other API traffic gets a
# conservative fixed-window limit before it reaches application code.
RATE_WINDOWS = {
    "auth": (60, 10),
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
    if any(x in p for x in ("/token/", "/register/", "/login/", "/logout/")):
        return "auth"
    if "password-reset" in p:
        return "password"
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


def audit_security_event(user, action, *, organization=None, target_type="", target_id="", metadata=None):
    safe_meta = {}
    for key, value in (metadata or {}).items():
        if key.lower() in {"token", "password", "secret", "authorization", "api_key", "access_token", "refresh_token"}:
            continue
        safe_meta[str(key)[:80]] = str(value)[:500]
    return AuditLog.objects.create(
        user=user if getattr(user, "is_authenticated", False) else None,
        organization=organization,
        action=action[:120],
        target_type=target_type[:80],
        target_id=str(target_id or "")[:120],
        metadata=safe_meta,
    )


def validate_remote_url(value: str, *, allow_hosts=None, allow_private=False) -> str:
    """Validate an outbound URL and reject localhost/private/link-local targets."""
    parsed = urlparse(str(value or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only absolute http(s) URLs are allowed.")
    hostname = parsed.hostname.rstrip(".").lower()
    if allow_hosts and hostname not in {h.lower() for h in allow_hosts}:
        raise ValueError("Destination host is not allowed.")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise ValueError("Destination host could not be resolved.") from exc
    if not allow_private:
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
                raise ValueError("Private or local destinations are not allowed.")
    return parsed.geturl()


def require_safe_url(value: str, **kwargs):
    try:
        return validate_remote_url(value, **kwargs)
    except ValueError as exc:
        raise APIException(detail=str(exc), code="unsafe_url") from exc
