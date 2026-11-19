"""Bounded, deterministic, read-only tools for approved engineering-agent tasks.

No tool in this registry executes project code, accesses the network, or mutates
workspace state. Results contain metadata/findings only, never source excerpts.
"""
import json
import re
from itertools import islice


MAX_WORKSPACE_FILES = 500
MAX_FILE_BYTES = 256_000
MAX_WORKSPACE_TOTAL_BYTES = 2_000_000
MAX_FINDINGS = 50
MAX_DEPENDENCY_RESULTS = 300


def _safe_path(path):
    """Reject malformed/traversal paths before applying secret-file policy."""
    if not isinstance(path, str) or not path or len(path) > 500 or "\x00" in path:
        return False
    normalized = path.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[a-zA-Z]:", normalized):
        return False
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return False
    lowered = [part.lower() for part in parts]
    name = lowered[-1]
    # Exclude well-known user-level CLI credential stores. These files often
    # contain opaque tokens that cannot be reliably detected by content regexes.
    private_store_prefixes = (
        (".config", "gh"),
        (".config", "gcloud"),
        (".config", "azure"),
        (".config", "containers"),
        (".config", "rclone"),
        (".config", "op"),
        (".config", "1password"),
        (".config", "oci"),
        (".config", "terraform"),
    )
    private_state_file = (
        name.endswith((".tfstate", ".tfstate.backup", ".tfvars", ".tfvars.json"))
        or name in {
            "application_default_credentials.json",
            "accesstokens.json",
            "hosts.yml",
            "hosts.yaml",
            "config.json",
        } and any(
            tuple(lowered[:len(prefix)]) == prefix
            for prefix in private_store_prefixes
        )
    )
    return not (
        name == ".env"
        or name.startswith(".env.")
        or name in {".npmrc", ".pypirc", ".netrc", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "known_hosts", "authorized_keys", "credentials", "secrets.json", "service-account.json"}
        or name.startswith(("credentials.", "secret.", "secrets."))
        or name.endswith((".pem", ".key", ".p12", ".pfx", ".keystore", ".jks"))
        or any(part in {".aws", ".ssh", "secrets", "credentials"} for part in lowered[:-1])
        or any(tuple(lowered[:len(prefix)]) == prefix for prefix in private_store_prefixes)
        or private_state_file
    )


def _files(workspace):
    """Return a bounded safe snapshot without copying the entire workspace map."""
    raw = workspace.files if isinstance(workspace.files, dict) else {}
    selected = {}
    total_bytes = 0
    for path, value in islice(raw.items(), MAX_WORKSPACE_FILES):
        if not _safe_path(path) or not isinstance(value, str):
            continue
        # Cheap character bound avoids encoding arbitrarily large strings.
        if len(value) > MAX_FILE_BYTES:
            continue
        try:
            size_bytes = len(value.encode("utf-8"))
        except UnicodeEncodeError:
            # JSON strings may contain lone surrogate code points; skip malformed
            # text rather than crashing every read-only agent tool invocation.
            continue
        if size_bytes > MAX_FILE_BYTES:
            continue
        if total_bytes + size_bytes > MAX_WORKSPACE_TOTAL_BYTES:
            continue
        selected[str(path).replace("\\", "/")[:500]] = value
        total_bytes += size_bytes
    return selected

def inspect_workspace(workspace):
    files = _files(workspace)
    extensions = {}
    for path in files:
        name = path.rsplit("/", 1)[-1]
        ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else "[no extension]"
        extensions[ext] = extensions.get(ext, 0) + 1
    return {
        "tool": "inspect_workspace",
        "workspace": {"id": workspace.id, "name": workspace.name, "language": workspace.language,
                      "framework": workspace.framework, "runtime": workspace.runtime,
                      "revision": workspace.revision},
        "file_count": len(files),
        "file_paths": sorted(files)[:MAX_WORKSPACE_FILES],
        "extensions": extensions,
        "read_only": True,
    }


def _record_python_requirement(requirement, group, record_dependency):
    """Record a PEP 508-like requirement without evaluating markers or URLs."""
    if not isinstance(requirement, str):
        return
    value = requirement.strip()
    if not value or value.startswith(("-", "#")):
        return
    match = re.match(
        r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\\[[^\\]]+\\])?\\s*(.*)$",
        value,
    )
    if match:
        # An unpinned requirement is valid; preserve that fact explicitly.
        record_dependency(match.group(1), match.group(2).strip() or "*", group)


