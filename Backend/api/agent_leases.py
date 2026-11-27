"""Shared validity rules for durable BackgroundJob worker leases."""
from datetime import datetime, timedelta

from django.utils import timezone


JOB_LEASE_STALE_AFTER = timedelta(minutes=10)


def is_current_job_lease(job, lease_token, *, now=None):
    """Require both the fencing token and an unexpired, well-formed lease."""
    if not job or getattr(job, "status", None) != "running":
        return False
    if not isinstance(lease_token, str) or not lease_token.strip():
        return False
    if getattr(job, "locked_by", None) != lease_token:
        return False
    locked_at = getattr(job, "locked_at", None)
    if not isinstance(locked_at, datetime) or not timezone.is_aware(locked_at):
        return False
    current_time = timezone.now() if now is None else now
    # A caller-supplied clock is useful for deterministic boundary tests, but
    # malformed/naive values must fail closed rather than raising during
    # datetime comparison or accidentally weakening the lease check.
    if not isinstance(current_time, datetime) or not timezone.is_aware(current_time):
        return False
    return current_time - JOB_LEASE_STALE_AFTER <= locked_at <= current_time
