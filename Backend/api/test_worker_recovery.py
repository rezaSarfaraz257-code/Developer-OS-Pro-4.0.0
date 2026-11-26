from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from .management.commands.worker import STALE_AFTER, Command, claim_job, execute, recover_stale_jobs, refresh_job_lease
from .models import AgentRun, AgentTask, AuditLog, BackgroundJob, CodeWorkspace


class StaleWorkerJobRecoveryTests(TestCase):

    def test_reclaimed_job_fences_out_previous_worker_even_when_identity_is_reused(self):
        job = BackgroundJob.objects.create(
            kind="ai_agent_plan",
            status="queued",
            available_at=timezone.now() - timedelta(seconds=1),
            attempts=0,
            max_attempts=3,
        )

        first_claim = claim_job("worker-reused")
        self.assertEqual(first_claim.pk, job.pk)
        first_lease = first_claim.locked_by
        self.assertTrue(refresh_job_lease(job.pk, first_lease))

        BackgroundJob.objects.filter(pk=job.pk).update(
            locked_at=timezone.now() - timedelta(minutes=15)
        )
        recover_stale_jobs()

        job.refresh_from_db()
        self.assertEqual(job.status, "queued")
        BackgroundJob.objects.filter(pk=job.pk).update(
            available_at=timezone.now() - timedelta(seconds=1)
        )

        second_claim = claim_job("worker-reused")
        self.assertEqual(second_claim.pk, job.pk)
        second_lease = second_claim.locked_by

        self.assertNotEqual(first_lease, second_lease)
        self.assertFalse(refresh_job_lease(job.pk, first_lease))
        self.assertTrue(refresh_job_lease(job.pk, second_lease))


    def test_expired_lease_cannot_be_resurrected_by_original_worker(self):
        job = BackgroundJob.objects.create(
            kind="ai_agent_plan",
            status="running",
            attempts=1,
            max_attempts=3,
            locked_at=timezone.now() - STALE_AFTER - timedelta(seconds=1),
            locked_by="delayed-worker:expired-token",
        )

        self.assertFalse(refresh_job_lease(job.pk, "delayed-worker:expired-token"))

        job.refresh_from_db()
        self.assertEqual(job.status, "running")
        self.assertEqual(job.locked_by, "delayed-worker:expired-token")
        self.assertLess(job.locked_at, timezone.now() - STALE_AFTER)

    def test_recovery_rechecks_a_lease_refreshed_after_candidate_scan(self):
        scan_time = timezone.now()
        job = BackgroundJob.objects.create(
            kind="email",
            status="running",
            attempts=1,
            max_attempts=3,
            locked_at=scan_time - STALE_AFTER - timedelta(seconds=1),
            locked_by="worker:live-lease-token",
        )
        original_select_for_update = BackgroundJob.objects.select_for_update
        refreshed_at = []

        def refresh_lease_before_lock(*args, **kwargs):
            # Simulate the owner extending its lease after the stale candidate
            # scan but before recovery acquires the row lock.
            refreshed_at.append(timezone.now())
            BackgroundJob.objects.filter(pk=job.pk, status="running").update(
                locked_at=refreshed_at[-1]
            )
            return original_select_for_update(*args, **kwargs)

        with patch.object(
            BackgroundJob.objects,
            "select_for_update",
            side_effect=refresh_lease_before_lock,
        ):
            recover_stale_jobs()

        job.refresh_from_db()
        self.assertEqual(job.status, "running")
        self.assertEqual(job.locked_by, "worker:live-lease-token")
        self.assertEqual(job.locked_at, refreshed_at[0])
        self.assertNotIn("Recovered stale worker lease.", job.error)


    @patch("api.management.commands.worker.execute")
    def test_worker_discards_outcome_when_lease_expires_without_reclaim(self, mocked_execute):
        job = BackgroundJob.objects.create(
            kind="ai_agent_plan",
            status="queued",
            available_at=timezone.now() - timedelta(seconds=1),
            attempts=0,
            max_attempts=3,
        )

        executions = []

        def execute_then_expire(claimed_job):
            executions.append(claimed_job.locked_by)
            if len(executions) == 1:
                BackgroundJob.objects.filter(pk=claimed_job.pk).update(
                    locked_at=timezone.now() - STALE_AFTER - timedelta(seconds=1)
                )
                return {"worker": "expired", "must_not_persist": True}
            return {"worker": "replacement", "persisted": True}

        mocked_execute.side_effect = execute_then_expire

        Command().handle(once=True, sleep=0)

        job.refresh_from_db()
        self.assertEqual(len(executions), 2)
        self.assertNotEqual(executions[0], executions[1])
        self.assertEqual(job.status, "succeeded")
        self.assertEqual(job.result, {"worker": "replacement", "persisted": True})

    @patch("api.management.commands.worker.execute")
    def test_old_worker_outcome_is_discarded_after_job_is_reclaimed(self, mocked_execute):
        job = BackgroundJob.objects.create(
            kind="ai_agent_plan",
            status="queued",
            available_at=timezone.now() - timedelta(seconds=1),
            attempts=0,
            max_attempts=3,
        )
        replacement_claims = []

        def execute_then_reclaim(claimed_job):
            old_lease = claimed_job.locked_by
            BackgroundJob.objects.filter(pk=claimed_job.pk).update(
                locked_at=timezone.now() - STALE_AFTER - timedelta(seconds=1)
            )
            recover_stale_jobs()
            BackgroundJob.objects.filter(pk=claimed_job.pk).update(
                available_at=timezone.now() - timedelta(seconds=1)
            )
            replacement = claim_job("replacement-worker")
            replacement_claims.append(replacement)
            self.assertIsNotNone(replacement)
            self.assertNotEqual(old_lease, replacement.locked_by)
            return {"worker": "stale", "must_not_persist": True}

        mocked_execute.side_effect = execute_then_reclaim

        Command().handle(once=True, sleep=0)

        job.refresh_from_db()
        self.assertEqual(job.status, "running")
        self.assertEqual(job.locked_by, replacement_claims[0].locked_by)
        self.assertNotEqual(job.result, {"worker": "stale", "must_not_persist": True})

    def test_running_job_with_future_lease_timestamp_is_recovered(self):
        future_lease = timezone.now() + timedelta(minutes=5)
        job = BackgroundJob.objects.create(
            kind="ai_agent_plan",
            status="running",
            attempts=1,
            max_attempts=3,
            locked_at=future_lease,
            locked_by="clock-skewed-worker:future-token",
        )

        self.assertFalse(refresh_job_lease(job.pk, "clock-skewed-worker:future-token"))
        recover_stale_jobs()

        job.refresh_from_db()
        self.assertEqual(job.status, "queued")
        self.assertIsNone(job.locked_at)
        self.assertEqual(job.locked_by, "")
        self.assertIn("Recovered stale worker lease.", job.error)

    def test_running_job_without_lock_timestamp_is_requeued(self):
        job = BackgroundJob.objects.create(
            kind="ai_agent_plan",
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

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend")
    def test_stale_email_job_is_dead_lettered_to_avoid_duplicate_delivery(self):
        job = BackgroundJob.objects.create(
            kind="email",
            payload={"to": "private@example.invalid", "subject": "Sensitive"},
            status="running",
            attempts=1,
            max_attempts=3,
            locked_at=timezone.now() - timedelta(minutes=15),
            locked_by="interrupted-email-worker:lease",
        )

        recover_stale_jobs()

        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.attempts, 1)
        self.assertIsNone(job.locked_at)
        self.assertEqual(job.locked_by, "")
        self.assertIsNotNone(job.dead_lettered_at)
        self.assertIsNotNone(job.finished_at)
        self.assertIn("outcome is unknown", job.error)
        self.assertIn("automatic retry suppressed", job.error)
        self.assertIsNone(claim_job("email-retry-worker"))

    @override_settings(EMAIL_BACKEND="api.email_backends.resend.EmailBackend")
    def test_stale_resend_email_is_requeued_inside_provider_idempotency_window(self):
        job = BackgroundJob.objects.create(
            kind="email",
            payload={"to": "private@example.invalid"},
            status="running",
            attempts=1,
            max_attempts=3,
            locked_at=timezone.now() - timedelta(minutes=15),
            locked_by="interrupted-resend-worker:lease",
        )

        recover_stale_jobs()

        job.refresh_from_db()
        self.assertEqual(job.status, "queued")
        self.assertEqual(job.attempts, 1)
        self.assertIsNone(job.locked_at)
        self.assertEqual(job.locked_by, "")
        self.assertIsNone(job.dead_lettered_at)
        self.assertIn("Recovered stale worker lease.", job.error)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend")
    @patch("api.management.commands.worker.execute", side_effect=RuntimeError("transport timeout"))
    def test_ambiguous_smtp_failure_is_not_automatically_retried(self, mocked_execute):
        job = BackgroundJob.objects.create(
            kind="email",
            payload={"to": "private@example.invalid"},
            status="queued",
            available_at=timezone.now() - timedelta(seconds=1),
            attempts=0,
            max_attempts=3,
        )

        Command().handle(once=True, sleep=0)

        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.attempts, 1)
        self.assertIsNotNone(job.dead_lettered_at)
        self.assertIn("outcome may be unknown", job.error)
        self.assertIn("automatic retry suppressed", job.error)
        mocked_execute.assert_called_once()

    @override_settings(EMAIL_BACKEND="api.email_backends.resend.EmailBackend")
    @patch("api.management.commands.worker.execute", side_effect=RuntimeError("temporary provider error"))
    def test_resend_failure_remains_retryable_with_provider_idempotency(self, mocked_execute):
        job = BackgroundJob.objects.create(
            kind="email",
            payload={"to": "private@example.invalid"},
            status="queued",
            available_at=timezone.now() - timedelta(seconds=1),
            attempts=0,
            max_attempts=3,
        )

        Command().handle(once=True, sleep=0)

        job.refresh_from_db()
        self.assertEqual(job.status, "queued")
        self.assertEqual(job.attempts, 1)
        self.assertEqual(job.locked_by, "")
        mocked_execute.assert_called_once()

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

    @patch("api.views._runner_request", return_value=({"status": "synced", "files": {"main.py": "print('v2')\n"}}, None))
    def test_agent_runner_sync_job_coalesces_to_latest_locked_workspace_revision(self, runner_request):
        owner = User.objects.create_user(username="agent-runner-sync-owner")
        workspace = CodeWorkspace.objects.create(
            owner=owner,
            name="Runner sync workspace",
            files={"main.py": "print('v1')\n"},
        )
        requested_revision = workspace.revision
        workspace.files = {"main.py": "print('v2')\n"}
        workspace.save(update_fields={"files"})
        workspace.refresh_from_db()
        job = BackgroundJob.objects.create(
            kind="ai_agent_runner_sync",
            payload={
                "workspace_id": workspace.id,
                "owner_id": owner.id,
                "requested_revision": requested_revision,
            },
            idempotency_key=f"agent-runner-sync:{workspace.id}:{requested_revision}",
        )

        result = execute(job)

        self.assertEqual(result["status"], "synced")
        self.assertEqual(result["revision"], workspace.revision)
        self.assertTrue(result["coalesced"])
        runner_request.assert_called_once()
        self.assertEqual(runner_request.call_args.args[2]["files"], workspace.files)
        self.assertTrue(AuditLog.objects.filter(
            user=owner,
            action="ai_agent.runner_workspace_synced",
            target_id=str(workspace.id),
            metadata__revision=workspace.revision,
            metadata__coalesced=True,
        ).exists())

    @patch("api.views._runner_request", return_value=(None, {"error": "unavailable"}))
    def test_agent_runner_sync_job_fails_closed_for_worker_retry(self, runner_request):
        owner = User.objects.create_user(username="agent-runner-sync-failure")
        workspace = CodeWorkspace.objects.create(owner=owner, name="Runner unavailable")
        job = BackgroundJob.objects.create(
            kind="ai_agent_runner_sync",
            payload={
                "workspace_id": workspace.id,
                "owner_id": owner.id,
                "requested_revision": workspace.revision,
            },
            idempotency_key=f"agent-runner-sync:{workspace.id}:{workspace.revision}",
        )

        with self.assertRaisesMessage(RuntimeError, "Runner workspace synchronization failed."):
            execute(job)

        self.assertFalse(AuditLog.objects.filter(
            user=owner,
            action="ai_agent.runner_workspace_synced",
            target_id=str(workspace.id),
        ).exists())

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


    def _create_stale_agent_verification(self, *, task_status="running", evidence=None):
        owner = User.objects.create_user(username=f"agent-verify-recovery-{task_status}")
        workspace = CodeWorkspace.objects.create(owner=owner, name="Verification recovery workspace")
        run = AgentRun.objects.create(
            owner=owner,
            workspace=workspace,
            goal="Recover interrupted Runner verification",
            status="running",
            idempotency_key=f"agent-verify-recovery-{task_status}-01",
            workspace_revision=workspace.revision,
        )
        job = BackgroundJob.objects.create(
            kind="ai_agent_task_verify",
            payload={
                "run_id": run.id,
                "task_id": 1,
                "workspace_id": workspace.id,
                "owner_id": owner.id,
                "revision": workspace.revision,
                "sync_job_id": 9001,
                "patch_audit_id": 9002,
                "profile": "python_pytest",
                "runner_execution_id": "",
            },
            status="running",
            attempts=1,
            max_attempts=1,
            locked_at=timezone.now() - timedelta(minutes=15),
            locked_by="worker:expired-lease",
        )
        job.payload = {**job.payload, "task_id": 1, "runner_execution_id": f"agent-{job.id}"}
        job.save(update_fields=["payload"])
        task_evidence = evidence or {"state": "queued", "iteration": 1, "history": []}
        if evidence is not None:
            task_evidence = {
                **evidence,
                "revision": workspace.revision,
                "verification_job_id": job.id,
                "runner_job_id": f"agent-{job.id}",
            }
        task = AgentTask.objects.create(
            run=run,
            key="implement",
            title="Implement feature",
            acceptance_criteria=["Tests pass"],
            status=task_status,
            execution_job=job,
            execution_revision=workspace.revision,
            execution_profile="python_pytest",
            execution_attempts=1,
            execution_evidence=task_evidence,
        )
        job.payload = {**job.payload, "task_id": task.id}
        job.save(update_fields=["payload"])
        return owner, workspace, run, task, job

    def test_expired_single_attempt_runner_lease_fails_closed_and_queues_resync(self):
        owner, workspace, run, task, job = self._create_stale_agent_verification()

        recover_stale_jobs()

        job.refresh_from_db()
        task.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertIsNotNone(job.dead_lettered_at)
        self.assertEqual(task.status, "failed")
        self.assertEqual(task.execution_evidence["state"], "infrastructure_failed")
        self.assertEqual(task.execution_evidence["verification_job_id"], job.id)
        self.assertIsNotNone(task.execution_evidence["resync_job_id"])
        resync = BackgroundJob.objects.get(pk=task.execution_evidence["resync_job_id"])
        self.assertEqual(resync.kind, "ai_agent_runner_sync")
        self.assertEqual(resync.payload["workspace_id"], workspace.id)
        self.assertEqual(run.status, "failed")
        self.assertTrue(AuditLog.objects.filter(
            user=owner,
            action="agent_task.runner_verification_exhausted",
            target_id=str(task.id),
            metadata__verification_job_id=job.id,
        ).exists())

    def test_interrupted_task_finalization_is_not_promoted_to_success_on_recovery(self):
        passed_evidence = {
            "state": "passed",
            "profile": "python_pytest",
            "iteration": 1,
            "history": [],
            "output_persisted": False,
        }
        owner, workspace, run, task, job = self._create_stale_agent_verification(
            task_status="awaiting_approval",
            evidence=passed_evidence,
        )

        recover_stale_jobs()

        task.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(task.status, "failed")
        self.assertEqual(task.execution_evidence["state"], "finalization_failed")
        self.assertEqual(task.execution_evidence["runner_job_id"], f"agent-{job.id}")
        self.assertIsNotNone(task.execution_evidence["resync_job_id"])
        self.assertEqual(run.status, "failed")
        self.assertTrue(AuditLog.objects.filter(
            user=owner,
            action="agent_task.runner_job_finalization_failed",
            target_id=str(task.id),
            metadata__verification_job_id=job.id,
            metadata__finalization_interrupted=True,
        ).exists())


    def test_stale_recovery_tolerates_non_object_job_payloads(self):
        malformed_plan = BackgroundJob.objects.create(
            kind="ai_agent_plan",
            payload=["invalid", "json-object-shape"],
            status="running",
            attempts=1,
            max_attempts=3,
            locked_at=timezone.now() - STALE_AFTER - timedelta(seconds=1),
            locked_by="worker:malformed-plan",
        )
        malformed_export = BackgroundJob.objects.create(
            kind="data_export",
            payload="invalid-json-object-shape",
            status="running",
            attempts=1,
            max_attempts=3,
            locked_at=timezone.now() - STALE_AFTER - timedelta(seconds=1),
            locked_by="worker:malformed-export",
        )

        recover_stale_jobs()

        malformed_plan.refresh_from_db()
        malformed_export.refresh_from_db()
        self.assertEqual(malformed_plan.status, "queued")
        self.assertEqual(malformed_export.status, "queued")
        self.assertIsNone(malformed_plan.locked_at)
        self.assertIsNone(malformed_export.locked_at)
        self.assertEqual(malformed_plan.locked_by, "")
        self.assertEqual(malformed_export.locked_by, "")


    def test_stale_recovery_ignores_invalid_identifier_shapes_in_object_payloads(self):
        malformed_plan = BackgroundJob.objects.create(
            kind="ai_agent_plan",
            payload={"run_id": []},
            status="running",
            attempts=3,
            max_attempts=3,
            locked_at=timezone.now() - STALE_AFTER - timedelta(seconds=1),
            locked_by="worker:invalid-plan-id",
        )
        malformed_export = BackgroundJob.objects.create(
            kind="data_export",
            payload={"export_id": {}, "user_id": []},
            status="running",
            attempts=3,
            max_attempts=3,
            locked_at=timezone.now() - STALE_AFTER - timedelta(seconds=1),
            locked_by="worker:invalid-export-id",
        )

        recover_stale_jobs()

        malformed_plan.refresh_from_db()
        malformed_export.refresh_from_db()
        self.assertEqual(malformed_plan.status, "failed")
        self.assertEqual(malformed_export.status, "failed")
        self.assertIsNotNone(malformed_plan.dead_lettered_at)
        self.assertIsNotNone(malformed_export.dead_lettered_at)
        self.assertIsNone(malformed_plan.locked_at)
        self.assertIsNone(malformed_export.locked_at)


    def test_worker_finalizes_failed_plan_job_with_non_object_payload(self):
        job = BackgroundJob.objects.create(
            kind="ai_agent_plan",
            payload=["invalid", "json-object-shape"],
            status="queued",
            available_at=timezone.now() - timedelta(seconds=1),
            attempts=0,
            max_attempts=1,
        )

        Command().handle(once=True, sleep=0)

        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertIsNone(job.locked_at)
        self.assertEqual(job.locked_by, "")
        self.assertIsNotNone(job.dead_lettered_at)
        self.assertIn("TypeError", job.error)

    def test_worker_finalizes_failed_export_job_with_non_object_payload(self):
        job = BackgroundJob.objects.create(
            kind="data_export",
            payload="invalid-json-object-shape",
            status="queued",
            available_at=timezone.now() - timedelta(seconds=1),
            attempts=0,
            max_attempts=1,
        )

        Command().handle(once=True, sleep=0)

        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertIsNone(job.locked_at)
        self.assertEqual(job.locked_by, "")
        self.assertIsNotNone(job.dead_lettered_at)
        self.assertIn("AttributeError", job.error)
