"""Safe, evidence-bounded diagnosis for failed AgentTask Runner verification.

This module produces reviewable repair guidance from allowlisted diagnostic enums.
It never consumes or persists raw stdout/stderr, reads source files, edits a
workspace, or executes a proposed action.
"""

from __future__ import annotations


_SUPPORTED_PROFILES = {"python_pytest", "python_unittest", "node_test", "node_build", "node_lint"}
_DIAGNOSTIC_PROFILES = {
    "python_import_error": {"python_pytest", "python_unittest"},
    "python_syntax_error": {"python_pytest", "python_unittest"},
    "python_test_failure": {"python_pytest", "python_unittest"},
    "node_module_resolution_error": {"node_test", "node_build", "node_lint"},
    "node_test_failure": {"node_test"},
    "node_build_error": {"node_build"},
    "node_lint_error": {"node_lint"},
    "command_failed": _SUPPORTED_PROFILES,
}


_DIAGNOSTICS = {
    "python_import_error": {
        "cause": "A Python import could not be resolved in the Runner environment.",
        "confidence": "moderate",
        "actions": [
            "Check whether the imported package is declared in the project's dependency manifest.",
            "Verify the package name and the import name are correct and compatible.",
            "Re-run the same verification profile after an approved dependency or import correction.",
        ],
        "checks": [
            "Confirm the dependency manifest and lockfile agree.",
            "Confirm the selected Python runtime matches the project's supported version.",
        ],
    },
    "python_syntax_error": {
        "cause": "Python source could not be parsed by the selected interpreter.",
        "confidence": "moderate",
        "actions": [
            "Review the changed Python syntax and surrounding block for an incomplete or malformed construct.",
            "Check whether the syntax requires a newer Python version than the Runner runtime.",
            "Run the project's syntax and test checks after an approved correction.",
        ],
        "checks": [
            "Confirm the configured Python runtime version.",
            "Re-run the failing verification profile against the exact approved workspace revision.",
        ],
    },
    "python_test_failure": {
        "cause": "At least one Python test reported a failure; the underlying behavioral cause is not established by this signal.",
        "confidence": "low",
        "actions": [
            "Reproduce the failing test using the same verification profile.",
            "Compare the test's expected behavior with the task acceptance criteria and the approved change.",
            "Prepare the smallest targeted correction and add a regression test where appropriate.",
        ],
        "checks": [
            "Confirm the failure is reproducible on the same workspace revision.",
            "Run the relevant test and the broader regression suite after approval.",
        ],
    },
    "node_module_resolution_error": {
        "cause": "The Node.js runtime or bundler could not resolve a required module.",
        "confidence": "moderate",
        "actions": [
            "Check whether the module is declared in the correct dependency section.",
            "Verify package installation state, lockfile consistency, and import path casing.",
            "Re-run the same Node verification profile after an approved dependency or import correction.",
        ],
        "checks": [
            "Confirm package.json and the lockfile are consistent.",
            "Confirm the configured Node.js runtime is supported by the project.",
        ],
    },
    "node_test_failure": {
        "cause": "At least one Node.js test reported a failure; the underlying behavioral cause is not established by this signal.",
        "confidence": "low",
        "actions": [
            "Reproduce the failing test with the same Node.js test profile.",
            "Compare actual behavior with the task acceptance criteria and the approved change.",
            "Prepare a minimal correction and regression coverage for human review.",
        ],
        "checks": [
            "Confirm the failure occurs on the same workspace revision.",
            "Run the relevant test and the full project test command after approval.",
        ],
    },
    "node_build_error": {
        "cause": "The Node.js build or type-check pipeline reported a compilation error.",
        "confidence": "low",
        "actions": [
            "Review the changed component, module imports, and type contracts involved in the build.",
            "Check the project's compiler and bundler configuration before changing dependencies.",
            "Prepare a minimal correction and re-run the build after human approval.",
        ],
        "checks": [
            "Confirm the configured Node.js version and build script.",
            "Run the build and relevant tests on the exact approved revision.",
        ],
    },
    "node_lint_error": {
        "cause": "The configured lint command reported one or more rule violations.",
        "confidence": "low",
        "actions": [
            "Run the project's lint command in a controlled development environment to inspect the affected rules.",
            "Separate correctness issues from formatting-only findings.",
            "Prepare the smallest compliant change for human review.",
        ],
        "checks": [
            "Confirm the lint configuration and script are the intended project versions.",
            "Re-run lint and regression tests after approval.",
        ],
    },
    "command_failed": {
        "cause": "The verification command exited unsuccessfully, but no recognized diagnostic category was available.",
        "confidence": "insufficient",
        "actions": [
            "Inspect the failure in the authorized development environment; raw process output is intentionally not retained as Agent evidence.",
            "Check Runner availability, runtime configuration, and the selected verification profile.",
            "Do not infer a source-code defect until additional safe evidence is available.",
        ],
        "checks": [
            "Confirm Runner status and the exact verification profile.",
            "Retry only through a newly approved workspace revision when a code change is required.",
        ],
    },
}


