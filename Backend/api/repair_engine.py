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
MAX_PATCH_SOURCE_BYTES = 8 * 1024 * 1024
MAX_PATCH_TOTAL_SOURCE_BYTES = 16 * 1024 * 1024
MAX_REPAIR_HISTORY = 20
MAX_REPAIR_ATTEMPTS = 3
MAX_APPROVAL_TTL_SECONDS = 60 * 60
MAX_RESOURCE_USAGE_VALUE = (2 ** 63) - 1

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
    re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?(?:ENCRYPTED )?PRIVATE KEY-----"
        r"[\s\S]*?-----END (?:RSA |EC |OPENSSH |DSA )?(?:ENCRYPTED )?PRIVATE KEY-----",
        re.I,
    ),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?(?:ENCRYPTED )?PRIVATE KEY-----", re.I),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b(?:xox[baprs]|npm|pypi)-[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{20,}"),
    re.compile(r"""(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|aws[_-]?secret[_-]?access[_-]?key|secret|password)\s*[:=]\s*(?:['"][^'"]{8,}['"]|[A-Za-z0-9_./+=-]{20,})"""),
)
_DIFF_HEADER = re.compile(r"^(---|\+\+\+) ([ab])/(.+)$")
_HUNK_HEADER = re.compile(r"^@@ -([0-9]{1,10})(?:,([0-9]{1,10}))? \+([0-9]{1,10})(?:,([0-9]{1,10}))? @@(?: .*)?$")


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
    if (
        not isinstance(value, str) or not value or len(value) > 512
        or "\x00" in value or "\\" in value
        or any(ord(char) < 32 for char in value)
        or re.match(r"^[A-Za-z]:", value)
    ):
        return False
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and all(part not in {"", ".", ".."} and not part.endswith((".", " ")) and ":" not in part for part in value.split("/"))
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


def _artifact_reference(value, field):
    value = _bounded_text(value, field, 512)
    match = re.fullmatch(
        r"(?:artifact|runner|blob)://([A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*)",
        value,
    )
    segments = match.group(1).split("/") if match else []
    if (
        not match
        or any(segment in {".", ".."} for segment in segments)
        or any(pattern.search(value) for pattern in _SECRET_PATTERNS)
    ):
        raise RepairContractError(f"{field} is not a safe artifact reference.")
    return value


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
    if not isinstance(expected_execution_id, str) or not expected_execution_id or len(expected_execution_id) > 128:
        raise RepairContractError("Expected execution identity is invalid.")
    _positive_id(expected_workspace_id, "expected_workspace_id")
    _positive_id(expected_job_id, "expected_job_id")
    _revision(expected_workspace_revision)
    if payload.get("execution_id") != expected_execution_id:
        raise RepairContractError("Execution identity mismatch.")
    if type(payload.get("workspace_id")) is not int or payload["workspace_id"] != expected_workspace_id:
        raise RepairContractError("Workspace identity mismatch.")
    if (
        type(payload.get("workspace_revision")) is not type(expected_workspace_revision)
        or payload.get("workspace_revision") != expected_workspace_revision
    ):
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
        signal = _redact_secrets(_bounded_text(signal, "termination_signal", 64))

    refs = {}
    for field in ("stdout_reference", "stderr_reference"):
        value = payload.get(field)
        if value is not None:
            refs[field] = _artifact_reference(value, field)

    errors = payload.get("structured_errors")
    if not isinstance(errors, list) or len(errors) > MAX_STRUCTURED_ERRORS:
        raise RepairContractError("structured_errors must be a bounded list.")
    normalized_errors = []
    for item in errors:
        if not isinstance(item, dict) or set(item) != _ERROR_KEYS:
            raise RepairContractError("A structured error has an invalid shape.")
        code = _redact_secrets(_bounded_text(item["code"], "structured error code", 80))
        message = _redact_secrets(_bounded_text(item["message"], "structured error message", 2_000))
        location = item["source_location"]
        if location is not None:
            location = _normalize_source_location(location)
        evidence_ref = item["evidence_reference"]
        if evidence_ref is not None:
            evidence_ref = _artifact_reference(evidence_ref, "evidence_reference")
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
        normalized_environment[key] = _redact_secrets(
            _bounded_text(value, f"execution_environment.{key}", 160)
        )

    usage = payload.get("resource_usage")
    if not isinstance(usage, dict) or set(usage) - _RESOURCE_KEYS:
        raise RepairContractError("resource_usage contains unsupported fields.")
    normalized_usage = {}
    for key, value in usage.items():
        if type(value) is not int or not 0 <= value <= MAX_RESOURCE_USAGE_VALUE:
            raise RepairContractError(f"resource_usage.{key} must be a bounded non-negative integer.")
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
        result["symbol"] = _redact_secrets(
            _bounded_text(symbol, "source_location.symbol", 240)
        )
    return result