def _record_poetry_dependency(name, value, group, record_dependency):
    """Read only the declarative version field from Poetry dependency tables."""
    if not isinstance(name, str) or name.casefold() == "python":
        return
    if isinstance(value, str):
        record_dependency(name, value, group)
    elif isinstance(value, dict):
        version = value.get("version")
        if isinstance(version, str):
            record_dependency(name, version, group)


def analyze_dependencies(workspace):
    """Parse bounded JSON, requirements, and TOML manifests without executing code."""
    import tomllib

    files = _files(workspace)
    dependencies = []
    dependency_count = 0
    manifests = []

    def record_dependency(name, version, group):
        nonlocal dependency_count
        # Manifests are untrusted workspace input. Do not coerce malformed
        # objects into misleading dependency names or versions.
        if not isinstance(name, str) or not name.strip():
            return
        if not isinstance(version, str) or not version.strip():
            return
        dependency_count += 1
        # Bound response memory/output even for adversarial dependency manifests.
        if len(dependencies) < MAX_DEPENDENCY_RESULTS:
            dependencies.append({
                "name": name.strip()[:120],
                "version": version.strip()[:160],
                "group": str(group)[:80],
            })

    for path, content in files.items():
        name = path.rsplit("/", 1)[-1].lower()
        if name == "package.json":
            try:
                data = json.loads(content)
            except (TypeError, ValueError):
                manifests.append({"path": path, "status": "invalid_json"})
                continue
            if not isinstance(data, dict):
                manifests.append({"path": path, "status": "invalid_manifest_shape"})
                continue
            manifests.append({"path": path, "status": "parsed"})
            for group in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
                values = data.get(group, {})
                if isinstance(values, dict):
                    for key, value in values.items():
                        record_dependency(key, value, group)
        elif name in {"requirements.txt", "requirements-dev.txt"}:
            manifests.append({"path": path, "status": "parsed"})
            for line in content.splitlines():
                _record_python_requirement(line, "python", record_dependency)
        elif name == "pyproject.toml":
            try:
                data = tomllib.loads(content)
            except (TypeError, ValueError, tomllib.TOMLDecodeError):
                manifests.append({"path": path, "status": "invalid_toml"})
                continue
            if not isinstance(data, dict):
                manifests.append({"path": path, "status": "invalid_manifest_shape"})
                continue
            manifests.append({"path": path, "status": "parsed"})

            # PEP 621 project dependencies and optional dependency groups.
            project = data.get("project", {})
            if isinstance(project, dict):
                for requirement in project.get("dependencies", []) if isinstance(project.get("dependencies", []), list) else []:
                    _record_python_requirement(requirement, "project", record_dependency)
                optional = project.get("optional-dependencies", {})
                if isinstance(optional, dict):
                    for group, requirements in optional.items():
                        if isinstance(requirements, list):
                            for requirement in requirements:
                                _record_python_requirement(requirement, f"optional:{group}", record_dependency)

            # PEP 735 dependency groups. Only literal requirement strings are
            # interpreted; include-group directives and arbitrary objects are
            # deliberately not expanded or executed.
            groups = data.get("dependency-groups", {})
            if isinstance(groups, dict):
                for group, requirements in groups.items():
                    if isinstance(requirements, list):
                        for requirement in requirements:
                            _record_python_requirement(requirement, f"group:{group}", record_dependency)

            # Poetry's legacy and named dependency groups.
            tool = data.get("tool", {})
            poetry = tool.get("poetry", {}) if isinstance(tool, dict) else {}
            if isinstance(poetry, dict):
                for dependency_name, value in (poetry.get("dependencies", {}) or {}).items() if isinstance(poetry.get("dependencies", {}), dict) else []:
                    _record_poetry_dependency(dependency_name, value, "poetry", record_dependency)
                legacy_dev = poetry.get("dev-dependencies", {})
                if isinstance(legacy_dev, dict):
                    for dependency_name, value in legacy_dev.items():
                        _record_poetry_dependency(dependency_name, value, "poetry:dev", record_dependency)
                poetry_groups = poetry.get("group", {})
                if isinstance(poetry_groups, dict):
                    for group, group_data in poetry_groups.items():
                        group_dependencies = group_data.get("dependencies", {}) if isinstance(group_data, dict) else {}
                        if isinstance(group_dependencies, dict):
                            for dependency_name, value in group_dependencies.items():
                                _record_poetry_dependency(dependency_name, value, f"poetry:{group}", record_dependency)

    return {
        "tool": "analyze_dependencies",
        "manifests": manifests[:50],
        "dependency_count": dependency_count,
        "dependencies": dependencies,
        "dependencies_truncated": dependency_count > len(dependencies),
        "read_only": True,
    }

