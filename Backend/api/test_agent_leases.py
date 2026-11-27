from datetime import timedelta
from types import SimpleNamespace

from django.test import SimpleTestCase
from django.utils import timezone

from .agent_leases import JOB_LEASE_STALE_AFTER, is_current_job_lease


class CurrentJobLeaseTests(SimpleTestCase):
    def setUp(self):
        self.now = timezone.now()
        self.token = "worker:unique-fencing-token"

    def job(self, **overrides):
        values = {
            "status": "running",
            "locked_by": self.token,
            "locked_at": self.now - timedelta(seconds=1),
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_current_lease_requires_running_state_and_exact_nonempty_token(self):
        self.assertTrue(is_current_job_lease(self.job(), self.token, now=self.now))
        self.assertFalse(is_current_job_lease(self.job(status="queued"), self.token, now=self.now))
        self.assertFalse(is_current_job_lease(self.job(status="succeeded"), self.token, now=self.now))
        self.assertFalse(is_current_job_lease(self.job(), "another-worker:token", now=self.now))
        self.assertFalse(is_current_job_lease(self.job(), "", now=self.now))
        self.assertFalse(is_current_job_lease(self.job(), None, now=self.now))

    def test_lease_expiry_boundary_is_inclusive_but_older_leases_are_rejected(self):
        boundary = self.now - JOB_LEASE_STALE_AFTER
        self.assertTrue(
            is_current_job_lease(self.job(locked_at=boundary), self.token, now=self.now)
        )
        self.assertFalse(
            is_current_job_lease(
                self.job(locked_at=boundary - timedelta(microseconds=1)),
                self.token,
                now=self.now,
            )
        )

    def test_future_and_naive_lease_timestamps_fail_closed(self):
        self.assertFalse(
            is_current_job_lease(
                self.job(locked_at=self.now + timedelta(microseconds=1)),
                self.token,
                now=self.now,
            )
        )
        self.assertFalse(
            is_current_job_lease(
                self.job(locked_at=self.now.replace(tzinfo=None)),
                self.token,
                now=self.now,
            )
        )
        self.assertFalse(
            is_current_job_lease(self.job(locked_at=None), self.token, now=self.now)
        )

    def test_missing_job_and_naive_clock_fail_closed_without_comparison_errors(self):
        self.assertFalse(is_current_job_lease(None, self.token, now=self.now))
        self.assertFalse(
            is_current_job_lease(
                self.job(),
                self.token,
                now=self.now.replace(tzinfo=None),
            )
        )
        self.assertFalse(is_current_job_lease(self.job(), self.token, now="not-a-datetime"))
