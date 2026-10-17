"""Dependency-free operational liveness endpoint.

Liveness deliberately avoids database, cache, email and external-service calls.
It is safe for orchestrators to use as a process-health signal.
"""
from __future__ import annotations

import os

from django.http import JsonResponse
from django.views.decorators.http import require_GET


def release_identity():
    """Return one consistent release identity for every public health probe."""
    return {
        "version": (os.environ.get("RELEASE_VERSION") or "4.0.0").strip(),
        "release_sha": (os.environ.get("RELEASE_SHA") or os.environ.get("RENDER_GIT_COMMIT") or "unknown").strip(),
    }


def no_store(response):
    """Prevent browsers and intermediary caches from serving stale health payloads."""
    response["Cache-Control"] = "no-store, max-age=0"
    return response


@require_GET
def liveness_api(request):
    """Return minimal immutable release/process identity for liveness probes."""
    return no_store(JsonResponse(
        {
            "status": "alive",
            "service": "developer-os-api",
            **release_identity(),
        },
        status=200,
    ))