def _revalidate_runner_evidence(evidence):
    """Re-normalize a Runner payload before any downstream consumer uses it."""
    if not isinstance(evidence, dict):
        raise RepairContractError("Failure analysis requires normalized Runner evidence.")
    try:
        return normalize_runner_evidence(
            evidence,
            expected_execution_id=evidence.get("execution_id"),
            expected_workspace_id=evidence.get("workspace_id"),
            expected_workspace_revision=evidence.get("workspace_revision"),
            expected_job_id=evidence.get("job_id"),
        )
    except (AttributeError, TypeError) as exc:
        raise RepairContractError("Failure analysis requires normalized Runner evidence.") from exc


def classify_execution_failure(evidence):
    """Classify only fully normalized Runner evidence; no Agent claim is accepted."""
    evidence = _revalidate_runner_evidence(evidence)
    status = evidence.get("execution_status")
    exit_code = evidence.get("exit_code")
    if (
        (exit_code is not None and type(exit_code) is not int)
        or (status == "succeeded" and exit_code != 0)
        or (status != "succeeded" and exit_code == 0)
    ):
        raise RepairContractError("Runner status and exit code are inconsistent.")
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
    evidence = _revalidate_runner_evidence(evidence)
    classification = classify_execution_failure(evidence)
    if classification["category"] is None:
        raise RepairContractError("Successful executions do not have a repair root cause.")
    if (
        isinstance(changed_paths, (str, bytes))
        or not isinstance(changed_paths, (list, tuple, set, frozenset))
        or len(changed_paths) > 500
        or any(not _safe_relative_path(path) for path in changed_paths)
    ):
        raise RepairContractError("changed_paths must be a bounded collection of safe relative paths.")
    changed = set(changed_paths)
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
            "evidence_reference": (
                error.get("evidence_reference")
                or evidence.get("stderr_reference")
                or evidence.get("stdout_reference")
            ),
            "change_relation": relation,
            "confidence": (
                "moderate"
                if path and relation != "outside_changed_files"
                and (error.get("evidence_reference") or evidence.get("stderr_reference") or evidence.get("stdout_reference"))
                else "low"
            ),
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
    protected_dirs = {
        ".git", ".github", ".gitlab", ".circleci", ".buildkite",
        ".ssh", ".aws", ".azure", ".config", "secrets",
    }
    protected_files = {
        ".env", ".npmrc", ".pypirc", ".netrc", ".git-credentials",
        "id_rsa", "id_ed25519", "id_ecdsa", "authorized_keys",
        "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock",
        "bun.lockb", "npm-shrinkwrap.json", "poetry.lock", "uv.lock",
        "pipfile.lock", "pdm.lock", "pip-tools.lock", "cargo.lock",
        "go.sum", "composer.lock", "gemfile.lock", "dockerfile",
        "docker-compose.yml", "docker-compose.yaml", "compose.yml",
        "compose.yaml", "render.yaml", "fly.toml", "vercel.json",
        "netlify.toml", "procfile", "nginx.conf", "seccomp-bwrap.json",
        "developer-os-runner", "known_hosts",
    }
    sensitive_suffixes = (".pem", ".key", ".p12", ".pfx", ".jks", ".keystore")
    return (
        not any(part in protected_dirs for part in parts)
        and basename not in protected_files
        and not basename.startswith("dockerfile.")
        and not basename.startswith(".env.")
        and not basename.endswith(sensitive_suffixes)
    )

