from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from .management.commands.worker import recover_stale_jobs
from .models import BackgroundJob


class StaleWorkerJobRecoveryTests(TestCase):
    def test_running_job_without_lock_timestamp_is_requeued(self):
        job = BackgroundJob.objects.create(
            kind="email",
            status="running",
            attempts=1,
            max_attempts=3,
            locked_at=None,
        )

        recover_stale_jobs()

        job.refresh_from_db()
        self.assertEqual(job.status, "queued")
        self.assertIn("Recovered stale worker lease.", job.error)

    def test_stale_running_job_at_attempt_limit_is_dead_lettered(self):
        job = BackgroundJob.objects.create(
            kind="email",
            status="running",
            attempts=3,
            max_attempts=3,
            locked_at=timezone.now() - timedelta(minutes=15),
        )

        recover_stale_jobs()

        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertIsNotNone(job.dead_lettered_at)
        self.assertIsNotNone(job.finished_at)
