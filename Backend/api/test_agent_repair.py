import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import AgentRun, AgentTask, AuditLog, BackgroundJob, CodeWorkspace


class AgentTaskRepairProposalTests(TestCase):
    def setUp(self):
        rate_limit_patcher = patch("api.security.consume_rate_limit", return_value=(True, None))
        rate_limit_patcher.start()
        self.addCleanup(rate_limit_patcher.stop)

        self.user = User.objects.create_user(username="repair-owner", password="StrongPassword123!")
        self.other = User.objects.create_user(username="repair-other", password="StrongPassword123!")
        self.workspace = CodeWorkspace.objects.create(
            owner=self.user,
            name="Repair workspace",
            language="python",
            files={"main.py": "import missing_dependency\n"},
        )
        self.run = AgentRun.objects.create(
            owner=self.user,
            workspace=self.workspace,
            goal="Repair the failed task safely",
            status="failed",
            approved_at=timezone.now(),
            workspace_revision=self.workspace.revision - 1,
            idempotency_key="repair-proposal-run-001",
        )
        self.task = AgentTask.objects.create(
            run=self.run,
            key="repair_import",
            title="Repair missing import",
            description="Resolve the import failure without unrelated changes.",
            agent_role="backend",
            status="failed",
            acceptance_criteria=["The module imports successfully."],
            execution_revision=self.workspace.revision,
            execution_profile="python_pytest",
        )
        self.sync_job = BackgroundJob.objects.create(
            kind="ai_agent_runner_sync",
            status="succeeded",
            payload={
                "workspace_id": self.workspace.id,
                "owner_id": self.user.id,
                "requested_revision": self.workspace.revision,
            },
            result={"status": "synced", "revision": self.workspace.revision},
            idempotency_key="repair-sync-001",
        )
        AuditLog.objects.create(
            user=self.user,
            action="ai_agent.runner_workspace_synced",
            target_type="CodeWorkspace",
            target_id=str(self.workspace.id),
            metadata={"job_id": self.sync_job.id, "revision": self.workspace.revision},
        )
        self.patch_audit = AuditLog.objects.create(
            user=self.user,
            action="ai_agent.patch_applied",
            target_type="CodeWorkspace",
            target_id=str(self.workspace.id),
            metadata={
                "previous_revision": self.workspace.revision - 1,
                "revision": self.workspace.revision,
                "approval": "signed_single_use_revision_bound",
                "agent_run_id": self.run.id,
                "agent_task_id": self.task.id,
            },
        )
        self.evidence = {
            "state": "failed",
            "profile": "python_pytest",
            "revision": self.workspace.revision,
            "verification_job_id": None,
            "sync_job_id": self.sync_job.id,
            "patch_audit_id": self.patch_audit.id,
            "runner_job_id": "agent-runner-001",
            "runner_status": "failed",
            "exit_code": 1,
            "duration_ms": 125,
            "output_persisted": False,
            "diagnostic_signals": [{"code": "python_import_error", "source": "stderr"}],
            "repair_analysis": {
                "schema_version": 1,
                "status": "review_required",
                "diagnostic_code": "python_import_error",
                "confidence": "moderate",
                "probable_cause": "A Python import could not be resolved.",
                "recommended_actions": ["Check the dependency declaration."],
                "verification_checklist": ["Re-run the same test profile."],
                "workspace_mutated": False,
                "patch_generated": False,
                "execution_requested": False,
                "human_review_required": True,
            },
        }
        self.verification_job = BackgroundJob.objects.create(
            kind="ai_agent_task_verify",
            status="succeeded",
            payload={
                "run_id": self.run.id,
                "task_id": self.task.id,
                "owner_id": self.user.id,
                "workspace_id": self.workspace.id,
                "revision": self.workspace.revision,
                "profile": "python_pytest",
                "sync_job_id": self.sync_job.id,
                "patch_audit_id": self.patch_audit.id,
            },
            result={"status": "verification_failed", "task_id": self.task.id, "evidence": self.evidence},
            idempotency_key="repair-verify-001",
        )
        self.evidence["verification_job_id"] = self.verification_job.id
        self.verification_job.result = {
            "status": "verification_failed",
            "task_id": self.task.id,
            "evidence": self.evidence,
        }
        self.verification_job.save(update_fields=["result"])
        self.task.execution_job = self.verification_job
        self.task.execution_evidence = self.evidence
        self.task.save(update_fields=["execution_job", "execution_evidence", "updated_at"])

        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.url = f"/api/ai/agent/runs/{self.run.id}/tasks/{self.task.id}/repair-proposal/"

    def proposal(self):
        return {
            "type": "patch_proposal",
            "summary": "Correct the missing module import",
            "root_cause": "The source imports a module that is not present in the workspace.",
            "confidence": 0.72,
            "affected_files": ["main.py"],
            "changes": [{
                "path": "main.py",
                "operation": "modify",
                "reason": "Replace the unresolved import with the task's supported local implementation.",
                "content": "def load_value():\n    return 1\n",
            }],
            "test_plan": ["python -m pytest -q"],
            "risks": ["The intended dependency contract should be confirmed during review."],
            "requires_approval": True,
        }

    @patch("api.views._consume_usage", return_value=(True, 1, 100, "pro"))
    @patch("api.ai_engine._call_provider")
    def test_generates_task_bound_reviewable_proposal_without_mutating_workspace(self, provider, usage):
        provider.return_value = (json.dumps(self.proposal()), None)
        original_files = dict(self.workspace.files)
        original_revision = self.workspace.revision

        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "proposal_ready")
        self.assertTrue(response.data["approval_token"])
        self.assertEqual(response.data["proposal"]["type"], "patch_proposal")
        self.assertTrue(response.data["workspace_mutated"] is False)
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.files, original_files)
        self.assertEqual(self.workspace.revision, original_revision)
        audit = AuditLog.objects.get(action="agent_task.repair_proposal_generated")
        self.assertEqual(audit.metadata["workspace_revision"], original_revision)
        self.assertTrue(audit.metadata["human_approval_required"])
        usage.assert_called_once()
        provider.assert_called_once()

    @patch("api.views._consume_usage", return_value=(True, 1, 100, "pro"))
    @patch("api.ai_engine._call_provider")
    def test_approved_repair_proposal_uses_existing_revision_bound_apply_and_sync_outbox(self, provider, usage):
        provider.return_value = (json.dumps(self.proposal()), None)
        proposal_response = self.client.post(self.url, {}, format="json")
        self.assertEqual(proposal_response.status_code, 200)

        apply_response = self.client.post(
            "/api/ai/agent/apply/",
            {"approval_token": proposal_response.data["approval_token"]},
            format="json",
        )
        self.assertEqual(apply_response.status_code, 200)
        self.assertEqual(apply_response.data["status"], "applied_sync_pending")
        self.workspace.refresh_from_db()
        self.assertEqual(
            self.workspace.files["main.py"],
            "def load_value():\n    return 1\n",
        )
        applied = AuditLog.objects.filter(
            action="ai_agent.patch_applied",
            target_id=str(self.workspace.id),
            metadata__agent_run_id=self.run.id,
            metadata__agent_task_id=self.task.id,
            metadata__approval="signed_single_use_revision_bound",
        ).latest("created_at")
        self.assertEqual(applied.metadata["revision"], self.workspace.revision)
        self.assertTrue(BackgroundJob.objects.filter(
            kind="ai_agent_runner_sync",
            payload__workspace_id=self.workspace.id,
            payload__requested_revision=self.workspace.revision,
        ).exists())

    @patch("api.views._consume_usage", return_value=(True, 1, 100, "pro"))
    @patch("api.ai_engine._call_provider")
    def test_rejects_provider_output_that_is_not_a_complete_patch_contract(self, provider, usage):
        invalid = self.proposal()
        invalid["requires_approval"] = False
        provider.return_value = (json.dumps(invalid), None)

        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.data["code"], "repair_proposal_rejected")
        self.assertFalse(AuditLog.objects.filter(action="agent_task.repair_proposal_generated").exists())
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.files["main.py"], "import missing_dependency\n")

    def test_rejects_other_users(self):
        self.client.force_authenticate(self.other)
        response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, 404)

    def test_rejects_infrastructure_failures_and_stale_workspace_evidence(self):
        self.evidence["state"] = "infrastructure_failed"
        self.task.execution_evidence = self.evidence
        self.task.save(update_fields=["execution_evidence"])
        response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, 409)

        self.evidence["state"] = "failed"
        self.workspace.files = {"main.py": "print('new revision')\n"}
        self.workspace.save(update_fields=["files"])
        response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, 409)

    @patch("api.views._consume_usage", return_value=(True, 1, 100, "pro"))
    @patch("api.ai_engine._call_provider")
    def test_rechecks_workspace_revision_after_provider_call(self, provider, usage):
        def change_workspace(*args, **kwargs):
            self.workspace.refresh_from_db()
            self.workspace.files = {"main.py": "print('changed during generation')\n"}
            self.workspace.save(update_fields=["files"])
            return json.dumps(self.proposal()), None

        provider.side_effect = change_workspace
        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "stale_repair_context")
        self.assertFalse(AuditLog.objects.filter(action="agent_task.repair_proposal_generated").exists())

    @patch("api.views._consume_usage", return_value=(True, 1, 100, "pro"))
    @patch("api.ai_engine._call_provider", return_value=("", '{"code":"AI_CONFIG_ERROR"}'))
    def test_provider_unavailable_never_falls_back_to_fake_patch(self, provider, usage):
        response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.data.get("approval_token"))
        self.assertFalse(AuditLog.objects.filter(action="agent_task.repair_proposal_generated").exists())

    def test_rejects_patch_that_targets_missing_or_existing_paths_incorrectly(self):
        from .agent_repair import _valid_repair_proposal

        invalid_modify = self.proposal()
        invalid_modify["changes"][0]["path"] = "not-present.py"
        invalid_modify["affected_files"] = ["not-present.py"]
        self.assertIsNone(_valid_repair_proposal(json.dumps(invalid_modify), self.workspace.files))

        invalid_create = self.proposal()
        invalid_create["changes"][0]["operation"] = "create"
        self.assertIsNone(_valid_repair_proposal(json.dumps(invalid_create), self.workspace.files))

    def test_only_complete_unredacted_source_files_are_repairable(self):
        from .agent_repair import _repairable_sample_paths

        full_source = "print('safe')\n"
        self.assertEqual(
            _repairable_sample_paths(
                {"main.py": full_source, "large.py": "x" * 1801, "secret.py": "TOKEN=real"},
                {"main.py": full_source, "large.py": "x" * 1800, "secret.py": "TOKEN=[REDACTED]"},
            ),
            {"main.py"},
        )

    def test_rejects_unknown_or_generic_diagnostic_codes(self):
        self.evidence["diagnostic_signals"] = [{"code": "command_failed", "source": "stderr"}]
        self.evidence["repair_analysis"]["diagnostic_code"] = "command_failed"
        self.verification_job.result = {
            "status": "verification_failed",
            "task_id": self.task.id,
            "evidence": self.evidence,
        }
        self.verification_job.save(update_fields=["result"])
        self.task.execution_evidence = self.evidence
        self.task.save(update_fields=["execution_evidence"])

        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "repair_evidence_not_eligible")

    def test_rejects_low_confidence_repair_diagnosis_before_provider_call(self):
        self.evidence["repair_analysis"]["confidence"] = "low"
        self.verification_job.result = {
            "status": "verification_failed",
            "task_id": self.task.id,
            "evidence": self.evidence,
        }
        self.verification_job.save(update_fields=["result"])
        self.task.execution_evidence = self.evidence
        self.task.save(update_fields=["execution_evidence"])

        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "repair_evidence_not_eligible")

    def test_rejects_modifying_a_file_that_was_not_in_the_provider_snapshot(self):
        from .agent_repair import _valid_repair_proposal

        self.assertIsNone(_valid_repair_proposal(
            json.dumps(self.proposal()),
            self.workspace.files,
            sampled_paths=set(),
        ))
