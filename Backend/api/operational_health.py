""""Dependency-free operational liveness endpoint.

Liveness deliberately avoids database, cache, email and external-service calls.
It is safe for orchestrators to use as a process-health signal.
"""
from __future__ import annotations

import os
import re

from django.http import JsonResponse
from django.views.decorators.http import require_GET


_RELEASE_SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{7,40}$")


def release_identity():
    """Return one release identity shared by all public health endpoints.

    On Render, the platform-provided commit is authoritative. A manually
    configured RELEASE_SHA can become stale after later deployments, which
    would make healthy new code advertise an old release identity. Use the
    explicit override only when the platform SHA is absent or malformed.
    Reject malformed fallback values too, so monitoring never mistakes an
    arbitrary environment string for a real deployed revision.
    """
    render_sha = os.environ.get("RENDER_GIT_COMMIT", "").strip()
    configured_sha = os.environ.get("RELEASE_SHA", "").strip()
    if _RELEASE_SHA_PATTERN.fullmatch(render_sha):
        release_sha = render_sha
    elif _RELEASE_SHA_PATTERN.fullmatch(configured_sha):
        release_sha = configured_sha
    else:
        release_sha = "unknown"

    return {
        "version": os.environ.get("RELEASE_VERSION", "4.0.0"),
        "release_sha": release_sha,
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
