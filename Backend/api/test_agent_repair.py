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
        self.assertEqual(
            audit.metadata["repair_engine_validation"]["mode"],
            "revision_bound_unified_diff",
        )
        self.assertEqual(
            audit.metadata["repair_engine_validation"]["secret_scan"],
            "passed",
        )
        self.assertTrue(audit.metadata["repair_engine_validation"]["patch_sha256"])
        self.assertTrue(audit.metadata["repair_engine_validation"]["snapshot_sha256"])
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
    @patch("api.ai_engine._call_provider")
    def test_rejects_changed_verification_evidence_even_when_workspace_revision_is_unchanged(self, provider, usage):
        def change_evidence(*args, **kwargs):
            self.task.refresh_from_db()
            changed = dict(self.task.execution_evidence)
            changed_analysis = dict(changed["repair_analysis"])
            changed_analysis["probable_cause"] = "A different diagnosis was persisted during generation."
            changed["repair_analysis"] = changed_analysis
            self.task.execution_evidence = changed
            self.task.save(update_fields=["execution_evidence"])
            self.verification_job.refresh_from_db()
            self.verification_job.result = {
                "status": "verification_failed",
                "task_id": self.task.id,
                "evidence": changed,
            }
            self.verification_job.save(update_fields=["result"])
            return json.dumps(self.proposal()), None

        provider.side_effect = change_evidence
        original_revision = self.workspace.revision

        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "stale_repair_context")
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.revision, original_revision)
        self.assertFalse(AuditLog.objects.filter(action="agent_task.repair_proposal_generated").exists())

    @patch("api.views._consume_usage", return_value=(True, 1, 100, "pro"))
    @patch("api.ai_engine._call_provider", return_value=("", '{"code":"AI_CONFIG_ERROR"}'))
    def test_provider_unavailable_never_falls_back_to_fake_patch(self, provider, usage):
        response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.data.get("approval_token"))
        self.assertFalse(AuditLog.objects.filter(action="agent_task.repair_proposal_generated").exists())

    def test_json_identity_fields_require_strict_integer_values(self):
        from copy import deepcopy

        for field in ("run_id", "task_id", "owner_id", "workspace_id"):
            for malformed in (True, str(getattr(self, {"run_id": "run", "task_id": "task", "owner_id": "user", "workspace_id": "workspace"}[field]).id)):
                with self.subTest(payload_field=field, malformed=repr(malformed)):
                    payload = dict(self.verification_job.payload)
                    payload[field] = malformed
                    self.verification_job.payload = payload
                    self.verification_job.save(update_fields=["payload"])
                    response = self.client.post(self.url, {}, format="json")
                    self.assertEqual(response.status_code, 409)
                    self.assertEqual(response.data["code"], "repair_evidence_not_eligible")
                    self.verification_job.payload = dict(payload, **{field: getattr(self, {"run_id": "run", "task_id": "task", "owner_id": "user", "workspace_id": "workspace"}[field]).id})
                    self.verification_job.save(update_fields=["payload"])

        for field, malformed in (("verification_job_id", True), ("verification_job_id", str(self.verification_job.id))):
            with self.subTest(evidence_field=field, malformed=repr(malformed)):
                evidence = deepcopy(self.evidence)
                evidence[field] = malformed
                self.task.execution_evidence = evidence
                self.task.save(update_fields=["execution_evidence"])
                self.verification_job.result = {"status": "verification_failed", "task_id": self.task.id, "evidence": evidence}
                self.verification_job.save(update_fields=["result"])
                response = self.client.post(self.url, {}, format="json")
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.data["code"], "repair_evidence_not_eligible")

    def test_sync_job_payload_identity_requires_strict_integer_values(self):
        for field, expected in (
            ("workspace_id", self.workspace.id),
            ("owner_id", self.user.id),
            ("requested_revision", self.workspace.revision),
        ):
            for malformed in (True, str(expected)):
                with self.subTest(sync_payload_field=field, malformed=repr(malformed)):
                    payload = dict(self.sync_job.payload)
                    payload[field] = malformed
                    self.sync_job.payload = payload
                    self.sync_job.save(update_fields=["payload"])
                    response = self.client.post(self.url, {}, format="json")
                    self.assertEqual(response.status_code, 409)
                    self.assertEqual(response.data["code"], "repair_evidence_not_eligible")
                    payload[field] = expected
                    self.sync_job.payload = payload
                    self.sync_job.save(update_fields=["payload"])

    def test_malformed_persisted_evidence_references_fail_closed(self):
        from copy import deepcopy

        for field in ("sync_job_id", "patch_audit_id"):
            for malformed in ([], {}, "not-an-id", True):
                with self.subTest(field=field, malformed=repr(malformed)):
                    evidence = deepcopy(self.evidence)
                    evidence[field] = malformed
                    self.task.execution_evidence = evidence
                    self.task.save(update_fields=["execution_evidence"])
                    self.verification_job.result = {
                        "status": "verification_failed",
                        "task_id": self.task.id,
                        "evidence": evidence,
                    }
                    self.verification_job.save(update_fields=["result"])

                    response = self.client.post(self.url, {}, format="json")

                    self.assertEqual(response.status_code, 409)
                    self.assertEqual(response.data["code"], "repair_evidence_not_eligible")

    def test_rejects_unknown_provider_fields_before_signing(self):
        from .agent_repair import _valid_repair_proposal

        unknown_top_level = self.proposal()
        unknown_top_level["provider_metadata"] = {"untrusted": "value\x00"}
        self.assertIsNone(_valid_repair_proposal(
            json.dumps(unknown_top_level),
            self.workspace.files,
        ))

        unknown_change_field = self.proposal()
        unknown_change_field["changes"][0]["provider_metadata"] = {"untrusted": "value\x00"}
        self.assertIsNone(_valid_repair_proposal(
            json.dumps(unknown_change_field),
            self.workspace.files,
        ))

    def test_rejects_database_unsafe_unicode_in_all_repair_metadata(self):
        from .agent_repair import _valid_repair_proposal

        invalid_values = ("unsafe\x00text", "unpaired-\ud800-surrogate")
        for field in ("summary", "root_cause", "affected_files", "test_plan", "risks"):
            for invalid in invalid_values:
                with self.subTest(field=field, invalid=repr(invalid)):
                    proposal = self.proposal()
                    if field in {"affected_files", "test_plan", "risks"}:
                        proposal[field] = [invalid]
                    else:
                        proposal[field] = invalid
                    self.assertIsNone(_valid_repair_proposal(
                        json.dumps(proposal),
                        self.workspace.files,
                    ))

        for invalid in invalid_values:
            with self.subTest(field="change.reason", invalid=repr(invalid)):
                proposal = self.proposal()
                proposal["changes"][0]["reason"] = invalid
                self.assertIsNone(_valid_repair_proposal(
                    json.dumps(proposal),
                    self.workspace.files,
                ))

    def test_rejects_database_unsafe_nul_patch_content_before_approval_or_apply(self):
        from .ai_engine import _agent_parse_patch
        from .agent_repair import _valid_repair_proposal

        proposal = self.proposal()
        proposal["changes"][0]["content"] = "def load_value():\n    return 1\x00\n"
        encoded = json.dumps(proposal)

        self.assertIsNone(_agent_parse_patch(encoded))
        self.assertIsNone(_valid_repair_proposal(encoded, self.workspace.files))

    def test_rejects_patch_that_targets_missing_or_existing_paths_incorrectly(self):
        from .agent_repair import _valid_repair_proposal

        invalid_modify = self.proposal()
        invalid_modify["changes"][0]["path"] = "not-present.py"
        invalid_modify["affected_files"] = ["not-present.py"]
        self.assertIsNone(_valid_repair_proposal(json.dumps(invalid_modify), self.workspace.files))

        invalid_create = self.proposal()
        invalid_create["changes"][0]["operation"] = "create"
        self.assertIsNone(_valid_repair_proposal(json.dumps(invalid_create), self.workspace.files))

    def test_repair_proposals_cannot_change_ci_deployment_or_runner_security_controls(self):
        from .agent_repair import _repair_path_is_allowed

        for path in (
            ".github/workflows/deploy.yml",
            ".github/actions/custom/action.yml",
            ".github/CODEOWNERS",
            "nested/.github/workflows/deploy.yml",
            "project/.config/.github/workflows/release.yml",
            "nested/.gitlab/pipeline.yml",
            "project/.circleci/config.yml",
            "workspace/.buildkite/pipeline.yml",
            ".gitlab-ci.yml",
            "docker-compose.yml",
            "Dockerfile",
            "render.yaml",
            "pnpm-lock.yaml",
            "bun.lock",
            "npm-shrinkwrap.json",
            "Dockerfile.ci",
            "poetry.lock",
            "Runner/seccomp-bwrap.json",
            "Runner/apparmor/developer-os-runner",
            ".env",
            ".config/gh/hosts.yml",
            "project/.config/azure/accessTokens.json",
            "secrets/service-account.json",
            "nested/.ssh/id_ed25519",
            "project/.aws/credentials",
            ".terraform/terraform.tfstate",
            ".npmrc",
        ):
            with self.subTest(path=path):
                self.assertFalse(_repair_path_is_allowed(path))

        for path in ("src/main.py", "tests/test_main.py", "README.md"):
            with self.subTest(path=path):
                self.assertTrue(_repair_path_is_allowed(path))

    def test_repair_proposals_cannot_create_credential_store_paths(self):
        from .agent_repair import _valid_repair_proposal

        proposal = self.proposal()
        proposal["affected_files"] = [".config/gh/hosts.yml"]
        proposal["changes"] = [{
            "path": ".config/gh/hosts.yml",
            "operation": "create",
            "reason": "Attempted credential-store creation must be rejected.",
            "content": "github.com:\n  oauth_token: fake-token\n",
        }]

        self.assertIsNone(_valid_repair_proposal(
            json.dumps(proposal),
            self.workspace.files,
            sampled_paths=set(),
        ))

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

    def test_malformed_repair_evidence_values_fail_closed_without_server_errors(self):
        from copy import deepcopy

        cases = (
            ("runner_status", [], None),
            ("repair_analysis", None, None),
            ("confidence", [], "repair_analysis"),
            ("diagnostic_code", [], "repair_analysis"),
            ("signal_code", [], "diagnostic_signals"),
            ("signal_source", [], "diagnostic_signals"),
        )
        for field, malformed, container in cases:
            with self.subTest(field=field, malformed=repr(malformed)):
                evidence = deepcopy(self.evidence)
                if field == "runner_status":
                    evidence[field] = malformed
                elif field == "repair_analysis":
                    evidence[field] = malformed
                elif container == "repair_analysis":
                    evidence[container][field] = malformed
                else:
                    evidence[container][0][
                        "code" if field == "signal_code" else "source"
                    ] = malformed
                self.task.execution_evidence = evidence
                self.task.save(update_fields=["execution_evidence"])
                self.verification_job.result = {
                    "status": "verification_failed",
                    "task_id": self.task.id,
                    "evidence": evidence,
                }
                self.verification_job.save(update_fields=["result"])

                response = self.client.post(self.url, {}, format="json")

                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.data["code"], "repair_evidence_not_eligible")

    def test_malformed_persisted_diagnostic_signals_fail_closed(self):
        for malformed in (None, {}, "python_import_error", 7):
            with self.subTest(diagnostic_signals=malformed):
                evidence = dict(self.evidence)
                evidence["diagnostic_signals"] = malformed
                self.task.execution_evidence = evidence
                self.task.save(update_fields=["execution_evidence"])
                self.verification_job.result = {
                    "status": "verification_failed",
                    "task_id": self.task.id,
                    "evidence": evidence,
                }
                self.verification_job.save(update_fields=["result"])

                response = self.client.post(self.url, {}, format="json")

                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.data["code"], "repair_evidence_not_eligible")

    def test_nullable_execution_job_relation_is_safe_under_row_lock(self):
        self.task.execution_job = None
        self.task.save(update_fields=["execution_job"])
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

    def test_provider_excluded_credential_stores_are_not_repairable(self):
        from .agent_repair import _repairable_sample_paths
        from .ai_engine import _agent_context_file_samples, _provider_safe_context_path

        files = {
            "main.py": "print('safe')\n",
            ".config/gh/hosts.yml": "github.com:\n  user: developer\n",
        }
        sampled = _agent_context_file_samples(files)
        provider_files = {
            path: content
            for path, content in sampled.items()
            if _provider_safe_context_path(path)
        }

        self.assertIn(".config/gh/hosts.yml", sampled)
        self.assertNotIn(".config/gh/hosts.yml", provider_files)
        self.assertEqual(
            _repairable_sample_paths(files, provider_files),
            {"main.py"},
        )

    def test_rejects_modifying_a_file_that_was_not_in_the_provider_snapshot(self):
        from .agent_repair import _valid_repair_proposal

        self.assertIsNone(_valid_repair_proposal(
            json.dumps(self.proposal()),
            self.workspace.files,
            sampled_paths=set(),
        ))

    def test_live_repair_proposal_validation_uses_revision_bound_engine_for_mixed_changes(self):
        from .agent_repair import _validate_repair_proposal_with_engine

        proposal = self.proposal()
        proposal["affected_files"] = ["main.py", "README.md"]
        proposal["changes"].append({
            "path": "README.md",
            "operation": "create",
            "reason": "Document the approved repair behavior.",
            "content": "# Repair notes\n",
        })

        result = _validate_repair_proposal_with_engine(
            proposal,
            dict(self.workspace.files),
            self.workspace.revision,
        )

        self.assertTrue(result["validated"])
        self.assertEqual(result["validation_mode"], "revision_bound_unified_diff")
        self.assertEqual(result["changed_paths"], ["main.py", "README.md"])
        self.assertEqual(result["secret_scan"], "passed")
        self.assertTrue(result["patch_sha256"])
        self.assertTrue(result["snapshot_sha256"])

    def test_live_repair_engine_rejects_secret_material_in_modified_and_created_files(self):
        from .agent_repair import _validate_repair_proposal_with_engine
        from .repair_engine import RepairContractError

        modified = self.proposal()
        modified["changes"][0]["content"] = "API_KEY = 'sk_live_1234567890abcdef'\n"
        with self.assertRaises(RepairContractError):
            _validate_repair_proposal_with_engine(
                modified,
                dict(self.workspace.files),
                self.workspace.revision,
            )

        created = self.proposal()
        created["affected_files"] = ["README.md"]
        created["changes"] = [{
            "path": "README.md",
            "operation": "create",
            "reason": "The created file must be scanned too.",
            "content": "API_KEY = 'sk_live_1234567890abcdef'\n",
        }]
        with self.assertRaises(RepairContractError):
            _validate_repair_proposal_with_engine(
                created,
                dict(self.workspace.files),
                self.workspace.revision,
            )

