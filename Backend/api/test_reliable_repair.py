from django.test import SimpleTestCase

from .reliable_repair import analyze_repair_evidence


class ReliableRepairAnalysisTests(SimpleTestCase):
    def evidence(self, code="python_import_error", source="stderr", state="failed"):
        return {
            "state": state,
            "profile": "python_pytest",
            "diagnostic_signals": [{"code": code, "source": source}],
            "revision": 7,
            "exit_code": 1,
            "output_persisted": False,
        }

    def test_import_failure_produces_bounded_reviewable_recommendation(self):
        analysis = analyze_repair_evidence("python_pytest", self.evidence())
        self.assertEqual(analysis["status"], "review_required")
        self.assertEqual(analysis["diagnostic_code"], "python_import_error")
        self.assertEqual(analysis["confidence"], "moderate")
        self.assertTrue(analysis["human_review_required"])
        self.assertFalse(analysis["workspace_mutated"])
        self.assertFalse(analysis["patch_generated"])
        self.assertFalse(analysis["execution_requested"])
        self.assertTrue(analysis["recommended_actions"])
        self.assertTrue(analysis["verification_checklist"])

    def test_unknown_failure_does_not_invent_a_root_cause(self):
        evidence = self.evidence("command_failed", "exit_status")
        analysis = analyze_repair_evidence("python_pytest", evidence)
        self.assertEqual(analysis["confidence"], "insufficient")
        self.assertIn("no recognized diagnostic category", analysis["probable_cause"])

    def test_stale_revision_is_classified_as_workspace_integrity_issue(self):
        evidence = self.evidence(state="stale_workspace")
        analysis = analyze_repair_evidence("python_pytest", evidence)
        self.assertEqual(analysis["diagnostic_code"], "command_failed")
        self.assertEqual(analysis["confidence"], "high")
        self.assertIn("revision changed", analysis["probable_cause"])

    def test_infrastructure_failure_is_not_misreported_as_code_defect(self):
        evidence = self.evidence(state="infrastructure_failed")
        evidence.pop("diagnostic_signals")
        analysis = analyze_repair_evidence("python_pytest", evidence)
        self.assertIn("not evidence of a source-code defect", analysis["probable_cause"])

    def test_rejects_profile_mismatch_and_untrusted_signal_shapes(self):
        with self.assertRaises(ValueError):
            analyze_repair_evidence("node_test", self.evidence())
        evidence = self.evidence()
        evidence["diagnostic_signals"] = [{"code": "python_import_error", "source": "stderr", "detail": "secret"}]
        with self.assertRaises(ValueError):
            analyze_repair_evidence("python_pytest", evidence)

    def test_every_supported_diagnostic_category_has_reviewable_guidance(self):
        cases = [
            ("python_syntax_error", "stderr"),
            ("python_test_failure", "stdout"),
            ("node_module_resolution_error", "stderr"),
            ("node_test_failure", "stdout"),
            ("node_build_error", "stderr"),
            ("node_lint_error", "stdout"),
        ]
        for code, source in cases:
            with self.subTest(code=code):
                profile = "python_pytest" if code.startswith("python_") else "node_test"
                evidence = self.evidence(code, source)
                evidence["profile"] = profile
                analysis = analyze_repair_evidence(profile, evidence)
                self.assertEqual(analysis["diagnostic_code"], code)
                self.assertTrue(analysis["recommended_actions"])
                self.assertTrue(analysis["verification_checklist"])
                self.assertTrue(analysis["human_review_required"])

    def test_rejects_unsupported_verification_profile(self):
        evidence = self.evidence()
        evidence["profile"] = "arbitrary_shell"
        with self.assertRaises(ValueError):
            analyze_repair_evidence("arbitrary_shell", evidence)

    def test_rejects_unknown_diagnostic_codes_and_oversized_signal_lists(self):
        evidence = self.evidence("arbitrary_output", "stderr")
        with self.assertRaises(ValueError):
            analyze_repair_evidence("python_pytest", evidence)
        evidence = self.evidence()
        evidence["diagnostic_signals"] = evidence["diagnostic_signals"] * 5
        with self.assertRaises(ValueError):
            analyze_repair_evidence("python_pytest", evidence)