def scan_patch_for_secrets(unified_diff):
    """Scan added diff lines while recognizing file headers only in header position."""
    if not isinstance(unified_diff, str) or len(unified_diff) > MAX_DIFF_CHARS:
        raise RepairContractError("Patch must be bounded text.")
    findings = []
    header_state = 0
    for line_number, line in enumerate(unified_diff.splitlines(), 1):
        if line.startswith("diff --git "):
            header_state = 1
            continue
        if header_state == 1 and line.startswith("index "):
            continue
        if header_state == 1 and re.fullmatch(r"--- a/.+", line):
            header_state = 2
            continue
        if header_state == 2 and re.fullmatch(r"\+\+\+ b/.+", line):
            header_state = 0
            continue
        if line.startswith("@@ "):
            header_state = 0
            continue
        if header_state and not line.startswith(("index ", "--- a/")):
            header_state = 0
        if not line.startswith("+"):
            continue
        for pattern in _SECRET_PATTERNS:
            if pattern.search(line[1:]):
                findings.append({"line": line_number, "rule": "possible_secret"})
                break
    return findings


def validate_unified_patch(unified_diff, *, target_revision, expected_revision, workspace_files):
    """Validate a bounded git-style unified diff against an exact source snapshot.

    This is validation only: it never applies a patch or mutates a workspace.
    File creation/deletion/rename, mode changes, binary patches, protected
    paths, stale revisions, malformed hunks and context mismatches fail closed.
    """
    _revision(target_revision)
    _revision(expected_revision)
    if type(target_revision) is not type(expected_revision) or target_revision != expected_revision:
        raise RepairContractError("Patch target revision is stale.")
    if not isinstance(workspace_files, dict):
        raise RepairContractError("An authoritative workspace snapshot is required.")
    if not isinstance(unified_diff, str) or not unified_diff or len(unified_diff) > MAX_DIFF_CHARS:
        raise RepairContractError("Patch is empty or exceeds the configured size limit.")
    try:
        encoded = unified_diff.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise RepairContractError("Patch contains invalid Unicode.") from exc
    if "\x00" in unified_diff or "GIT binary patch" in unified_diff or "Binary files " in unified_diff:
        raise RepairContractError("Binary or malformed patches are not supported.")

    total_source_bytes = 0

    def finish_hunk(file_record):
        hunk = file_record.get("active_hunk")
        if hunk is None:
            return
        old_count = sum(1 for line in hunk["lines"] if line.startswith((" ", "-")))
        new_count = sum(1 for line in hunk["lines"] if line.startswith((" ", "+")))
        has_change = any(line.startswith(("+", "-")) for line in hunk["lines"])
        if not has_change:
            raise RepairContractError("Patch hunks must contain at least one added or removed line.")
        if old_count != hunk["old_count"] or new_count != hunk["new_count"]:
            raise RepairContractError("Patch hunk line counts do not match its header.")
        file_record["hunks"].append(hunk)
        file_record["active_hunk"] = None

    def finish_file(file_record):
        nonlocal total_source_bytes
        if file_record is None:
            return
        finish_hunk(file_record)
        path = file_record["git_path"]
        if not file_record["hunks"] or file_record["old"] != path or file_record["new"] != path:
            raise RepairContractError("Patch file headers and hunks are incomplete or inconsistent.")
        if path not in workspace_files or not isinstance(workspace_files[path], str):
            raise RepairContractError("Patch target is not present in the authoritative workspace snapshot.")
        source_text = workspace_files[path]
        if len(source_text) > MAX_PATCH_SOURCE_BYTES or "\x00" in source_text:
            raise RepairContractError("Patch target exceeds the text snapshot safety limit.")
        try:
            source_bytes = source_text.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise RepairContractError("Patch target contains invalid Unicode.") from exc
        if len(source_bytes) > MAX_PATCH_SOURCE_BYTES:
            raise RepairContractError("Patch target exceeds the text snapshot safety limit.")
        total_source_bytes += len(source_bytes)
        if total_source_bytes > MAX_PATCH_TOTAL_SOURCE_BYTES:
            raise RepairContractError("Patched workspace snapshots exceed the aggregate safety limit.")
        source_lines = source_text.splitlines(keepends=True)
        previous_old_end = 0
        previous_new_end = 0
        cumulative_line_delta = 0
        for hunk in file_record["hunks"]:
            start = hunk["old_start"] if hunk["old_count"] == 0 else hunk["old_start"] - 1
            new_start = hunk["new_start"] if hunk["new_count"] == 0 else hunk["new_start"] - 1
            if (
                start < previous_old_end or start < 0 or start > len(source_lines)
                or new_start < previous_new_end or new_start < 0
                or new_start > len(source_lines) + cumulative_line_delta
                or new_start != start + cumulative_line_delta
            ):
                raise RepairContractError("Patch hunks overlap or their new-file offsets are inconsistent.")
            cursor = start
            for line in hunk["lines"]:
                marker, content = line[0], line[1:]
                if marker in {" ", "-"}:
                    if cursor >= len(source_lines) or source_lines[cursor] != content:
                        raise RepairContractError("Patch context does not match the exact workspace snapshot.")
                    cursor += 1
            previous_old_end = start + hunk["old_count"]
            previous_new_end = new_start + hunk["new_count"]
            cumulative_line_delta += hunk["new_count"] - hunk["old_count"]

    files = []
    current = None
    for raw_line in unified_diff.splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        if line.startswith("diff --git "):
            finish_file(current)
            match = re.fullmatch(r"diff --git a/(.+) b/(.+)", line)
            if not match:
                raise RepairContractError("Quoted, renamed or malformed git diff paths are not supported.")
            old_path, new_path = match.groups()
            if old_path != new_path or not _patch_path_allowed(old_path):
                raise RepairContractError("Patch contains a rename or protected path.")
            current = {
                "git_path": old_path, "old": None, "new": None,
                "hunks": [], "active_hunk": None, "index_seen": False,
            }
            files.append(current)
            if len(files) > MAX_PATCH_FILES:
                raise RepairContractError("Patch changes too many files.")
            continue

        if (line.startswith("--- ") or line.startswith("+++ ")) and not (
            current is not None and current.get("active_hunk") is not None
        ):
            if current is None:
                raise RepairContractError("Patch file header appeared before a git diff header.")
            finish_hunk(current)
            match = _DIFF_HEADER.fullmatch(line)
            if not match:
                raise RepairContractError("Patch contains an invalid file header.")
            marker, side, path = match.groups()
            expected_side = "a" if marker == "---" else "b"
            key = "old" if marker == "---" else "new"
            if side != expected_side or path != current["git_path"] or not _patch_path_allowed(path):
                raise RepairContractError("Patch file header does not match its git diff path.")
            if (marker == "---" and current["new"] is not None) or (marker == "+++" and current["old"] is None):
                raise RepairContractError("Patch file headers are out of order.")
            if current[key] is not None:
                raise RepairContractError("Patch file headers are duplicated.")
            current[key] = path
            continue

        if line.startswith("@@ "):
            if current is None or current["old"] is None or current["new"] is None:
                raise RepairContractError("Patch hunk appeared before complete file headers.")
            finish_hunk(current)
            match = _HUNK_HEADER.fullmatch(line)
            if not match:
                raise RepairContractError("Patch hunk header is malformed.")
            old_start, old_count, new_start, new_count = match.groups()
            numeric_values = [
                int(old_start),
                int(old_count) if old_count is not None else 1,
                int(new_start),
                int(new_count) if new_count is not None else 1,
            ]
            if any(value > 2_147_483_647 for value in numeric_values):
                raise RepairContractError("Patch hunk coordinates exceed the supported line range.")
            current["active_hunk"] = {
                "old_start": numeric_values[0],
                "old_count": numeric_values[1],
                "new_start": numeric_values[2],
                "new_count": numeric_values[3],
                "lines": [],
            }
            continue

        if line.startswith("index "):
            if (
                current is None
                or current["old"] is not None
                or current["active_hunk"] is not None
                or current["index_seen"]
                or not re.fullmatch(r"index [a-f0-9]{7,64}\.\.[a-f0-9]{7,64}(?: [0-7]{6})?", line)
            ):
                raise RepairContractError("Patch index metadata is malformed or out of order.")
            current["index_seen"] = True
            continue
        if line.startswith(("\\ No newline at end of file", "new file mode ", "deleted file mode ", "old mode ", "new mode ", "rename from ", "rename to ", "similarity index ", "dissimilarity index ")):
            raise RepairContractError("Unsupported patch metadata or no-newline marker.")
        if current is not None and current.get("active_hunk") is not None:
            if raw_line.startswith((" ", "+", "-")):
                current["active_hunk"]["lines"].append(raw_line)
                continue
        if line.strip():
            raise RepairContractError("Patch contains content outside a recognized unified-diff hunk.")

    finish_file(current)
    paths = [item["git_path"] for item in files]
    if not files or len(paths) != len({path.casefold() for path in paths}):
        raise RepairContractError("Patch must contain unique file paths.")
    secret_findings = scan_patch_for_secrets(unified_diff)
    if secret_findings:
        raise RepairContractError("Patch contains possible secret material.")
    return {
        "target_revision": target_revision,
        "changed_paths": paths,
        "patch_sha256": hashlib.sha256(encoded).hexdigest(),
        "patch_bytes": len(encoded),
        "secret_scan": "passed",
        "source_context": "matched",
        "validated": True,
    }

