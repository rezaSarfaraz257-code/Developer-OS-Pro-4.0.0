"""Operational health endpoints kept independent from application business logic.

Liveness answers one question only: is the Python/ASGI process alive enough to
serve an HTTP response? It deliberately performs no database, Redis, email or
external-service checks. Readiness remains the traffic-gating probe.
"""
from __future__ import annotations

import os

from django.http import JsonResponse
from django.views.decorators.http import require_GET


@require_GET
def liveness_api(request):
    """Cheap process liveness probe suitable for container orchestrators."""
    return JsonResponse(
        {
            "status": "alive",
            "service": "developer-os-api",
            "version": os.environ.get("RELEASE_VERSION", "1.0.0"),
        },
        status=200,
    )
