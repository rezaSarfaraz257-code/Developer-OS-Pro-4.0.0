from datetime import datetime, timedelta, timezone, tzinfo

from django.test import SimpleTestCase

from .repair_engine import (
    MAX_PATCH_SOURCE_BYTES,
    MAX_PATCH_TOTAL_SOURCE_BYTES,
    MAX_PATCH_SNAPSHOT_FILES,
    MAX_PATCH_TOTAL_PATH_BYTES,
    MAX_PATCH_SOURCE_LINES,
    RepairContractError,
    analyze_root_cause,
    classify_execution_failure,
    compare_repair_outcomes,
    normalize_runner_evidence,
    scan_patch_for_secrets,
    validate_revision_bound_approval,
    validate_unified_patch,
    evaluate_repair_loop,
    evaluate_rollback_eligibility,
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

    def test_rejects_whitespace_and_control_characters_in_opaque_revisions(self):
        invalid_revisions = (
            "", "revision with spaces", "revision" + chr(10) + "next",
            "revision" + chr(0), "revision" + chr(0x200b),
        )
        for revision in invalid_revisions:
            with self.subTest(revision=repr(revision)):
                payload = self.evidence(workspace_revision=revision)
                with self.assertRaises(RepairContractError):
                    normalize_runner_evidence(
                        payload,
                        expected_execution_id="exec-001",
                        expected_workspace_id=7,
                        expected_workspace_revision=revision,
                        expected_job_id=33,
                    )

    def test_rejects_whitespace_and_control_characters_in_execution_identity(self):
        invalid_ids = (
            "", "exec with spaces", "exec" + chr(10) + "next",
            "exec" + chr(0), "exec" + chr(0x200b),
        )
        for execution_id in invalid_ids:
            with self.subTest(execution_id=repr(execution_id)):
                payload = self.evidence(execution_id=execution_id)
                with self.assertRaises(RepairContractError):
                    normalize_runner_evidence(
                        payload,
                        expected_execution_id=execution_id,
                        expected_workspace_id=7,
                        expected_workspace_revision=12,
                        expected_job_id=33,
                    )

    def test_rejects_non_string_runner_origin_and_execution_identity(self):
        class EqualToRunner:
            def __eq__(self, other):
                return other == "runner"

        class EqualToExecutionId:
            def __eq__(self, other):
                return other == "exec-001"

        for payload in (
            self.evidence(evidence_origin=EqualToRunner()),
            self.evidence(execution_id=EqualToExecutionId()),
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(RepairContractError):
                    self.normalize(payload)

    def test_rejects_string_subclasses_for_runner_identity_fields(self):
        class SpoofedString(str):
            def __eq__(self, other):
                return True

            __hash__ = str.__hash__

        for field, value in (
            ("evidence_origin", SpoofedString("agent")),
            ("execution_id", SpoofedString("other-execution")),
            ("execution_status", SpoofedString("succeeded")),
        ):
            with self.subTest(field=field):
                payload = self.evidence(**{field: value})
                with self.assertRaises(RepairContractError):
                    self.normalize(payload)

        spoofed_revision = SpoofedString("12")
        with self.assertRaises(RepairContractError):
            normalize_runner_evidence(
                self.evidence(workspace_revision=spoofed_revision),
                expected_execution_id="exec-001",
                expected_workspace_id=7,
                expected_workspace_revision=spoofed_revision,
                expected_job_id=33,
            )

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
        oversized_usage = self.evidence(resource_usage={"peak_memory_bytes": 10 ** 100})
        with self.assertRaises(RepairContractError):
            self.normalize(bad_location)
        for unsafe_path in ("src/hidden" + chr(0x200b) + ".py", "src/report" + chr(0x202e) + "txt.py", "src/control" + chr(0x0085) + ".py"):
            with self.subTest(unsafe_path=repr(unsafe_path)):
                with self.assertRaises(RepairContractError):
                    self.normalize(self.evidence(source_location={"path": unsafe_path, "line": 1}))
        with self.assertRaises(RepairContractError):
            self.normalize(bad_usage)
        with self.assertRaises(RepairContractError):
            self.normalize(oversized_usage)

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

    def test_redacts_entire_private_key_blocks_from_persisted_evidence(self):
        key_body = "MIIE" + "K" * 48
        private_key = (
            "-----BEGIN PRIVATE KEY-----\n"
            + key_body
            + "\n-----END PRIVATE KEY-----"
        )
        result = self.normalize(self.evidence(stack_trace=private_key))
        self.assertNotIn(key_body, result["stack_trace"])
        self.assertNotIn("BEGIN PRIVATE KEY", result["stack_trace"])
        self.assertIn("[REDACTED_SECRET]", result["stack_trace"])


    def test_redacts_incomplete_private_key_block_to_end_of_evidence(self):
        key_body = "MIIE" + "Q" * 48
        incomplete_key = (
            "diagnostic prefix\n"
            "-----BEGIN PRIVATE KEY-----\n"
            + key_body
            + "\ntrailing diagnostic material"
        )
        result = self.normalize(self.evidence(stack_trace=incomplete_key))
        self.assertIn("diagnostic prefix", result["stack_trace"])
        self.assertNotIn(key_body, result["stack_trace"])
        self.assertNotIn("trailing diagnostic material", result["stack_trace"])
        self.assertTrue(result["stack_trace"].endswith("[REDACTED_SECRET]"))

    def test_redacts_secret_patterns_from_error_codes_and_source_symbols(self):
        secret = "ghp_" + "S" * 30
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signatureABCDEF0123456789"
        result = self.normalize(self.evidence(structured_errors=[{
            "code": "provider_" + secret,
            "message": "failure " + jwt,
            "source_location": {"path": "src/main.py", "symbol": "symbol_" + secret},
            "evidence_reference": None,
        }]))
        self.assertNotIn(secret, result["structured_errors"][0]["code"])
        self.assertNotIn(secret, result["structured_errors"][0]["source_location"]["symbol"])
        self.assertNotIn(jwt, result["structured_errors"][0]["message"])


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

    def test_redacts_additional_provider_tokens_and_termination_signal(self):
        secrets = (
            "github_pat_" + "G" * 30,
            "glpat-" + "L" * 30,
            "AIza" + "M" * 36,
            "sk_live_" + "S" * 24,
            "rk_test_" + "R" * 24,
            "ASIA" + "A" * 16,
            "npm-" + "N" * 24,
            "pypi-" + "P" * 24,
        )
        for secret in secrets:
            with self.subTest(secret_prefix=secret[:8]):
                normalized = self.normalize(
                    self.evidence(termination_signal="SIGTERM " + secret)
                )
                self.assertNotIn(secret, normalized["termination_signal"])
                self.assertIn("[REDACTED_SECRET]", normalized["termination_signal"])
        for credential in (
            "password='private phrase with spaces'",
            "aws_secret_access_key=AbCdEf0123456789+/=XYZ",
        ):
            with self.subTest(credential_key=credential.split("=")[0]):
                normalized = self.normalize(
                    self.evidence(termination_signal=credential)
                )
                self.assertNotIn(credential.split("=", 1)[1].strip("'"), normalized["termination_signal"])
                self.assertIn("[REDACTED_SECRET]", normalized["termination_signal"])

    def test_root_cause_uses_redacted_normalized_evidence_not_raw_payload(self):
        secret = "ghp_" + "R" * 30
        payload = self.evidence(structured_errors=[{
            "code": "runtime_exception",
            "message": "provider failure " + secret,
            "source_location": {"path": "src/main.py", "line": 8},
            "evidence_reference": "artifact://errors/33/0",
        }])
        result = analyze_root_cause(payload)
        self.assertNotIn(secret, result["candidates"][0]["message"])
        self.assertIn("[REDACTED_SECRET]", result["candidates"][0]["message"])

    def test_classifier_fails_closed_on_malformed_runner_payloads(self):
        for evidence in (
            {"evidence_origin": "runner", "execution_status": "failed", "structured_errors": None},
            {"evidence_origin": "runner", "execution_status": "unknown", "structured_errors": []},
            {"evidence_origin": "runner", "execution_status": "failed", "structured_errors": [{"code": []}]},
            {"evidence_origin": "runner", "execution_status": [], "structured_errors": []},
            {"evidence_origin": "runner", "execution_status": "failed", "structured_errors": [], "exit_code": 1},
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

    def test_before_after_comparison_requires_verified_post_repair_gates(self):
        before = self.normalize()
        after = self.normalize(self.evidence(
            execution_id="exec-002",
            workspace_revision=13,
            execution_status="succeeded",
            exit_code=0,
            structured_errors=[],
            source_location=None,
        ))
        verification = {
            "schema_version": 1,
            "evidence_origin": "runner",
            "execution_id": "exec-002",
            "workspace_id": 7,
            "job_id": 34,
            "workspace_revision": 13,
            "execution_status": "succeeded",
            "exit_code": 0,
            "checks": {
                name: {
                    "status": "passed",
                    "evidence_reference": "artifact://checks/" + name,
                    "execution_id": "exec-002",
                    "workspace_revision": 13,
                }
                for name in ("targeted_tests", "regression_tests", "security")
            },
        }
        result = compare_repair_outcomes(before, after, verification)
        self.assertEqual(result["outcome"], "verified_repair")
        verification["checks"]["security"]["status"] = "failed"
        result = compare_repair_outcomes(before, after, verification)
        self.assertEqual(result["outcome"], "verification_incomplete")
        verification["checks"]["security"]["status"] = "passed"
        verification["execution_id"] = "different-execution"
        result = compare_repair_outcomes(before, after, verification)
        self.assertEqual(result["outcome"], "verification_incomplete")
        verification["execution_id"] = "exec-002"
        verification["job_id"] = 999
        result = compare_repair_outcomes(before, after, verification)
        self.assertEqual(result["outcome"], "verification_incomplete")
        verification["job_id"] = 34
        verification["workspace_id"] = 8
        result = compare_repair_outcomes(before, after, verification)
        self.assertEqual(result["outcome"], "verification_incomplete")

    def test_before_after_comparison_rejects_same_workspace_revision(self):
        before = self.normalize()
        after = self.normalize(self.evidence(
            execution_id="exec-002",
            execution_status="failed",
        ))
        with self.assertRaises(RepairContractError):
            compare_repair_outcomes(before, after, {})


class PatchAndApprovalValidationTests(SimpleTestCase):
    def validate_patch(self, unified_diff, *, target_revision, expected_revision, workspace_files, snapshot_revision=None):
        # Existing fixtures model an atomic trusted snapshot read at the target
        # revision. Dedicated tests pass a mismatched revision explicitly.
        if snapshot_revision is None:
            snapshot_revision = target_revision
        return validate_unified_patch(
            unified_diff,
            target_revision=target_revision,
            expected_revision=expected_revision,
            snapshot_revision=snapshot_revision,
            workspace_files=workspace_files,
        )

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
        result = self.validate_patch(
            indexed, target_revision="rev-12", expected_revision="rev-12",
            workspace_files={"src/main.py": "value = 1\n"},
        )
        self.assertTrue(result["validated"])
        mode_change = self.patch().replace(
            "diff --git a/src/main.py b/src/main.py\n",
            "diff --git a/src/main.py b/src/main.py\nold mode 100644\nnew mode 100755\n",
        )
        with self.assertRaises(RepairContractError):
            self.validate_patch(
                mode_change, target_revision="rev-12", expected_revision="rev-12",
                workspace_files={"src/main.py": "value = 1\n"},
            )

    def test_rejects_index_metadata_outside_file_header_position(self):
        valid_index = "index 0123456789abcdef..abcdef0123456789 100644\n"
        malformed_patches = (
            valid_index + self.patch(),
            self.patch().replace("@@ -1 +1 @@\n", "@@ -1 +1 @@\n" + valid_index),
            self.patch().replace("--- a/src/main.py\n", "--- a/src/main.py\n" + valid_index),
            self.patch().replace(
                "--- a/src/main.py\n",
                valid_index + valid_index + "--- a/src/main.py\n",
            ),
        )
        for malformed in malformed_patches:
            with self.subTest(patch=malformed):
                with self.assertRaises(RepairContractError):
                    self.validate_patch(
                        malformed, target_revision="r", expected_revision="r",
                        workspace_files={"src/main.py": "value = 1\n"},
                    )

    def test_validates_minimal_patch_against_exact_revision_and_existing_paths(self):
        result = self.validate_patch(
            self.patch(),
            target_revision="rev-12",
            expected_revision="rev-12",
            workspace_files={"src/main.py": "value = 1\n"},
        )
        self.assertTrue(result["validated"])
        self.assertEqual(result["changed_paths"], ["src/main.py"])
        self.assertEqual(len(result["patch_sha256"]), 64)
        self.assertEqual(result["source_context"], "matched")

    def test_patch_validation_rejects_unsafe_unmodified_snapshot_entries(self):
        with self.assertRaises(RepairContractError):
            self.validate_patch(
                self.patch(),
                target_revision="rev-12",
                expected_revision="rev-12",
                workspace_files={
                    "src/main.py": "value = 1\n",
                    "../outside.py": "not patched but still part of snapshot\n",
                },
            )

    def test_patch_validation_bounds_snapshot_entry_and_path_metadata_counts(self):
        too_many_files = {"src/main.py": "value = 1\\n"}
        too_many_files.update({
            f"empty/{index}.txt": ""
            for index in range(MAX_PATCH_SNAPSHOT_FILES)
        })
        with self.assertRaises(RepairContractError):
            self.validate_patch(
                self.patch(),
                target_revision="rev-12",
                expected_revision="rev-12",
                workspace_files=too_many_files,
            )

        oversized_path_metadata = {
            "src/main.py": "value = 1\\n",
            **{
                f"{index:04d}/" + ("x" * 500) + ".txt": ""
                for index in range((MAX_PATCH_TOTAL_PATH_BYTES // 500) + 2)
            },
        }
        with self.assertRaises(RepairContractError):
            self.validate_patch(
                self.patch(),
                target_revision="rev-12",
                expected_revision="rev-12",
                workspace_files=oversized_path_metadata,
            )

    def test_patch_validation_rejects_casefold_and_unicode_normalization_path_collisions(self):
        colliding_snapshots = (
            {
                "src/main.py": "value = 1\\n",
                "src/Config.py": "a",
                "src/config.py": "b",
            },
            {
                "src/main.py": "value = 1\\n",
                "src/café.py": "a",
                "src/cafe\\u0301.py": "b",
            },
        )
        for snapshot in colliding_snapshots:
            with self.subTest(snapshot=list(snapshot)):
                with self.assertRaises(RepairContractError):
                    self.validate_patch(
                        self.patch(),
                        target_revision="rev-12",
                        expected_revision="rev-12",
                        workspace_files=snapshot,
                    )

    def test_patch_parser_rejects_duplicate_file_entries(self):
        duplicated = self.patch() + self.patch()
        with self.assertRaises(RepairContractError):
            self.validate_patch(
                duplicated,
                target_revision="rev-12",
                expected_revision="rev-12",
                workspace_files={"src/main.py": "value = 1\\n"},
            )

    def test_patch_validation_rejects_line_count_memory_amplification(self):
        oversized_line_count = "\n" * (MAX_PATCH_SOURCE_LINES + 1)
        with self.assertRaises(RepairContractError):
            self.validate_patch(
                self.patch(),
                target_revision="rev-12",
                expected_revision="rev-12",
                workspace_files={"src/main.py": oversized_line_count},
            )

    def test_patch_validation_rejects_source_without_final_lf(self):
        with self.assertRaises(RepairContractError):
            self.validate_patch(
                self.patch(),
                target_revision="rev-12",
                expected_revision="rev-12",
                workspace_files={"src/main.py": "value = 1"},
            )

    def test_patch_parser_rejects_unterminated_final_diff_record(self):
        with self.assertRaises(RepairContractError):
            self.validate_patch(
                self.patch().rstrip("\\n"),
                target_revision="rev-12",
                expected_revision="rev-12",
                workspace_files={"src/main.py": "value = 1\\n"},
            )

    def test_patch_parser_treats_unicode_line_separator_as_file_content(self):
        source = "value = 'before\u2028after'\n"
        patch = (
            "diff --git a/src/main.py b/src/main.py\n"
            "--- a/src/main.py\n"
            "+++ b/src/main.py\n"
            "@@ -1 +1 @@\n"
            "-value = 'before\u2028after'\n"
            "+value = 'updated'\n"
        )
        result = self.validate_patch(
            patch,
            target_revision="rev-12",
            expected_revision="rev-12",
            workspace_files={"src/main.py": source},
        )
        self.assertTrue(result["validated"])

    def test_patch_validation_rejects_snapshot_revision_mismatch(self):
        with self.assertRaises(RepairContractError):
            self.validate_patch(
                self.patch(),
                target_revision="rev-12",
                expected_revision="rev-12",
                snapshot_revision="rev-11",
                workspace_files={"src/main.py": "value = 1\n"},
            )

    def test_patch_validation_returns_stable_snapshot_digest(self):
        first = self.validate_patch(
            self.patch(),
            target_revision="rev-12",
            expected_revision="rev-12",
            workspace_files={"README.md": "notes\n", "src/main.py": "value = 1\n"},
        )
        second = self.validate_patch(
            self.patch(),
            target_revision="rev-12",
            expected_revision="rev-12",
            workspace_files={"src/main.py": "value = 1\n", "README.md": "notes\n"},
        )
        changed = self.validate_patch(
            self.patch(),
            target_revision="rev-12",
            expected_revision="rev-12",
            workspace_files={"README.md": "changed\n", "src/main.py": "value = 1\n"},
        )
        self.assertEqual(first["snapshot_revision"], "rev-12")
        self.assertEqual(first["snapshot_sha256"], second["snapshot_sha256"])
        self.assertNotEqual(first["snapshot_sha256"], changed["snapshot_sha256"])

    def test_patch_validation_rejects_spoofed_snapshot_containers_and_values(self):
        class SpoofedString(str):
            def __eq__(self, other):
                return True

            __hash__ = str.__hash__

        class SpoofedDict(dict):
            pass

        invalid_snapshots = (
            SpoofedDict({"src/main.py": "value = 1\n"}),
            {SpoofedString("src/main.py"): "value = 1\n"},
            {"src/main.py": SpoofedString("value = 1\n")},
        )
        for snapshot in invalid_snapshots:
            with self.subTest(snapshot_type=type(snapshot).__name__):
                with self.assertRaises(RepairContractError):
                    self.validate_patch(
                        self.patch(),
                        target_revision="rev-12",
                        expected_revision="rev-12",
                        workspace_files=snapshot,
                    )

    def test_rejects_stale_revision_protected_path_and_secret_patch(self):
        with self.assertRaises(RepairContractError):
            self.validate_patch(self.patch(), target_revision="old", expected_revision="new", workspace_files={"src/main.py": ""})
        protected = self.patch().replace("src/main.py", ".github/workflows/ci.yml")
        with self.assertRaises(RepairContractError):
            self.validate_patch(protected, target_revision="r", expected_revision="r", workspace_files={".github/workflows/ci.yml": ""})
        secret_patch = self.patch().replace("+value = 2", "+api_key = 'super-secret-value-123'")
        short_unquoted_secret_patch = self.patch().replace("+value = 2", "+password=ShortSecret9")
        triple_plus_secret_patch = self.patch().replace(
            "+value = 2", "+++ b/token = 'ghp_" + "E" * 30 + "'"
        )
        with self.assertRaises(RepairContractError):
            self.validate_patch(secret_patch, target_revision="r", expected_revision="r", workspace_files={"src/main.py": "value = 1\n"})
        with self.assertRaises(RepairContractError):
            self.validate_patch(short_unquoted_secret_patch, target_revision="r", expected_revision="r", workspace_files={"src/main.py": "value = 1\n"})
        with self.assertRaises(RepairContractError):
            self.validate_patch(
                triple_plus_secret_patch, target_revision="r", expected_revision="r",
                workspace_files={"src/main.py": "value = 1\n"},
            )
        for protected_name in (".env.staging", "config/.env/production.env", "config/.env.staging", "config/.npmrc", "nested/.netrc", "credentials/id_rsa", "config/service.pem", "nested/secrets/token.txt", "config/certificate.pem/source.py", "nested/Dockerfile.backup/app.py"):
            protected_patch = self.patch().replace("src/main.py", protected_name)
            with self.assertRaises(RepairContractError):
                self.validate_patch(protected_patch, target_revision="r", expected_revision="r", workspace_files={protected_name: "value = 1\n"})
    def test_rejects_bidirectional_controls_in_added_source_lines(self):
        controls = ("\\u061c", "\\u200e", "\\u200f", "\\u202a", "\\u202b", "\\u202c", "\\u202d", "\\u202e", "\\u2066", "\\u2067", "\\u2068", "\\u2069")
        for control in controls:
            patch = self.patch().replace("+value = 2", '+value = "safe' + control + 'text"')
            with self.subTest(control=repr(control)):
                with self.assertRaises(RepairContractError):
                    self.validate_patch(
                        patch, target_revision="r", expected_revision="r",
                        workspace_files={"src/main.py": "value = 1\n"},
                    )

    def test_rejects_oversized_hunk_coordinates(self):
        huge_coordinate = self.patch().replace(
            "@@ -1 +1 @@", "@@ -" + ("9" * 5000) + " +1 @@"
        )
        out_of_range_coordinate = self.patch().replace(
            "@@ -1 +1 @@", "@@ -2147483648 +1 @@"
        )
        for patch in (huge_coordinate, out_of_range_coordinate):
            with self.subTest(patch_length=len(patch)):
                with self.assertRaises(RepairContractError):
                    self.validate_patch(
                        patch, target_revision="r", expected_revision="r",
                        workspace_files={"src/main.py": "value = 1\n"},
                    )

    def test_rejects_oversized_binary_and_invalid_unicode_source_snapshots(self):
        oversized_source = "x" * (MAX_PATCH_SOURCE_BYTES + 1)
        oversized_utf8_source = "é" * (MAX_PATCH_SOURCE_BYTES // 2 + 1)
        invalid_sources = (
            oversized_source,
            oversized_utf8_source,
            "value = 1\x00\n",
            "value = \ud800\n",
        )
        for source in invalid_sources:
            with self.subTest(source_length=len(source)):
                with self.assertRaises(RepairContractError):
                    self.validate_patch(
                        self.patch(), target_revision="r", expected_revision="r",
                        workspace_files={"src/main.py": source},
                    )

    def test_rejects_invalid_unicode_patch_text_with_contract_error(self):
        invalid_patch = self.patch().replace("+value = 2", "+value = \ud800")
        with self.assertRaises(RepairContractError):
            self.validate_patch(
                invalid_patch, target_revision="r", expected_revision="r",
                workspace_files={"src/main.py": "value = 1\n"},
            )

    def test_rejects_aggregate_workspace_snapshot_size_over_limit(self):
        per_file_size = MAX_PATCH_SOURCE_BYTES - 1024
        source = "value = 1\n" + ("x" * (per_file_size - len("value = 1\n")))
        paths = ("src/a.py", "src/b.py", "src/c.py")
        patch_parts = []
        for path in paths:
            patch_parts.extend((
                f"diff --git a/{path} b/{path}\n",
                f"--- a/{path}\n",
                f"+++ b/{path}\n",
                "@@ -1 +1 @@\n",
                "-value = 1\n",
                "+value = 2\n",
            ))
        patch = "".join(patch_parts)
        self.assertLess(len(source.encode("utf-8")), MAX_PATCH_SOURCE_BYTES)
        self.assertGreater(3 * len(source.encode("utf-8")), MAX_PATCH_TOTAL_SOURCE_BYTES)
        with self.assertRaises(RepairContractError):
            self.validate_patch(
                patch, target_revision="r", expected_revision="r",
                workspace_files={path: source for path in paths},
            )

    def test_accepts_zero_count_insertions_at_file_boundaries(self):
        cases = (
            (
                "@@ -0,0 +1 @@\n+first\n",
                "first\nalpha\nbeta\n",
            ),
            (
                "@@ -2,0 +3 @@\n+last\n",
                "alpha\nbeta\nlast\n",
            ),
        )
        for hunk, source in cases:
            with self.subTest(hunk=hunk):
                patch = (
                    "diff --git a/src/main.py b/src/main.py\n"
                    "--- a/src/main.py\n"
                    "+++ b/src/main.py\n"
                    + hunk
                )
                self.assertTrue(self.validate_patch(
                    patch, target_revision="r", expected_revision="r",
                    workspace_files={"src/main.py": source.replace(
                        "first\n", ""
                    ).replace("last\n", "")},
                )["validated"])

    def test_validates_multiple_hunks_after_cumulative_line_delta(self):
        patch = (
            "diff --git a/src/main.py b/src/main.py\n"
            "--- a/src/main.py\n"
            "+++ b/src/main.py\n"
            "@@ -1 +1,2 @@\n"
            " alpha\n"
            "+inserted\n"
            "@@ -4 +5 @@\n"
            "-delta\n"
            "+DELTA\n"
        )
        self.assertTrue(self.validate_patch(
            patch, target_revision="r", expected_revision="r",
            workspace_files={"src/main.py": "alpha\nbeta\ngamma\ndelta\n"},
        )["validated"])
        wrong_offset = patch.replace("@@ -4 +5 @@", "@@ -4 +4 @@")
        with self.assertRaises(RepairContractError):
            self.validate_patch(
                wrong_offset, target_revision="r", expected_revision="r",
                workspace_files={"src/main.py": "alpha\nbeta\ngamma\ndelta\n"},
            )

    def test_rejects_empty_and_context_only_hunks(self):
        context_only = self.patch().replace(
            "@@ -1 +1 @@\n-value = 1\n+value = 2\n",
            "@@ -1 +1 @@\n value = 1\n",
        )
        empty = self.patch().replace(
            "@@ -1 +1 @@\n-value = 1\n+value = 2\n",
            "@@ -0,0 +0,0 @@\n",
        )
        for patch in (context_only, empty):
            with self.subTest(patch=patch):
                with self.assertRaises(RepairContractError):
                    self.validate_patch(
                        patch, target_revision="r", expected_revision="r",
                        workspace_files={"src/main.py": "value = 1\n"},
                    )

    def test_rejects_unmarked_blank_line_inside_hunk(self):
        malformed = self.patch().replace(
            "@@ -1 +1 @@\n-value = 1\n+value = 2\n",
            "@@ -1 +1 @@\n-value = 1\n\n+value = 2\n",
        )
        with self.assertRaises(RepairContractError):
            self.validate_patch(
                malformed, target_revision="r", expected_revision="r",
                workspace_files={"src/main.py": "value = 1\n"},
            )

    def test_hunk_lines_that_resemble_file_headers_remain_hunk_content(self):
        patch = (
            "diff --git a/src/main.py b/src/main.py\n"
            "--- a/src/main.py\n"
            "+++ b/src/main.py\n"
            "@@ -1 +1 @@\n"
            "---- old\n"
            "++++ new\n"
        )
        result = self.validate_patch(
            patch, target_revision="r", expected_revision="r",
            workspace_files={"src/main.py": "--- old\n"},
        )
        self.assertTrue(result["validated"])

    def test_rejects_patch_when_git_header_or_source_context_is_forged(self):
        wrong_git_path = self.patch().replace("diff --git a/src/main.py b/src/main.py", "diff --git a/src/other.py b/src/other.py")
        with self.assertRaises(RepairContractError):
            self.validate_patch(wrong_git_path, target_revision="r", expected_revision="r", workspace_files={"src/main.py": "value = 1\n"})
        wrong_context = self.patch().replace("-value = 1", "-value = 999")
        with self.assertRaises(RepairContractError):
            self.validate_patch(wrong_context, target_revision="r", expected_revision="r", workspace_files={"src/main.py": "value = 1\n"})
        wrong_count = self.patch().replace("@@ -1 +1 @@", "@@ -1,2 +1,2 @@")
        with self.assertRaises(RepairContractError):
            self.validate_patch(wrong_count, target_revision="r", expected_revision="r", workspace_files={"src/main.py": "value = 1\n"})

        wrong_new_offset = self.patch().replace("@@ -1 +1 @@", "@@ -1 +4 @@")
        with self.assertRaises(RepairContractError):
            self.validate_patch(wrong_new_offset, target_revision="r", expected_revision="r", workspace_files={"src/main.py": "value = 1\n"})

    def test_secret_scanner_never_returns_secret_value(self):
        findings = scan_patch_for_secrets("+token = 'ghp_" + "A" * 30 + "'")
        self.assertTrue(findings)
        self.assertNotIn("ghp_", str(findings))
        bearer = scan_patch_for_secrets("+Authorization: Bearer " + "B" * 30)
        unquoted = scan_patch_for_secrets("+access_token=" + "C" * 30)
        short_unquoted = scan_patch_for_secrets("+password=ShortSecret9")
        jwt = scan_patch_for_secrets("+token = \"eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signatureABCDEF0123456789\"")
        self.assertTrue(bearer)
        self.assertTrue(unquoted)
        self.assertTrue(short_unquoted)
        self.assertNotIn("ShortSecret9", str(short_unquoted))
        self.assertTrue(jwt)
        self.assertNotIn("signatureABC", str(jwt))
        self.assertNotIn("B" * 30, str(bearer))
        self.assertNotIn("C" * 30, str(unquoted))


    def test_scanner_checks_added_lines_starting_with_multiple_plus_signs(self):
        secret = "ghp_" + "D" * 30
        source_line = "+++ b/token = '" + secret + "'"
        patch = (
            "diff --git a/src/main.py b/src/main.py\n"
            "--- a/src/main.py\n"
            "+++ b/src/main.py\n"
            "@@ -1 +1 @@\n"
            "-value = 1\n"
            + source_line + "\n"
        )
        findings = scan_patch_for_secrets(patch)
        self.assertTrue(findings)
        self.assertNotIn(secret, str(findings))
        header_only = (
            "diff --git a/src/main.py b/src/main.py\n"
            "index 0123456789abcdef..abcdef0123456789 100644\n"
            "--- a/src/main.py\n"
            "+++ b/src/main.py\n"
        )
        self.assertEqual(scan_patch_for_secrets(header_only), [])
        self.assertTrue(scan_patch_for_secrets(source_line))
    def test_secret_scanner_maps_multiple_findings_to_original_patch_lines(self):
        token = "ghp_" + "R" * 30
        patch = (
            "diff --git a/src/config.py b/src/config.py\n"
            "--- a/src/config.py\n"
            "+++ b/src/config.py\n"
            "@@ -1,2 +1,3 @@\n"
            "+token = '" + token + "'\n"
            " context = 'safe'\n"
            "+password = 'LongSecretValue123'\n"
        )
        findings = scan_patch_for_secrets(patch)
        self.assertEqual({item["line"] for item in findings}, {5, 7})
        self.assertTrue(all(item["rule"] == "possible_secret" for item in findings))
        self.assertNotIn(token, str(findings))
        self.assertNotIn("LongSecretValue123", str(findings))

    def test_scans_multiline_and_incomplete_private_keys_across_added_patch_lines(self):
        private_key = (
            "-----BEGIN PRIVATE KEY-----\n"
            + "MIIE" + "K" * 48 + "\n"
            + "-----END PRIVATE KEY-----"
        )
        patch = (
            "diff --git a/src/config.py b/src/config.py\n"
            "--- a/src/config.py\n"
            "+++ b/src/config.py\n"
            "@@ -0,0 +1,3 @@\n"
            + "".join("+" + line + "\n" for line in private_key.splitlines())
        )
        findings = scan_patch_for_secrets(patch)
        self.assertTrue(findings)
        self.assertNotIn("MIIE", str(findings))
        self.assertNotIn("K" * 48, str(findings))

        incomplete = (
            "diff --git a/src/config.py b/src/config.py\n"
            "--- a/src/config.py\n"
            "+++ b/src/config.py\n"
            "@@ -0,0 +1,2 @@\n"
            "+-----BEGIN PRIVATE KEY-----\n"
            "+" + "MIIE" + "Q" * 48 + "\n"
        )
        incomplete_findings = scan_patch_for_secrets(incomplete)
        self.assertTrue(incomplete_findings)
        self.assertNotIn("Q" * 48, str(incomplete_findings))

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
        overlong_approval = {**approval, "expires_at": (now + timedelta(hours=2)).isoformat()}
        self.assertFalse(validate_revision_bound_approval(overlong_approval, current_revision="rev-12", patch_sha256="a" * 64, now=now))
        for invalid_clock in (False, 0, ""):
            with self.subTest(invalid_clock=invalid_clock):
                with self.assertRaises(RepairContractError):
                    validate_revision_bound_approval(
                        approval,
                        current_revision="rev-12",
                        patch_sha256="a" * 64,
                        now=invalid_clock,
                    )
        class MissingOffsetTimezone(tzinfo):
            def utcoffset(self, dt):
                return None

            def dst(self, dt):
                return None

        invalid_aware_clock = datetime(2026, 11, 27, 12, 0, tzinfo=MissingOffsetTimezone())
        with self.assertRaises(RepairContractError):
            validate_revision_bound_approval(
                approval,
                current_revision="rev-12",
                patch_sha256="a" * 64,
                now=invalid_aware_clock,
            )

        class EqualToDigest:
            def __eq__(self, other):
                return other == "a" * 64

        spoofed_digest = {**approval, "patch_sha256": EqualToDigest()}
        self.assertFalse(validate_revision_bound_approval(
            spoofed_digest,
            current_revision="rev-12",
            patch_sha256="a" * 64,
            now=now,
        ))

        class SpoofedApproval(dict):
            def get(self, key, default=None):
                return approval.get(key, default)

        self.assertFalse(validate_revision_bound_approval(
            SpoofedApproval(),
            current_revision="rev-12",
            patch_sha256="a" * 64,
            now=now,
        ))

        class SpoofedDigest(str):
            def __eq__(self, other):
                return True

        self.assertFalse(validate_revision_bound_approval(
            approval,
            current_revision="rev-12",
            patch_sha256=SpoofedDigest("a" * 64),
            now=now,
        ))


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
        result["execution_id"] = "x" * 129
        self.assertFalse(verification_passed(result, expected_revision=14))
        result["execution_id"] = "exec" + chr(10) + "invalid"
        self.assertFalse(verification_passed(result, expected_revision=14))
        result["execution_id"] = "exec-verified-14"
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
        result["workspace_revision"] = 14

        class EqualToRunner:
            def __eq__(self, other):
                return other == "runner"

        class EqualToSucceeded:
            def __eq__(self, other):
                return other == "succeeded"

        result["evidence_origin"] = EqualToRunner()
        self.assertFalse(verification_passed(result, expected_revision=14))
        result["evidence_origin"] = "runner"
        result["execution_status"] = EqualToSucceeded()
        self.assertFalse(verification_passed(result, expected_revision=14))
        result["execution_status"] = "succeeded"

        class EqualToPassed:
            def __eq__(self, other):
                return other == "passed"

        class EqualToExecutionId:
            def __eq__(self, other):
                return other == "exec-verified-14"

        result["checks"]["security"]["status"] = EqualToPassed()
        self.assertFalse(verification_passed(result, expected_revision=14))
        result["checks"]["security"]["status"] = "passed"
        result["checks"]["security"]["execution_id"] = EqualToExecutionId()
        self.assertFalse(verification_passed(result, expected_revision=14))
        result["checks"]["security"]["execution_id"] = "exec-verified-14"
        for unsafe_reference in (
            "artifact://../outside",
            "runner://logs/../../private",
            "blob://logs//stderr",
            "https://example.com/evidence",
        ):
            result["checks"]["security"]["evidence_reference"] = unsafe_reference
            with self.subTest(evidence_reference=unsafe_reference):
                self.assertFalse(verification_passed(result, expected_revision=14))


class RollbackEligibilityTests(SimpleTestCase):
    def evidence(self, failed_gate=None, **overrides):
        checks = {
            name: {
                "status": "failed" if name == failed_gate else "passed",
                "evidence_reference": "artifact://verification/" + name,
                "execution_id": "exec-post-repair",
                "workspace_revision": "rev-13",
            }
            for name in ("targeted_tests", "regression_tests", "security")
        }
        result = {
            "schema_version": 1,
            "evidence_origin": "runner",
            "execution_id": "exec-post-repair",
            "workspace_id": 7,
            "job_id": 35,
            "workspace_revision": "rev-13",
            "patch_sha256": "a" * 64,
            "execution_status": "succeeded",
            "exit_code": 0,
            "checks": checks,
        }
        result.update(overrides)
        return result

    def kwargs(self):
        return {
            "workspace_id": 7,
            "current_revision": "rev-13",
            "expected_post_revision": "rev-13",
            "pre_repair_revision": "rev-12",
            "applied_patch_sha256": "a" * 64,
            "snapshot_reference": "artifact://snapshots/pre-repair",
        }

    def test_rollback_is_eligible_only_after_correlated_failed_gate(self):
        result = evaluate_rollback_eligibility(
            self.evidence(failed_gate="regression_tests"), **self.kwargs()
        )
        self.assertTrue(result["eligible"])
        self.assertEqual(result["failed_gates"], ["regression_tests"])
        self.assertEqual(result["target_revision"], "rev-12")

    def test_successful_verification_does_not_authorize_rollback(self):
        result = evaluate_rollback_eligibility(self.evidence(), **self.kwargs())
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"], "verification_passed")

    def test_rollback_rejects_stale_revision_workspace_and_unsafe_snapshot(self):
        for overrides, kwargs in (
            ({}, {**self.kwargs(), "current_revision": "rev-14"}),
            ({"workspace_id": 8}, self.kwargs()),
            ({}, {**self.kwargs(), "snapshot_reference": "artifact://snapshots/../secret"}),
        ):
            with self.subTest(overrides=overrides, kwargs=kwargs):
                with self.assertRaises(RepairContractError):
                    evaluate_rollback_eligibility(
                        self.evidence(failed_gate="security", **overrides), **kwargs
                    )

    def test_rollback_rejects_equality_spoofed_runner_fields(self):
        class EqualTo:
            def __init__(self, expected):
                self.expected = expected

            def __eq__(self, other):
                return other == self.expected

        for field, value in (
            ("evidence_origin", EqualTo("runner")),
            ("patch_sha256", EqualTo("a" * 64)),
            ("execution_status", EqualTo("succeeded")),
        ):
            with self.subTest(field=field):
                evidence = self.evidence(failed_gate="security")
                evidence[field] = value
                with self.assertRaises(RepairContractError):
                    evaluate_rollback_eligibility(evidence, **self.kwargs())

    def test_rollback_rejects_string_subclasses_for_trusted_identity_fields(self):
        class SpoofedString(str):
            def __eq__(self, other):
                return True

        for field, value in (
            ("evidence_origin", SpoofedString("agent")),
            ("execution_id", SpoofedString("forged-execution")),
            ("patch_sha256", SpoofedString("b" * 64)),
            ("execution_status", SpoofedString("failed")),
        ):
            with self.subTest(field=field):
                evidence = self.evidence(failed_gate="security")
                evidence[field] = value
                with self.assertRaises(RepairContractError):
                    evaluate_rollback_eligibility(evidence, **self.kwargs())

    def test_rollback_rejects_malformed_or_uncorrelated_gate_evidence(self):
        evidence = self.evidence(failed_gate="targeted_tests")
        evidence["patch_sha256"] = "b" * 64
        with self.assertRaises(RepairContractError):
            evaluate_rollback_eligibility(evidence, **self.kwargs())
        evidence = self.evidence(failed_gate="targeted_tests")
        evidence["checks"]["security"]["execution_id"] = "other-execution"
        with self.assertRaises(RepairContractError):
            evaluate_rollback_eligibility(evidence, **self.kwargs())
        evidence = self.evidence(failed_gate="targeted_tests")
        evidence["checks"]["security"]["status"] = "unknown"
        with self.assertRaises(RepairContractError):
            evaluate_rollback_eligibility(evidence, **self.kwargs())
        evidence["checks"]["security"]["status"] = []
        with self.assertRaises(RepairContractError):
            evaluate_rollback_eligibility(evidence, **self.kwargs())
        evidence = self.evidence(
            failed_gate="targeted_tests",
            execution_id="exec" + chr(10) + "invalid",
        )
        with self.assertRaises(RepairContractError):
            evaluate_rollback_eligibility(evidence, **self.kwargs())


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
