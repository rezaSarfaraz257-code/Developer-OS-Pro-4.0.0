"""Bounded, deterministic, read-only tools for approved engineering-agent tasks.

No tool in this registry executes project code, accesses the network, or mutates
workspace state. Results contain metadata/findings only, never source excerpts.
"""
import json
import re


MAX_WORKSPACE_FILES = 500
MAX_FILE_BYTES = 256_000
MAX_FINDINGS = 50


def _safe_path(path):
    normalized = str(path).replace("\\", "/").strip("/")
    parts = normalized.lower().split("/")
    name = parts[-1] if parts else ""
    return not (
        name == ".env"
        or name.startswith(".env.")
        or name in {".npmrc", ".pypirc", "id_rsa", "id_ed25519", "credentials", "secrets.json", "service-account.json"}
        or name.startswith(("credentials.", "secret.", "secrets."))
        or name.endswith((".pem", ".key", ".p12", ".pfx", ".keystore", ".jks"))
        or any(part in {".aws", ".ssh", "secrets", "credentials"} for part in parts[:-1])
    )


def _files(workspace):
    raw = workspace.files if isinstance(workspace.files, dict) else {}
    return {
        str(path).replace("\\", "/")[:500]: value
        for path, value in list(raw.items())[:MAX_WORKSPACE_FILES]
        if _safe_path(path) and isinstance(value, str) and len(value.encode("utf-8", "ignore")) <= MAX_FILE_BYTES
    }


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


def analyze_dependencies(workspace):
    files = _files(workspace)
    dependencies = []
    manifests = []
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
                    dependencies.extend({"name": str(key)[:120], "version": str(value)[:80], "group": group}
                                        for key, value in values.items())
        elif name in {"requirements.txt", "requirements-dev.txt"}:
            manifests.append({"path": path, "status": "parsed"})
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith(("#", "-")):
                    continue
                match = re.match(r"^([A-Za-z0-9_.-]+)\s*(.*)$", line)
                if match:
                    dependencies.append({"name": match.group(1)[:120], "version": match.group(2)[:80], "group": "python"})
        elif name == "pyproject.toml":
            manifests.append({"path": path, "status": "detected_static_only"})
    return {"tool": "analyze_dependencies", "manifests": manifests[:50],
            "dependency_count": len(dependencies), "dependencies": dependencies[:300], "read_only": True}


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
