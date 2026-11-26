"""Evidence-driven primitives for the Phase 6 Reliable Repair Engine.

These functions are deliberately side-effect free. Callers must obtain evidence
from the authenticated Runner transport (never from Agent prose), persist the
normalized result against the durable job/workspace records, and perform all
workspace mutations through the existing revision-bound approval path.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import PurePosixPath


EVIDENCE_SCHEMA_VERSION = 1
MAX_STACK_TRACE_CHARS = 16_000
MAX_STRUCTURED_ERRORS = 100
MAX_DIFF_CHARS = 64_000
MAX_PATCH_FILES = 20

EXECUTION_STATUSES = frozenset({
    "succeeded", "failed", "timed_out", "cancelled",
    "infrastructure_failed", "sandbox_failed", "resource_exhausted",
})
FAILURE_CATEGORIES = frozenset({
    "syntax", "type", "dependency", "build", "test", "runtime",
    "configuration", "permission", "infrastructure", "sandbox",
    "resource_exhaustion", "unknown",
})

_EVIDENCE_KEYS = frozenset({
    "schema_version", "evidence_origin", "execution_id", "workspace_id",
    "workspace_revision", "job_id", "execution_status", "exit_code",
    "termination_signal", "stdout_reference", "stderr_reference",
    "structured_errors", "stack_trace", "source_location",
    "execution_environment", "resource_usage", "timestamps",
    "artifact_checksums",
})
_ERROR_KEYS = frozenset({"code", "message", "source_location", "evidence_reference"})
_LOCATION_KEYS = frozenset({"path", "line", "column", "symbol"})
_TIMESTAMP_KEYS = frozenset({"started_at", "finished_at"})
_ENVIRONMENT_KEYS = frozenset({"runner_version", "os", "architecture", "runtime", "runtime_version", "sandbox_profile"})
_RESOURCE_KEYS = frozenset({"duration_ms", "cpu_time_ms", "peak_memory_bytes", "output_bytes"})
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.I),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|secret|password)\s*[:=]\s*['\"][^'\"\s]{8,}"),
)
_DIFF_HEADER = re.compile(r"^(---|\\+\\+\\+) ([ab])/(.+)$")
_HUNK_HEADER = re.compile(r"^@@ -\\d+(?:,\\d+)? \\+\\d+(?:,\\d+)? @@(?: .*)?$")
_STACK_FRAME = re.compile(r'File ["\']([^"\']+)["\'], line (\\d+)(?:, in ([^\\n]+))?')


class RepairContractError(ValueError):
    """Raised when evidence or a repair artifact violates its strict contract."""


def _positive_id(value, field):
    if type(value) is not int or value < 1:
        raise RepairContractError(f"{field} must be a positive integer.")
    return value


def _revision(value):
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and value and len(value) <= 128 and not any(ch.isspace() for ch in value):
        return value
    raise RepairContractError("workspace_revision must be a non-negative integer or opaque revision string.")


def _safe_relative_path(value):
    if not isinstance(value, str) or not value or len(value) > 512 or "\x00" in value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and all(part not in {"", ".", ".."} for part in value.split("/"))
        and ".git" not in {part.casefold() for part in path.parts}
    )


def _bounded_text(value, field, limit, *, allow_empty=False):
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise RepairContractError(f"{field} must be a bounded string.")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise RepairContractError(f"{field} contains invalid Unicode.") from exc
    if not allow_empty and not value.strip():
        raise RepairContractError(f"{field} cannot be empty.")
    return value


def _iso_timestamp(value, field):
    if not isinstance(value, str) or len(value) > 64:
        raise RepairContractError(f"{field} must be an ISO-8601 timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RepairContractError(f"{field} must be an ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None:
        raise RepairContractError(f"{field} must include a timezone.")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _redact_secrets(value):
    result = value
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub("[REDACTED_SECRET]", result)
    return result


def normalize_runner_evidence(payload, *, expected_execution_id, expected_workspace_id,
                              expected_workspace_revision, expected_job_id):
    """Validate and canonicalize a Runner response against server-known identities.

    IMPORTANT: this validates shape and correlation, not authenticity. Only call
    it with a response received from the authenticated Runner service; never pass
    Agent-generated JSON or user-submitted evidence as the payload.
    """
    if not isinstance(payload, dict) or set(payload) != _EVIDENCE_KEYS:
        raise RepairContractError("Runner evidence has missing or unexpected fields.")
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != EVIDENCE_SCHEMA_VERSION:
        raise RepairContractError("Unsupported Runner evidence schema version.")
    if payload.get("evidence_origin") != "runner":
        raise RepairContractError("Evidence origin must be the authenticated Runner.")
    if payload.get("execution_id") != expected_execution_id:
        raise RepairContractError("Execution identity mismatch.")
    if type(payload.get("workspace_id")) is not int or payload["workspace_id"] != expected_workspace_id:
        raise RepairContractError("Workspace identity mismatch.")
    if payload.get("workspace_revision") != expected_workspace_revision:
        raise RepairContractError("Workspace revision mismatch.")
    if type(payload.get("job_id")) is not int or payload["job_id"] != expected_job_id:
        raise RepairContractError("Job identity mismatch.")

    status = payload.get("execution_status")
    if not isinstance(status, str) or status not in EXECUTION_STATUSES:
        raise RepairContractError("Unsupported execution status.")
    exit_code = payload.get("exit_code")
    if exit_code is not None and type(exit_code) is not int:
        raise RepairContractError("exit_code must be an integer or null.")
    if status == "succeeded" and exit_code != 0:
        raise RepairContractError("A successful execution must have exit_code 0.")
    if status != "succeeded" and exit_code == 0:
        raise RepairContractError("A failed execution cannot report exit_code 0.")
    signal = payload.get("termination_signal")
    if signal is not None:
        _bounded_text(signal, "termination_signal", 64)

    refs = {}
    for field in ("stdout_reference", "stderr_reference"):
        value = payload.get(field)
        if value is not None:
            refs[field] = _bounded_text(value, field, 512)

    errors = payload.get("structured_errors")
    if not isinstance(errors, list) or len(errors) > MAX_STRUCTURED_ERRORS:
        raise RepairContractError("structured_errors must be a bounded list.")
    normalized_errors = []
    for item in errors:
        if not isinstance(item, dict) or set(item) != _ERROR_KEYS:
            raise RepairContractError("A structured error has an invalid shape.")
        code = _bounded_text(item["code"], "structured error code", 80)
        message = _redact_secrets(_bounded_text(item["message"], "structured error message", 2_000))
        location = item["source_location"]
        if location is not None:
            location = _normalize_source_location(location)
        evidence_ref = item["evidence_reference"]
        if evidence_ref is not None:
            evidence_ref = _bounded_text(evidence_ref, "evidence_reference", 512)
        normalized_errors.append({
            "code": code, "message": message,
            "source_location": location, "evidence_reference": evidence_ref,
        })

    stack = payload.get("stack_trace")
    if stack is not None:
        stack = _redact_secrets(_bounded_text(stack, "stack_trace", MAX_STACK_TRACE_CHARS, allow_empty=True))

    source_location = payload.get("source_location")
    if source_location is not None:
        source_location = _normalize_source_location(source_location)

    environment = payload.get("execution_environment")
    if not isinstance(environment, dict) or set(environment) - _ENVIRONMENT_KEYS:
        raise RepairContractError("execution_environment contains unsupported fields.")
    normalized_environment = {}
    for key, value in environment.items():
        normalized_environment[key] = _bounded_text(value, f"execution_environment.{key}", 160)

    usage = payload.get("resource_usage")
    if not isinstance(usage, dict) or set(usage) - _RESOURCE_KEYS:
        raise RepairContractError("resource_usage contains unsupported fields.")
    normalized_usage = {}
    for key, value in usage.items():
        if type(value) is not int or value < 0:
            raise RepairContractError(f"resource_usage.{key} must be a non-negative integer.")
        normalized_usage[key] = value

    timestamps = payload.get("timestamps")
    if not isinstance(timestamps, dict) or set(timestamps) != _TIMESTAMP_KEYS:
        raise RepairContractError("timestamps must contain started_at and finished_at.")
    started = _iso_timestamp(timestamps["started_at"], "timestamps.started_at")
    finished = _iso_timestamp(timestamps["finished_at"], "timestamps.finished_at")
    if datetime.fromisoformat(finished.replace("Z", "+00:00")) < datetime.fromisoformat(started.replace("Z", "+00:00")):
        raise RepairContractError("finished_at cannot precede started_at.")

    checksums = payload.get("artifact_checksums")
    if not isinstance(checksums, dict) or len(checksums) > 100:
        raise RepairContractError("artifact_checksums must be a bounded mapping.")
    normalized_checksums = {}
    for path, digest in checksums.items():
        if not _safe_relative_path(path) or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise RepairContractError("Artifact checksum entry is invalid.")
        normalized_checksums[path] = digest

    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "evidence_origin": "runner",
        "execution_id": expected_execution_id,
        "workspace_id": expected_workspace_id,
        "workspace_revision": expected_workspace_revision,
        "job_id": expected_job_id,
        "execution_status": status,
        "exit_code": exit_code,
        "termination_signal": signal,
        "stdout_reference": refs.get("stdout_reference"),
        "stderr_reference": refs.get("stderr_reference"),
        "structured_errors": normalized_errors,
        "stack_trace": stack,
        "source_location": source_location,
        "execution_environment": normalized_environment,
        "resource_usage": normalized_usage,
        "timestamps": {"started_at": started, "finished_at": finished},
        "artifact_checksums": normalized_checksums,
    }


def _normalize_source_location(value):
    if not isinstance(value, dict) or set(value) - _LOCATION_KEYS or "path" not in value:
        raise RepairContractError("source_location has an invalid shape.")
    path = value["path"]
    if not _safe_relative_path(path):
        raise RepairContractError("source_location path must be repository-relative.")
    result = {"path": path}
    for field in ("line", "column"):
        number = value.get(field)
        if number is not None:
            if type(number) is not int or number < 1:
                raise RepairContractError(f"source_location.{field} must be a positive integer.")
            result[field] = number
    symbol = value.get("symbol")
    if symbol is not None:
        result["symbol"] = _bounded_text(symbol, "source_location.symbol", 240)
    return result


def classify_execution_failure(evidence):
    """Classify only normalized Runner evidence; no Agent claim is accepted."""
    if not isinstance(evidence, dict) or evidence.get("evidence_origin") != "runner":
        raise RepairContractError("Failure classification requires normalized Runner evidence.")
    status = evidence.get("execution_status")
    if status == "succeeded" and evidence.get("exit_code") == 0:
        return {"category": None, "confidence": "none", "reason": "Execution succeeded; no failure to classify."}
    if status == "timed_out" or any(
        isinstance(error, dict) and error.get("code") in {"timeout", "deadline_exceeded"}
        for error in evidence.get("structured_errors", [])
    ):
        category = "resource_exhaustion"
    elif status == "resource_exhausted":
        category = "resource_exhaustion"
    elif status == "sandbox_failed" or any(
        isinstance(error, dict) and "sandbox" in error.get("code", "").casefold()
        for error in evidence.get("structured_errors", [])
    ):
        category = "sandbox"
    elif status == "infrastructure_failed":
        category = "infrastructure"
    else:
        codes = [error.get("code", "").casefold() for error in evidence.get("structured_errors", []) if isinstance(error, dict)]
        mapping = (
            (("syntax", "syntax_error", "parse_error"), "syntax"),
            (("type", "type_error", "typecheck"), "type"),
            (("dependency", "module_not_found", "import_error"), "dependency"),
            (("build", "compile_error"), "build"),
            (("test", "assertion"), "test"),
            (("permission", "access_denied"), "permission"),
            (("config", "configuration"), "configuration"),
            (("runtime", "exception"), "runtime"),
        )
        category = next((target for markers, target in mapping if any(any(marker in code for marker in markers) for code in codes)), "unknown")
    return {
        "category": category,
        "confidence": "high" if category != "unknown" else "insufficient",
        "reason": "Runner status and structured error codes support this category." if category != "unknown" else "Runner evidence does not establish a specific failure category.",
    }


def analyze_root_cause(evidence, *, changed_paths=()):
    """Return evidence-linked candidate causes; unknowns remain explicitly uncertain."""
    classification = classify_execution_failure(evidence)
    if classification["category"] is None:
        raise RepairContractError("Successful executions do not have a repair root cause.")
    changed = {path for path in changed_paths if _safe_relative_path(path)}
    candidates = []
    for index, error in enumerate(evidence.get("structured_errors", [])):
        if not isinstance(error, dict):
            continue
        location = error.get("source_location")
        path = location.get("path") if isinstance(location, dict) else None
        if path and changed and path not in changed:
            relation = "outside_changed_files"
        elif path:
            relation = "changed_file" if path in changed else "workspace_file"
        else:
            relation = "unlocated"
        candidates.append({
            "candidate_id": f"runner-error-{index + 1}",
            "category": classification["category"],
            "message": error.get("message", ""),
            "source_location": location,
            "evidence_reference": error.get("evidence_reference"),
            "change_relation": relation,
            "confidence": "moderate" if path and relation != "outside_changed_files" else "low",
        })
    if not candidates and evidence.get("source_location"):
        candidates.append({
            "candidate_id": "runner-source-location",
            "category": classification["category"],
            "message": "Runner reported a source location but did not provide a structured root-cause message.",
            "source_location": evidence["source_location"],
            "evidence_reference": evidence.get("stderr_reference"),
            "change_relation": (
                "changed_file" if evidence["source_location"]["path"] in changed
                else "workspace_file"
            ),
            "confidence": "low",
        })
    return {
        "classification": classification,
        "candidates": candidates,
        "conclusion": "candidate_causes_available" if candidates else "insufficient_evidence",
        "uncertainty": None if candidates else "No structured error or source location supports a root-cause conclusion.",
    }


def _patch_path_allowed(path):
    if not _safe_relative_path(path):
        return False
    parts = [part.casefold() for part in path.split("/")]
    basename = parts[-1]
    protected_dirs = {".git", ".github", ".gitlab", ".circleci", ".buildkite"}
    protected_files = {
        ".env", ".env.local", ".env.production", "package-lock.json", "pnpm-lock.yaml",
        "yarn.lock", "bun.lock", "bun.lockb", "poetry.lock", "uv.lock", "pipfile.lock",
        "cargo.lock", "go.sum", "composer.lock", "gemfile.lock", "dockerfile",
        "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml",
        "render.yaml", "fly.toml", "vercel.json", "netlify.toml", "procfile",
        "nginx.conf", "seccomp-bwrap.json", "developer-os-runner",
    }
    return not any(part in protected_dirs for part in parts) and basename not in protected_files and not basename.startswith("dockerfile.")


def scan_patch_for_secrets(unified_diff):
    """Conservative scan of added diff lines; returns findings without echoing secrets."""
    if not isinstance(unified_diff, str):
        raise RepairContractError("Patch must be text.")
    findings = []
    for line_number, line in enumerate(unified_diff.splitlines(), 1):
        if not line.startswith("+") or line.startswith("+++"):
            continue
        for pattern in _SECRET_PATTERNS:
            if pattern.search(line[1:]):
                findings.append({"line": line_number, "rule": "possible_secret"})
                break
    return findings


def validate_unified_patch(unified_diff, *, target_revision, expected_revision, workspace_files):
    """Validate a bounded unified diff against one exact workspace snapshot.

    The validator rejects creates/deletes/renames and protected paths in this
    first contract version. The patch is never applied here.
    """
    if target_revision != expected_revision:
        raise RepairContractError("Patch target revision is stale.")
    if not isinstance(workspace_files, dict):
        raise RepairContractError("An authoritative workspace snapshot is required.")
    if not isinstance(unified_diff, str) or not unified_diff or len(unified_diff) > MAX_DIFF_CHARS:
        raise RepairContractError("Patch is empty or exceeds the configured size limit.")
    if "\x00" in unified_diff or "GIT binary patch" in unified_diff or "Binary files " in unified_diff:
        raise RepairContractError("Binary or malformed patches are not supported.")
    lines = unified_diff.splitlines()
    if not lines or not lines[0].startswith("diff --git "):
        raise RepairContractError("Patch must use a git unified-diff format.")

    files = []
    current = None
    hunk_seen = False
    for line in lines:
        if line.startswith("diff --git "):
            if current is not None and not hunk_seen:
                raise RepairContractError("Every changed file must contain at least one hunk.")
            current = {"old": None, "new": None}
            files.append(current)
            hunk_seen = False
            if len(files) > MAX_PATCH_FILES:
                raise RepairContractError("Patch changes too many files.")
            continue
        if line.startswith("--- ") or line.startswith("+++ "):
            match = _DIFF_HEADER.match(line)
            if not match:
                raise RepairContractError("Patch contains an invalid file header.")
            marker, side, path = match.groups()
            if path == "/dev/null" or not _patch_path_allowed(path):
                raise RepairContractError("Patch contains a create, delete, or protected path.")
            key = "old" if marker == "---" else "new"
            if current is None or current[key] is not None:
                raise RepairContractError("Patch file headers are malformed.")
            current[key] = path
            continue
        if line.startswith("@@ "):
            if current is None or current["old"] is None or current["new"] is None or current["old"] != current["new"]:
                raise RepairContractError("Patch renames and malformed hunks are not supported.")
            if not _HUNK_HEADER.match(line):
                raise RepairContractError("Patch hunk header is malformed.")
            hunk_seen = True
            continue
        if line.startswith(("index ", "new file mode ", "deleted file mode ", "old mode ", "new mode ", "rename from ", "rename to ", "similarity index ", "dissimilarity index ")):
            raise RepairContractError("Patch metadata for creates, deletes, renames, and mode changes is not allowed.")
    if current is not None and not hunk_seen:
        raise RepairContractError("Every changed file must contain at least one hunk.")
    paths = [item["old"] for item in files]
    if not files or len(paths) != len(set(paths)):
        raise RepairContractError("Patch must contain unique file paths.")
    for path in paths:
        if path not in workspace_files or not isinstance(workspace_files[path], str):
            raise RepairContractError("Patch target is not present in the authoritative workspace snapshot.")
    secret_findings = scan_patch_for_secrets(unified_diff)
    if secret_findings:
        raise RepairContractError("Patch contains possible secret material.")
    return {
        "target_revision": target_revision,
        "changed_paths": paths,
        "patch_sha256": hashlib.sha256(unified_diff.encode("utf-8")).hexdigest(),
        "patch_bytes": len(unified_diff.encode("utf-8")),
        "secret_scan": "passed",
        "validated": True,
    }


def validate_revision_bound_approval(approval, *, current_revision, patch_sha256, now=None):
    """Fail closed unless approval binds the exact revision and exact patch hash."""
    if not isinstance(approval, dict):
        return False
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise RepairContractError("Approval validation clock must be timezone-aware.")
    try:
        expires = _iso_timestamp(approval.get("expires_at"), "approval.expires_at")
    except RepairContractError:
        return False
    approved_at = approval.get("approved_at")
    if not isinstance(approved_at, str):
        return False
    try:
        approved = _iso_timestamp(approved_at, "approval.approved_at")
    except RepairContractError:
        return False
    return (
        approval.get("approved") is True
        and approval.get("workspace_revision") == current_revision
        and approval.get("patch_sha256") == patch_sha256
        and datetime.fromisoformat(approved.replace("Z", "+00:00")) <= now
        and datetime.fromisoformat(expires.replace("Z", "+00:00")) > now
    )


def verification_passed(result, *, expected_revision):
    """A repair passes only with independent, revision-bound test and security evidence."""
    if not isinstance(result, dict) or result.get("evidence_origin") != "runner":
        return False
    if result.get("workspace_revision") != expected_revision or result.get("execution_status") != "succeeded" or result.get("exit_code") != 0:
        return False
    checks = result.get("checks")
    if not isinstance(checks, dict) or set(checks) != {"targeted_tests", "regression_tests", "security"}:
        return False
    return all(
        isinstance(check, dict)
        and check.get("status") == "passed"
        and isinstance(check.get("evidence_reference"), str)
        and bool(check["evidence_reference"])
        for check in checks.values()
    )