def review_security(workspace):
    files = _files(workspace)
    rules = [
        ("debug_enabled", re.compile(r"(?im)^\s*DEBUG\s*=\s*True\b"), "high", "Debug mode appears enabled in source configuration."),
        ("wildcard_hosts", re.compile(r"(?im)^\s*ALLOWED_HOSTS\s*=\s*\[[^\]]*\*"), "high", "Wildcard host configuration appears present."),
        ("shell_true", re.compile(r"(?i)\bshell\s*=\s*True\b"), "high", "A subprocess call appears to enable shell interpretation."),
        ("dynamic_eval", re.compile(r"\b(?:eval|exec)\s*\("), "medium", "Dynamic code evaluation appears in source."),
        ("os_system", re.compile(r"\bos\.system\s*\("), "high", "Direct operating-system shell invocation appears in source."),
        ("hardcoded_secret", re.compile(r"""(?i)\b(?:api[_-]?key|client[_-]?secret|password|secret[_-]?key)\b\s*[:=]\s*[\'"][^\'"]{8,}[\'"]"""), "high", "A possible hard-coded credential assignment appears in source."),
    ]
    findings = []
    for path, content in files.items():
        for line_number, line in enumerate(content.splitlines(), start=1):
            for rule_id, pattern, severity, message in rules:
                if pattern.search(line):
                    findings.append({"rule": rule_id, "severity": severity, "path": path,
                                     "line": line_number, "message": message})
                    if len(findings) >= MAX_FINDINGS:
                        break
            if len(findings) >= MAX_FINDINGS:
                break
        if len(findings) >= MAX_FINDINGS:
            break
    return {"tool": "review_security", "finding_count": len(findings), "findings": findings,
            "truncated": len(findings) >= MAX_FINDINGS, "read_only": True}


def analyze_schema(workspace):
    files = _files(workspace)
    models = []
    class_pattern = re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)\s*\([^)]*models\.Model[^)]*\)\s*:", re.MULTILINE)
    field_pattern = re.compile(r"^\s{4,}([A-Za-z_][A-Za-z0-9_]*)\s*=\s*models\.([A-Za-z][A-Za-z0-9_]*)\s*\(", re.MULTILINE)
    for path, content in files.items():
        if path.rsplit("/", 1)[-1].lower() not in {"models.py", "models"}:
            continue
        for match in class_pattern.finditer(content):
            start = match.end()
            next_class = re.search(r"^\s*class\s+", content[start:], re.MULTILINE)
            body = content[start:start + next_class.start()] if next_class else content[start:]
            fields = [{"name": field.group(1), "type": field.group(2)}
                      for field in field_pattern.finditer(body)][:80]
            models.append({"name": match.group(1), "path": path, "fields": fields})
    return {"tool": "analyze_schema", "model_count": len(models), "models": models[:100], "read_only": True}


TOOL_REGISTRY = {
    "inspect_workspace": {"description": "Inspect bounded workspace metadata and safe file inventory.", "handler": inspect_workspace},
    "analyze_dependencies": {"description": "Parse supported dependency manifests without installing packages.", "handler": analyze_dependencies},
    "review_security": {"description": "Run bounded static checks for selected insecure source patterns.", "handler": review_security},
    "analyze_schema": {"description": "Inspect Django model declarations without accessing database records.", "handler": analyze_schema},
}


def execute_readonly_tool(tool_name, workspace):
    tool = TOOL_REGISTRY.get(str(tool_name))
    if not tool:
        raise ValueError("Unsupported read-only agent tool.")
    result = tool["handler"](workspace)
    # Keep responses bounded even when the workspace contains a large project.
    serialized = json.dumps(result, ensure_ascii=False, default=str)
    if len(serialized) > 48_000:
        raise ValueError("Tool output exceeded the response safety limit.")
    return result
