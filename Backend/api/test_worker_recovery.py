from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from .management.commands.worker import recover_stale_jobs
from .models import AgentRun, AuditLog, BackgroundJob, CodeWorkspace


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
        self.assertIsNone(job.locked_at)
        self.assertEqual(job.locked_by, "")
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
        self.assertIsNone(job.locked_at)
        self.assertEqual(job.locked_by, "")
        self.assertIsNotNone(job.dead_lettered_at)
        self.assertIsNotNone(job.finished_at)

    def test_stale_agent_plan_retry_returns_run_to_queued(self):
        owner = User.objects.create_user(username="agent-recovery-retry")
        workspace = CodeWorkspace.objects.create(owner=owner, name="Recovery workspace")
        run = AgentRun.objects.create(
            owner=owner,
            workspace=workspace,
            goal="Recover planning safely",
            status="planning",
            idempotency_key="agent-recovery-retry-01",
            workspace_revision=workspace.revision,
        )
        job = BackgroundJob.objects.create(
            kind="ai_agent_plan",
            payload={"run_id": run.id},
            status="running",
            attempts=1,
            max_attempts=3,
            locked_at=timezone.now() - timedelta(minutes=15),
        )

        recover_stale_jobs()

        run.refresh_from_db()
        job.refresh_from_db()
        self.assertEqual(job.status, "queued")
        self.assertEqual(run.status, "queued")
        self.assertIsNone(run.started_at)
        self.assertFalse(AuditLog.objects.filter(target_id=str(run.id), action="agent_run.failed").exists())

    def test_exhausted_agent_plan_lease_fails_run_and_writes_audit(self):
        owner = User.objects.create_user(username="agent-recovery-dead")
        workspace = CodeWorkspace.objects.create(owner=owner, name="Dead-letter workspace")
        run = AgentRun.objects.create(
            owner=owner,
            workspace=workspace,
            goal="Fail after exhausted lease",
            status="planning",
            idempotency_key="agent-recovery-dead-01",
            workspace_revision=workspace.revision,
        )
        job = BackgroundJob.objects.create(
            kind="ai_agent_plan",
            payload={"run_id": run.id},
            status="running",
            attempts=2,
            max_attempts=2,
            locked_at=timezone.now() - timedelta(minutes=15),
        )

        recover_stale_jobs()

        run.refresh_from_db()
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertEqual(run.status, "failed")
        self.assertIn("WorkerLeaseExpired", run.error)
        self.assertIsNotNone(run.finished_at)
        self.assertTrue(AuditLog.objects.filter(
            user=owner,
            action="agent_run.failed",
            target_id=str(run.id),
            metadata__reason="worker_lease_exhausted",
        ).exists())
