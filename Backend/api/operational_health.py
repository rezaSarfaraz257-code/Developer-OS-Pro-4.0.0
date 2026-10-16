"""Dependency-free liveness endpoint for orchestration and uptime checks.

Liveness deliberately avoids database, cache, email, and provider calls. Use
/api/health/ready/ for dependency readiness; this endpoint only confirms that
the Django process is responding and identifies the running release.
"""
import os

from django.http import JsonResponse
from django.views.decorators.http import require_GET


@require_GET
def liveness_api(request):
    response = JsonResponse(
        {
            "status": "alive",
            "service": "developer-os-api",
            "version": os.environ.get("RELEASE_VERSION", "4.0.0"),
            "release_sha": os.environ.get("RELEASE_SHA", "unknown"),
        },
        status=200,
    )
    response["Cache-Control"] = "no-store, max-age=0"
    return response