def analyze_repair_evidence(profile, evidence):
    """Build a bounded repair recommendation from trusted, allowlisted evidence."""
    if profile not in _SUPPORTED_PROFILES or not isinstance(evidence, dict):
        raise ValueError("Repair analysis requires a supported verification profile and evidence object.")

    if evidence.get("profile") != profile:
        raise ValueError("Repair analysis profile does not match the recorded evidence.")

    state = evidence.get("state")
    if state not in {"failed", "infrastructure_failed", "stale_workspace", "finalization_failed"}:
        raise ValueError("Repair analysis is only available for failed verification evidence.")

    signals = evidence.get("diagnostic_signals", [])
    if not isinstance(signals, list) or len(signals) > 4:
        raise ValueError("Repair diagnostic signals are malformed.")

    normalized = []
    seen = set()
    for signal in signals:
        if not isinstance(signal, dict) or set(signal) != {"code", "source"}:
            raise ValueError("Repair diagnostic signal has an invalid shape.")
        code, source = signal.get("code"), signal.get("source")
        if (
            code not in _DIAGNOSTICS
            or profile not in _DIAGNOSTIC_PROFILES.get(code, set())
            or source not in {"stdout", "stderr", "exit_status"}
        ):
            raise ValueError("Repair diagnostic signal is not allowlisted for this verification profile.")
        key = (code, source)
        if key not in seen:
            seen.add(key)
            normalized.append({"code": code, "source": source})

    if state == "stale_workspace":
        code = "command_failed"
        cause = "The workspace revision changed while verification was running; the result is stale and cannot validate the current workspace."
        confidence = "high"
        actions = [
            "Wait for Runner resynchronization to complete.",
            "Review the current workspace revision before preparing another patch.",
            "Obtain a new human-approved patch bound to the current revision before verification.",
        ]
        checks = [
            "Confirm the Runner sync job succeeded for the current workspace revision.",
            "Verify only the newly approved and synchronized revision.",
        ]
    elif state in {"infrastructure_failed", "finalization_failed"}:
        code = "command_failed"
        cause = "Runner verification could not be safely completed or its durable result could not be finalized; this is not evidence of a source-code defect."
        confidence = "high"
        actions = [
            "Check Runner and background-worker health and the recorded reconciliation job.",
            "Confirm the workspace has been resynchronized before any further execution.",
            "Do not replay an ambiguous Runner request; use a fresh approved verification attempt only after reconciliation.",
        ]
        checks = [
            "Confirm the reconciliation job completed successfully.",
            "Confirm the task is bound to the intended workspace revision and verification job.",
        ]
    elif normalized:
        code = normalized[0]["code"]
        guidance = _DIAGNOSTICS[code]
        cause = guidance["cause"]
        confidence = guidance["confidence"]
        actions = list(guidance["actions"])
        checks = list(guidance["checks"])
    else:
        code = "command_failed"
        guidance = _DIAGNOSTICS[code]
        cause = guidance["cause"]
        confidence = guidance["confidence"]
        actions = list(guidance["actions"])
        checks = list(guidance["checks"])

    return {
        "schema_version": 1,
        "status": "review_required",
        "diagnostic_code": code,
        "observed_signals": normalized,
        "probable_cause": cause,
        "confidence": confidence,
        "recommended_actions": actions,
        "verification_checklist": checks,
        "workspace_mutated": False,
        "patch_generated": False,
        "execution_requested": False,
        "human_review_required": True,
        "limitations": [
            "This is a bounded diagnosis from safe Runner signals, not a proven root cause.",
            "Raw stdout and stderr are not retained or used as repair instructions.",
            "No source files are read or changed by this analysis.",
        ],
    }
