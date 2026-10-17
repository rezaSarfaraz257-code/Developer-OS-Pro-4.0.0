"""Dependency-free operational liveness endpoint.

Liveness deliberately avoids database, cache, email and external-service calls.
It is safe for orchestrators to use as a process-health signal.
"""
from __future__ import annotations

import os

from django.http import JsonResponse
from django.views.decorators.http import require_GET


def release_identity():
    """Return one release identity shared by all public health endpoints."""
    return {
        "version": os.environ.get("RELEASE_VERSION", "4.0.0"),
        "release_sha": os.environ.get("RELEASE_SHA") or os.environ.get("RENDER_GIT_COMMIT", "unknown"),
    }


@require_GET
def liveness_api(request):
    """Return minimal immutable release/process identity for liveness probes."""
    response = JsonResponse(
        {
            "status": "alive",
            "service": "developer-os-api",
            **release_identity(),
        },
        status=200,
    )
    response["Cache-Control"] = "no-store, max-age=0"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response
