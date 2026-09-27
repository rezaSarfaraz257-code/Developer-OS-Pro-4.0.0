import time
import uuid
import logging

logger = logging.getLogger("developer_os.request")


class RequestObservabilityMiddleware:
    """Attach a correlation ID and server timing to every API response."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.request_id = request_id
        started = time.perf_counter()
        try:
            response = self.get_response(request)
        except Exception:
            logger.exception("Unhandled request request_id=%s method=%s path=%s", request_id, request.method, request.path)
            raise
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response["X-Request-ID"] = request_id
        response["Server-Timing"] = f"app;dur={duration_ms}"
        response["X-Content-Type-Options"] = "nosniff"
        response["X-Request-ID"] = request_id
        return response


class ApiSecurityHeadersMiddleware:
    """Add restrictive browser headers to JSON API responses."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith("/api/"):
            response["Content-Security-Policy"] = (
                "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
                "form-action 'none'"
            )
            response["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
            response["Cross-Origin-Resource-Policy"] = "same-site"
        return response
