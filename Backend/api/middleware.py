import re
import time
import uuid
import logging

logger = logging.getLogger("developer_os.request")

# Client-supplied trace IDs are untrusted. Keep them short and log-safe.
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _request_id_from_header(value):
    candidate = (value or "").strip()
    if _REQUEST_ID_PATTERN.fullmatch(candidate):
        return candidate
    return uuid.uuid4().hex


class RequestObservabilityMiddleware:
    """Attach correlation, timing and structured request telemetry to responses."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = _request_id_from_header(request.headers.get("X-Request-ID"))
        request.request_id = request_id
        started = time.perf_counter()
        response = None
        try:
            response = self.get_response(request)
            return response
        except Exception:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.exception(
                "Unhandled request",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.path,
                    "duration_ms": duration_ms,
                },
            )
            raise
        finally:
            if response is not None:
                duration_ms = round((time.perf_counter() - started) * 1000, 2)
                response["X-Request-ID"] = request_id
                response["Server-Timing"] = f"app;dur={duration_ms}"
                response["X-Content-Type-Options"] = "nosniff"
                logger.info(
                    "HTTP request",
                    extra={
                        "request_id": request_id,
                        "method": request.method,
                        "path": request.path,
                        "status_code": response.status_code,
                        "duration_ms": duration_ms,
                    },
                )


class ApiSecurityHeadersMiddleware:
    """Add restrictive browser headers to JSON API responses."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith("/api/"):
            # Endpoint-specific policies (for example, sandboxed untrusted IDE
            # previews) are stricter and must not be overwritten globally.
            if "Content-Security-Policy" not in response:
                response["Content-Security-Policy"] = (
                    "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
                    "form-action 'none'"
                )
            response["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
            response["Cross-Origin-Resource-Policy"] = "same-site"
            # Preview documents are intentionally sandboxed and must be embeddable
            # by the same-origin IDE iframe. Keep all other API responses unframeable.
            is_ide_preview = bool(re.match(r"^/api/ide/workspaces/\\d+/preview(?:/|$)", request.path))
            response["X-Frame-Options"] = "SAMEORIGIN" if is_ide_preview else "DENY"
            if "Referrer-Policy" not in response:
                response["Referrer-Policy"] = "same-origin"
            response["X-Content-Type-Options"] = "nosniff"
        return response