def validate_revision_bound_approval(approval, *, current_revision, patch_sha256, now=None):
    """Fail closed unless approval binds the exact revision and patch hash."""
    if not isinstance(approval, dict):
        return False
    try:
        _revision(current_revision)
    except RepairContractError:
        return False
    if not isinstance(patch_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", patch_sha256):
        return False
    now = now or datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise RepairContractError("Approval validation clock must be timezone-aware.")
    try:
        expires = _iso_timestamp(approval.get("expires_at"), "approval.expires_at")
        approved = _iso_timestamp(approval.get("approved_at"), "approval.approved_at")
    except RepairContractError:
        return False
    approved_revision = approval.get("workspace_revision")
    revision_matches = (
        type(approved_revision) is type(current_revision)
        and approved_revision == current_revision
    )
    approved_dt = datetime.fromisoformat(approved.replace("Z", "+00:00"))
    expires_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
    return (
        approval.get("approved") is True
        and revision_matches
        and approval.get("patch_sha256") == patch_sha256
        and approved_dt <= now
        and expires_dt > now
        and expires_dt > approved_dt
        and (expires_dt - approved_dt).total_seconds() <= MAX_APPROVAL_TTL_SECONDS
    )

def evaluate_repair_loop(history, *, current_revision, candidate_patch_sha256, max_attempts=MAX_REPAIR_ATTEMPTS):
    """Bound repair retries and reject repeated patches for one exact revision.

    Only attempted mutations count. Rejected proposals do not consume the
    execution budget. The function is pure and never applies or schedules work.
    """
    _revision(current_revision)
    if (
        not isinstance(candidate_patch_sha256, str)
        or not re.fullmatch(r"[a-f0-9]{64}", candidate_patch_sha256)
    ):
        raise RepairContractError("Candidate patch hash is invalid.")
    if type(max_attempts) is not int or not 1 <= max_attempts <= MAX_REPAIR_ATTEMPTS:
        raise RepairContractError("Repair attempt limit is outside the safe policy.")
    if not isinstance(history, list) or len(history) > MAX_REPAIR_HISTORY:
        raise RepairContractError("Repair history is malformed or exceeds its bound.")

    attempted_outcomes = {"applied_failed_verification", "rolled_back", "verified_success"}
    attempts = []
    for record in history:
        if (
            not isinstance(record, dict)
            or set(record) != {"patch_sha256", "outcome", "workspace_revision"}
            or not isinstance(record.get("patch_sha256"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", record["patch_sha256"])
            or not isinstance(record.get("outcome"), str)
            or record["outcome"] not in attempted_outcomes | {"rejected"}
        ):
            raise RepairContractError("Repair history record is invalid.")
        _revision(record["workspace_revision"])
        if (
            type(record["workspace_revision"]) is type(current_revision)
            and record["workspace_revision"] == current_revision
            and record["outcome"] in attempted_outcomes
        ):
            attempts.append(record)

    if any(record["outcome"] == "verified_success" for record in attempts):
        return {"allowed": False, "reason": "revision_already_verified", "attempts": len(attempts)}
    if any(record["patch_sha256"] == candidate_patch_sha256 for record in attempts):
        return {"allowed": False, "reason": "repeated_patch", "attempts": len(attempts)}
    if len(attempts) >= max_attempts:
        return {"allowed": False, "reason": "attempt_limit_reached", "attempts": len(attempts)}
    return {"allowed": True, "reason": "within_repair_budget", "attempts": len(attempts)}

def evaluate_rollback_eligibility(
    verification_evidence,
    *,
    workspace_id,
    current_revision,
    expected_post_revision,
    pre_repair_revision,
    applied_patch_sha256,
    snapshot_reference,
):
    """Decide whether a failed, revision-bound verification permits rollback.

    This is a pure policy check. It never reads a snapshot or mutates a workspace.
    Only a complete Runner verification envelope with at least one failed gate
    can authorize a caller to request rollback of the exact applied patch.
    """
    _positive_id(workspace_id, "workspace_id")
    _revision(current_revision)
    _revision(expected_post_revision)
    _revision(pre_repair_revision)
    if (
        type(current_revision) is not type(expected_post_revision)
        or current_revision != expected_post_revision
        or type(pre_repair_revision) is not type(expected_post_revision)
        or pre_repair_revision == expected_post_revision
    ):
        raise RepairContractError("Rollback revisions are stale or inconsistent.")
    if not isinstance(applied_patch_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", applied_patch_sha256):
        raise RepairContractError("Rollback patch identity is invalid.")
    snapshot_reference = _artifact_reference(snapshot_reference, "rollback snapshot_reference")
    evidence = verification_evidence
    if (
        not isinstance(evidence, dict)
        or evidence.get("evidence_origin") != "runner"
        or type(evidence.get("schema_version")) is not int
        or evidence.get("schema_version") != EVIDENCE_SCHEMA_VERSION
        or type(evidence.get("workspace_id")) is not int
        or evidence.get("workspace_id") != workspace_id
        or type(evidence.get("job_id")) is not int
        or evidence.get("job_id") < 1
        or not isinstance(evidence.get("execution_id"), str)
        or not evidence.get("execution_id")
        or len(evidence["execution_id"]) > 128
        or type(evidence.get("workspace_revision")) is not type(expected_post_revision)
        or evidence.get("workspace_revision") != expected_post_revision
        or evidence.get("patch_sha256") != applied_patch_sha256
        or evidence.get("execution_status") != "succeeded"
        or type(evidence.get("exit_code")) is not int
        or evidence.get("exit_code") != 0
    ):
        raise RepairContractError("Rollback requires complete Runner verification evidence for the current revision.")
    checks = evidence.get("checks")
    required = {"targeted_tests", "regression_tests", "security"}
    if not isinstance(checks, dict) or set(checks) != required:
        raise RepairContractError("Rollback verification evidence is incomplete.")
    failed_gates = []
    for name, check in checks.items():
        if (
            not isinstance(check, dict)
            or set(check) != {"status", "evidence_reference", "execution_id", "workspace_revision"}
            or not isinstance(check.get("status"), str)
            or check.get("status") not in {"passed", "failed"}
            or check.get("execution_id") != evidence["execution_id"]
            or type(check.get("workspace_revision")) is not type(expected_post_revision)
            or check.get("workspace_revision") != expected_post_revision
        ):
            raise RepairContractError("Rollback verification gate evidence is malformed or uncorrelated.")
        _artifact_reference(check.get("evidence_reference"), f"rollback {name} evidence_reference")
        if check["status"] == "failed":
            failed_gates.append(name)
    if not failed_gates:
        return {
            "eligible": False,
            "reason": "verification_passed",
            "failed_gates": [],
            "workspace_id": workspace_id,
            "from_revision": current_revision,
            "target_revision": pre_repair_revision,
            "patch_sha256": applied_patch_sha256,
            "snapshot_reference": snapshot_reference,
        }
    return {
        "eligible": True,
        "reason": "verification_failed",
        "failed_gates": sorted(failed_gates),
        "workspace_id": workspace_id,
        "from_revision": current_revision,
        "target_revision": pre_repair_revision,
        "patch_sha256": applied_patch_sha256,
        "snapshot_reference": snapshot_reference,
    }


def compare_repair_outcomes(before_evidence, after_evidence, verification_evidence):
    """Compare Runner behavior before/after a repair without trusting Agent claims.

    A successful process exit is not a successful repair: all independent
    verification gates must also pass on the exact post-repair revision.
    """
    before_evidence = _revalidate_runner_evidence(before_evidence)
    after_evidence = _revalidate_runner_evidence(after_evidence)
    before = classify_execution_failure(before_evidence)
    after = classify_execution_failure(after_evidence)
    if before["category"] is None:
        raise RepairContractError("Baseline evidence must describe a failed execution.")
    if (
        before_evidence["workspace_id"] != after_evidence["workspace_id"]
        or type(before_evidence["workspace_revision"]) is not type(after_evidence["workspace_revision"])
        or before_evidence["workspace_revision"] == after_evidence["workspace_revision"]
        or before_evidence["execution_id"] == after_evidence["execution_id"]
    ):
        raise RepairContractError("Before/after evidence must identify distinct executions and revisions in one workspace.")
    verified = (
        isinstance(verification_evidence, dict)
        and verification_evidence.get("workspace_id") == after_evidence["workspace_id"]
        and type(verification_evidence.get("workspace_id")) is int
        and verification_evidence.get("execution_id") == after_evidence["execution_id"]
        and verification_evidence.get("job_id") == after_evidence["job_id"]
        and verification_passed(
            verification_evidence,
            expected_revision=after_evidence["workspace_revision"],
        )
    )
    if after["category"] is None:
        outcome = "verified_repair" if verified else "verification_incomplete"
    elif before["category"] == after["category"]:
        outcome = "failure_persists"
    else:
        outcome = "failure_changed"
    return {
        "outcome": outcome,
        "before_category": before["category"],
        "after_category": after["category"],
        "verification_passed": verified,
        "before_execution_id": before_evidence["execution_id"],
        "after_execution_id": after_evidence["execution_id"],
        "before_revision": before_evidence["workspace_revision"],
        "after_revision": after_evidence["workspace_revision"],
    }


def verification_passed(result, *, expected_revision):
    """Require independent, revision-bound Runner evidence for every verification gate."""
    try:
        _revision(expected_revision)
    except RepairContractError:
        return False
    if (
        not isinstance(result, dict)
        or result.get("evidence_origin") != "runner"
        or type(result.get("schema_version")) is not int
        or result.get("schema_version") != EVIDENCE_SCHEMA_VERSION
        or not isinstance(result.get("execution_id"), str)
        or not result.get("execution_id")
        or len(result["execution_id"]) > 128
        or type(result.get("workspace_id")) is not int
        or result.get("workspace_id") < 1
        or type(result.get("job_id")) is not int
        or result.get("job_id") < 1
    ):
        return False
    revision = result.get("workspace_revision")
    if (
        type(revision) is not type(expected_revision)
        or revision != expected_revision
        or result.get("execution_status") != "succeeded"
        or type(result.get("exit_code")) is not int
        or result.get("exit_code") != 0
    ):
        return False
    checks = result.get("checks")
    required = {"targeted_tests", "regression_tests", "security"}
    if not isinstance(checks, dict) or set(checks) != required:
        return False
    for check in checks.values():
        if (
            not isinstance(check, dict)
            or set(check) != {"status", "evidence_reference", "execution_id", "workspace_revision"}
            or check.get("status") != "passed"
            or check.get("execution_id") != result["execution_id"]
            or type(check.get("workspace_revision")) is not type(revision)
            or check.get("workspace_revision") != revision
            or not isinstance(check.get("evidence_reference"), str)
        ):
            return False
        try:
            _artifact_reference(check["evidence_reference"], "verification evidence_reference")
        except RepairContractError:
            return False
    return True
