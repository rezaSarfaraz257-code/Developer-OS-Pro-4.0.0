from datetime import datetime, timedelta, timezone

from django.test import SimpleTestCase

from .repair_engine import (
    RepairContractError,
    analyze_root_cause,
    classify_execution_failure,
    normalize_runner_evidence,
    scan_patch_for_secrets,
    validate_revision_bound_approval,
    validate_unified_patch,
    evaluate_repair_loop,
    verification_passed,
)


class RunnerEvidenceContractTests(SimpleTestCase):
    def evidence(self, **overrides):
        value = {
            "schema_version": 1,
            "evidence_origin": "runner",
            "execution_id": "exec-001",
            "workspace_id": 7,
            "workspace_revision": 12,
            "job_id": 33,
            "execution_status": "failed",
            "exit_code": 1,
            "termination_signal": None,
            "stdout_reference": "artifact://stdout/33",
            "stderr_reference": "artifact://stderr/33",
            "structured_errors": [{
                "code": "python_syntax_error",
                "message": "SyntaxError in module",
                "source_location": {"path": "src/main.py", "line": 14, "column": 2, "symbol": "load"},
                "evidence_reference": "artifact://errors/33/0",
            }],
            "stack_trace": 'File "src/main.py", line 14, in load',
            "source_location": {"path": "src/main.py", "line": 14, "column": 2},
            "execution_environment": {"runner_version": "1.2.0", "runtime": "python", "runtime_version": "3.12"},
            "resource_usage": {"duration_ms": 1200, "cpu_time_ms": 900, "peak_memory_bytes": 1024, "output_bytes": 120},
            "timestamps": {
                "started_at": "2026-11-27T10:00:00Z",
                "finished_at": "2026-11-27T10:00:02Z",
            },
            "artifact_checksums": {"src/main.py": "a" * 64},
        }
        value.update(overrides)
        return value

    def normalize(self, payload=None):
        return normalize_runner_evidence(
            payload or self.evidence(),
            expected_execution_id="exec-001",
            expected_workspace_id=7,
            expected_workspace_revision=12,
            expected_job_id=33,
        )

    def test_normalizes_complete_runner_evidence_and_correlates_exact_identity(self):
        result = self.normalize()
        self.assertEqual(result["execution_id"], "exec-001")
        self.assertEqual(result["workspace_revision"], 12)
        self.assertEqual(result["execution_status"], "failed")
        self.assertEqual(result["artifact_checksums"]["src/main.py"], "a" * 64)

    def test_rejects_incomplete_extra_and_agent_origin_evidence(self):
        for payload in (
            {key: value for key, value in self.evidence().items() if key != "resource_usage"},
            {**self.evidence(), "agent_claim": "success"},
            self.evidence(evidence_origin="agent"),
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(RepairContractError):
                    self.normalize(payload)

    def test_rejects_boolean_workspace_revision_even_when_integer_value_matches(self):
        with self.assertRaises(RepairContractError):
            self.normalize(self.evidence(workspace_revision=True))

    def test_rejects_wrong_execution_workspace_job_or_revision(self):
        for field, value in (
            ("execution_id", "other"),
            ("workspace_id", 8),
            ("workspace_revision", 11),
            ("job_id", 34),
        ):
            with self.subTest(field=field):
                with self.assertRaises(RepairContractError):
                    self.normalize(self.evidence(**{field: value}))

    def test_rejects_boolean_exit_code_and_false_success(self):
        for payload in (
            self.evidence(execution_status="succeeded", exit_code=1),
            self.evidence(execution_status="failed", exit_code=0),
            self.evidence(exit_code=True),
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(RepairContractError):
                    self.normalize(payload)

    def test_rejects_unsafe_paths_and_invalid_resource_usage(self):
        bad_location = self.evidence(source_location={"path": "../secret", "line": 1})
        bad_usage = self.evidence(resource_usage={"peak_memory_bytes": -1})
        with self.assertRaises(RepairContractError):
            self.normalize(bad_location)
        with self.assertRaises(RepairContractError):
            self.normalize(bad_usage)

    def test_redacts_secret_patterns_from_persisted_error_and_stack_text(self):
        secret = "ghp_" + "A" * 30
        result = self.normalize(self.evidence(
            structured_errors=[{
                "code": "runtime_exception",
                "message": "provider returned " + secret,
                "source_location": None,
                "evidence_reference": None,
            }],
            stack_trace="failure " + secret,
        ))
        self.assertNotIn(secret, result["structured_errors"][0]["message"])
        self.assertNotIn(secret, result["stack_trace"])


class FailureIntelligenceTests(RunnerEvidenceContractTests):
    def test_classifies_failure_from_runner_evidence_not_agent_text(self):
        evidence = self.normalize()
        self.assertEqual(classify_execution_failure(evidence)["category"], "syntax")

    def test_classifies_infrastructure_and_resource_failures(self):
        infra = self.normalize(self.evidence(execution_status="infrastructure_failed", exit_code=None))
        timed = self.normalize(self.evidence(execution_status="timed_out", exit_code=None))
        self.assertEqual(classify_execution_failure(infra)["category"], "infrastructure")
        self.assertEqual(classify_execution_failure(timed)["category"], "resource_exhaustion")

    def test_root_cause_candidates_reference_runner_evidence_and_changed_paths(self):
        result = analyze_root_cause(self.normalize(), changed_paths={"src/main.py"})
        self.assertEqual(result["conclusion"], "candidate_causes_available")
        self.assertEqual(result["candidates"][0]["change_relation"], "changed_file")
        self.assertEqual(result["candidates"][0]["evidence_reference"], "artifact://errors/33/0")


    def test_classifies_all_supported_failure_families(self):
        cases = (
            ("python_type_error", "type"),
            ("dependency_module_not_found", "dependency"),
            ("build_compile_error", "build"),
            ("test_assertion_failure", "test"),
            ("runtime_exception", "runtime"),
            ("configuration_invalid", "configuration"),
            ("permission_access_denied", "permission"),
        )
        for code, expected in cases:
            evidence = self.normalize(self.evidence(structured_errors=[{
                "code": code, "message": "failure", "source_location": None,
                "evidence_reference": "artifact://errors/33/0",
            }]))
            with self.subTest(code=code):
                self.assertEqual(classify_execution_failure(evidence)["category"], expected)

    def test_rejects_secret_untrusted_and_traversing_artifact_references(self):
        for reference in (
            "https://example.com/log",
            "artifact://logs?token=secret",
            "artifact://../../private/key",
            "runner://logs/../private",
            "blob://logs//stderr",
        ):
            with self.subTest(reference=reference):
                with self.assertRaises(RepairContractError):
                    self.normalize(self.evidence(stdout_reference=reference))

    def test_unknown_failure_reports_uncertainty(self):
        evidence = self.normalize(self.evidence(structured_errors=[] , source_location=None))
        result = analyze_root_cause(evidence)
        self.assertEqual(result["conclusion"], "insufficient_evidence")
        self.assertIsNotNone(result["uncertainty"])

    def test_classifier_fails_closed_on_malformed_runner_payloads(self):
        for evidence in (
            {"evidence_origin": "runner", "execution_status": "failed", "structured_errors": None},
            {"evidence_origin": "runner", "execution_status": "unknown", "structured_errors": []},
            {"evidence_origin": "runner", "execution_status": "failed", "structured_errors": [{"code": []}]},
            {"evidence_origin": "runner", "execution_status": [], "structured_errors": []},
            {"evidence_origin": "runner", "execution_status": "succeeded", "exit_code": True, "structured_errors": []},
        ):
            with self.subTest(evidence=evidence):
                with self.assertRaises(RepairContractError):
                    classify_execution_failure(evidence)

    def test_root_cause_rejects_malformed_changed_path_collections(self):
        evidence = self.normalize()
        for changed_paths in ("src/main.py", None, ["../outside.py"]):
            with self.subTest(changed_paths=changed_paths):
                with self.assertRaises(RepairContractError):
                    analyze_root_cause(evidence, changed_paths=changed_paths)


class PatchAndApprovalValidationTests(SimpleTestCase):
    def patch(self):
        return (
            "diff --git a/src/main.py b/src/main.py\n"
            "--- a/src/main.py\n"
            "+++ b/src/main.py\n"
            "@@ -1 +1 @@\n"
            "-value = 1\n"
            "+value = 2\n"
        )

    def test_accepts_standard_git_index_metadata_but_rejects_mode_changes(self):
        indexed = self.patch().replace(
            "--- a/src/main.py",
            "index 0123456789abcdef..abcdef0123456789 100644\n--- a/src/main.py",
        )
        result = validate_unified_patch(
            indexed, target_revision="rev-12", expected_revision="rev-12",
            workspace_files={"src/main.py": "value = 1\n"},
        )
        self.assertTrue(result["validated"])
        mode_change = self.patch().replace(
            "diff --git a/src/main.py b/src/main.py\n",
            "diff --git a/src/main.py b/src/main.py\nold mode 100644\nnew mode 100755\n",
        )
        with self.assertRaises(RepairContractError):
            validate_unified_patch(
                mode_change, target_revision="rev-12", expected_revision="rev-12",
                workspace_files={"src/main.py": "value = 1\n"},
            )

    def test_validates_minimal_patch_against_exact_revision_and_existing_paths(self):
        result = validate_unified_patch(
            self.patch(),
            target_revision="rev-12",
            expected_revision="rev-12",
            workspace_files={"src/main.py": "value = 1\n"},
        )
        self.assertTrue(result["validated"])
        self.assertEqual(result["changed_paths"], ["src/main.py"])
        self.assertEqual(len(result["patch_sha256"]), 64)
        self.assertEqual(result["source_context"], "matched")

    def test_rejects_stale_revision_protected_path_and_secret_patch(self):
        with self.assertRaises(RepairContractError):
            validate_unified_patch(self.patch(), target_revision="old", expected_revision="new", workspace_files={"src/main.py": ""})
        protected = self.patch().replace("src/main.py", ".github/workflows/ci.yml")
        with self.assertRaises(RepairContractError):
            validate_unified_patch(protected, target_revision="r", expected_revision="r", workspace_files={".github/workflows/ci.yml": ""})
        secret_patch = self.patch().replace("+value = 2", "+api_key = 'super-secret-value-123'")
        with self.assertRaises(RepairContractError):
            validate_unified_patch(secret_patch, target_revision="r", expected_revision="r", workspace_files={"src/main.py": "value = 1\n"})
        for protected_name in (".env.staging", "config/service.pem", "nested/secrets/token.txt"):
            protected_patch = self.patch().replace("src/main.py", protected_name)
            with self.assertRaises(RepairContractError):
                validate_unified_patch(protected_patch, target_revision="r", expected_revision="r", workspace_files={protected_name: "value = 1\n"})

    def test_hunk_lines_that_resemble_file_headers_remain_hunk_content(self):
        patch = (
            "diff --git a/src/main.py b/src/main.py\n"
            "--- a/src/main.py\n"
            "+++ b/src/main.py\n"
            "@@ -1 +1 @@\n"
            "---- old\n"
            "++++ new\n"
        )
        result = validate_unified_patch(
            patch, target_revision="r", expected_revision="r",
            workspace_files={"src/main.py": "--- old\n"},
        )
        self.assertTrue(result["validated"])

    def test_rejects_patch_when_git_header_or_source_context_is_forged(self):
        wrong_git_path = self.patch().replace("diff --git a/src/main.py b/src/main.py", "diff --git a/src/other.py b/src/other.py")
        with self.assertRaises(RepairContractError):
            validate_unified_patch(wrong_git_path, target_revision="r", expected_revision="r", workspace_files={"src/main.py": "value = 1\n"})
        wrong_context = self.patch().replace("-value = 1", "-value = 999")
        with self.assertRaises(RepairContractError):
            validate_unified_patch(wrong_context, target_revision="r", expected_revision="r", workspace_files={"src/main.py": "value = 1\n"})
        wrong_count = self.patch().replace("@@ -1 +1 @@", "@@ -1,2 +1,2 @@")
        with self.assertRaises(RepairContractError):
            validate_unified_patch(wrong_count, target_revision="r", expected_revision="r", workspace_files={"src/main.py": "value = 1\n"})

        wrong_new_offset = self.patch().replace("@@ -1 +1 @@", "@@ -1 +4 @@")
        with self.assertRaises(RepairContractError):
            validate_unified_patch(wrong_new_offset, target_revision="r", expected_revision="r", workspace_files={"src/main.py": "value = 1\n"})

    def test_secret_scanner_never_returns_secret_value(self):
        findings = scan_patch_for_secrets("+token = 'ghp_" + "A" * 30 + "'")
        self.assertTrue(findings)
        self.assertNotIn("ghp_", str(findings))
        bearer = scan_patch_for_secrets("+Authorization: Bearer " + "B" * 30)
        unquoted = scan_patch_for_secrets("+access_token=" + "C" * 30)
        self.assertTrue(bearer)
        self.assertTrue(unquoted)
        self.assertNotIn("B" * 30, str(bearer))
        self.assertNotIn("C" * 30, str(unquoted))


    def test_scanner_checks_added_lines_starting_with_multiple_plus_signs(self):
        findings = scan_patch_for_secrets("+++ token = 'ghp_" + "D" * 30 + "'")
        self.assertTrue(findings)

    def test_revision_bound_approval_expires_and_invalidates_on_revision_or_patch_change(self):
        now = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)
        approval = {
            "approved": True,
            "workspace_revision": "rev-12",
            "patch_sha256": "a" * 64,
            "approved_at": (now - timedelta(minutes=1)).isoformat(),
            "expires_at": (now + timedelta(minutes=5)).isoformat(),
        }
        self.assertTrue(validate_revision_bound_approval(approval, current_revision="rev-12", patch_sha256="a" * 64, now=now))
        self.assertFalse(validate_revision_bound_approval(approval, current_revision="rev-13", patch_sha256="a" * 64, now=now))
        self.assertFalse(validate_revision_bound_approval(approval, current_revision="rev-12", patch_sha256="b" * 64, now=now))
        self.assertFalse(validate_revision_bound_approval(approval, current_revision="rev-12", patch_sha256="a" * 64, now=now + timedelta(minutes=6)))
        expired_before_approval = {**approval, "expires_at": (now - timedelta(minutes=2)).isoformat()}
        self.assertFalse(validate_revision_bound_approval(expired_before_approval, current_revision="rev-12", patch_sha256="a" * 64, now=now))


class VerificationEvidenceTests(SimpleTestCase):
    def test_success_requires_runner_and_all_independent_check_evidence(self):
        result = {
            "schema_version": 1,
            "evidence_origin": "runner",
            "execution_id": "exec-verified-14",
            "workspace_id": 7,
            "job_id": 34,
            "workspace_revision": 14,
            "execution_status": "succeeded",
            "exit_code": 0,
            "checks": {
                "targeted_tests": {"status": "passed", "evidence_reference": "artifact://targeted", "execution_id": "exec-verified-14", "workspace_revision": 14},
                "regression_tests": {"status": "passed", "evidence_reference": "artifact://regression", "execution_id": "exec-verified-14", "workspace_revision": 14},
                "security": {"status": "passed", "evidence_reference": "artifact://security", "execution_id": "exec-verified-14", "workspace_revision": 14},
            },
        }
        self.assertTrue(verification_passed(result, expected_revision=14))
        result["checks"]["security"]["status"] = "failed"
        self.assertFalse(verification_passed(result, expected_revision=14))
        result["checks"]["security"]["status"] = "passed"
        result["checks"]["security"]["workspace_revision"] = 13
        self.assertFalse(verification_passed(result, expected_revision=14))
        result["checks"]["security"]["workspace_revision"] = 14
        result["checks"]["security"]["execution_id"] = "other-execution"
        self.assertFalse(verification_passed(result, expected_revision=14))
        result["checks"]["security"]["execution_id"] = "exec-verified-14"
        result["workspace_revision"] = 13
        self.assertFalse(verification_passed(result, expected_revision=14))
        result["workspace_revision"] = 14
        result["evidence_origin"] = "agent"
        self.assertFalse(verification_passed(result, expected_revision=14))
        result["evidence_origin"] = "runner"
        result["exit_code"] = True
        self.assertFalse(verification_passed(result, expected_revision=14))
        result["exit_code"] = 0
        result["workspace_revision"] = True
        self.assertFalse(verification_passed(result, expected_revision=1))
        self.assertFalse(verification_passed(result, expected_revision=True))
        self.assertFalse(verification_passed(result, expected_revision=None))
        self.assertFalse(verification_passed(result, expected_revision=-1))


class RepairLoopGuardTests(SimpleTestCase):
    def test_blocks_repeated_patch_and_enforces_attempt_budget(self):
        revision = "rev-12"
        repeated = "a" * 64
        other = "b" * 64
        history = [
            {"patch_sha256": repeated, "outcome": "applied_failed_verification", "workspace_revision": revision},
            {"patch_sha256": other, "outcome": "rolled_back", "workspace_revision": revision},
        ]
        self.assertEqual(
            evaluate_repair_loop(history, current_revision=revision, candidate_patch_sha256=repeated)["reason"],
            "repeated_patch",
        )
        self.assertEqual(
            evaluate_repair_loop(history, current_revision=revision, candidate_patch_sha256="c" * 64, max_attempts=2)["reason"],
            "attempt_limit_reached",
        )

    def test_new_revision_gets_a_fresh_budget_and_success_stops_repair(self):
        history = [
            {"patch_sha256": "a" * 64, "outcome": "verified_success", "workspace_revision": "rev-12"},
        ]
        self.assertTrue(evaluate_repair_loop(
            history, current_revision="rev-13", candidate_patch_sha256="b" * 64
        )["allowed"])
        self.assertEqual(
            evaluate_repair_loop(history, current_revision="rev-12", candidate_patch_sha256="b" * 64)["reason"],
            "revision_already_verified",
        )

    def test_malformed_history_fails_closed(self):
        invalid_records = (
            [{"patch_sha256": "not-a-hash", "outcome": "rolled_back", "workspace_revision": "rev-12"}],
            [{"patch_sha256": "a" * 64, "outcome": [], "workspace_revision": "rev-12"}],
        )
        for history in invalid_records:
            with self.subTest(history=history):
                with self.assertRaises(RepairContractError):
                    evaluate_repair_loop(
                        history, current_revision="rev-12", candidate_patch_sha256="a" * 64,
                    )
