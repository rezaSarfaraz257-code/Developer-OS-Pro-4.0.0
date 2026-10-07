#!/usr/bin/env python3
"""Static release gate for Developer OS.

This validates the repository package before the real CI/host build is executed.
"""
from __future__ import annotations

import ast
import json
import py_compile
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
errors: list[str] = []

for forbidden in ("node_modules", "__pycache__"):
    if (ROOT / forbidden).exists():
        errors.append(f"release artifact present: {forbidden}")

for path in ROOT.rglob("*.sqlite3"):
    errors.append(f"local database must not ship: {path.relative_to(ROOT)}")

for path in list((ROOT / "Backend").rglob("*.py")) + list((ROOT / "Runner").rglob("*.py")):
    if "__pycache__" in path.parts:
        continue
    try:
        py_compile.compile(str(path), doraise=True)
    except Exception as exc:
        errors.append(f"python compile failed: {path.relative_to(ROOT)}: {exc}")

for rel in ("Frontend/package.json", "Frontend/package-lock.json"):
    path = ROOT / rel
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"invalid JSON: {rel}: {exc}")

for path in list((ROOT / "Frontend/src").rglob("*.jsx")) + list((ROOT / "Frontend/src").rglob("*.js")):
    text = path.read_text(encoding="utf-8", errors="replace")
    for match in re.finditer(r"""(?:from|import)\s*["'](\.{1,2}/[^"']+)["']""", text):
        spec = match.group(1)
        base = path.parent / spec
        candidates = [
            base, Path(str(base) + ".js"), Path(str(base) + ".jsx"),
            Path(str(base) + ".css"), base / "index.js", base / "index.jsx",
        ]
        if not any(candidate.exists() for candidate in candidates):
            errors.append(f"missing frontend import: {path.relative_to(ROOT)} -> {spec}")

# Security-sensitive static checks. These are release blockers because the
# platform executes untrusted developer code and handles SaaS credentials.
for root_name in ("Backend", "Runner"):
    root = ROOT / root_name
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        if re.search(r"\bshell\s*=\s*True\b|\bos\.system\s*\(", source):
            errors.append(f"unsafe process primitive detected: {path.relative_to(ROOT)}")

# Migration filenames may share a numeric prefix when Django has an explicit
# branch/merge topology. The release gate must reject unresolved migration
# conflicts, not valid branches that are later joined by a merge migration.
migration_dir = ROOT / "Backend/api/migrations"
migration_files = {p.stem: p for p in migration_dir.glob("[0-9][0-9][0-9][0-9]_*.py")}
migration_dependencies = {}
for name, path in migration_files.items():
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        deps = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                if any(isinstance(t, ast.Name) and t.id == "dependencies" for t in node.targets):
                    value = node.value
                    if isinstance(value, (ast.List, ast.Tuple)):
                        for item in value.elts:
                            if isinstance(item, (ast.List, ast.Tuple)) and len(item.elts) >= 2:
                                app, dep = item.elts[0], item.elts[1]
                                if (
                                    isinstance(app, ast.Constant) and app.value == "api"
                                    and isinstance(dep, ast.Constant) and isinstance(dep.value, str)
                                ):
                                    deps.append(dep.value)
        migration_dependencies[name] = deps
    except Exception as exc:
        errors.append(f"migration dependency parse failed: {path.relative_to(ROOT)}: {exc}")

children = {name: [] for name in migration_files}
for child, deps in migration_dependencies.items():
    for dep in deps:
        if dep in children:
            children[dep].append(child)

def descendants(start):
    seen = set()
    stack = list(children.get(start, ()))
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(children.get(node, ()))
    return seen

migration_numbers = {}
for name in migration_files:
    number = name.split("_", 1)[0]
    migration_numbers.setdefault(number, []).append(name)

for number, names in migration_numbers.items():
    if len(names) < 2:
        continue
    reachable = {name: descendants(name) | {name} for name in names}
    common = set.intersection(*(reachable[name] for name in names))
    if not common:
        joined = ", ".join(sorted(names))
        errors.append(f"unresolved migration conflict {number}: {joined}")

# Never allow obvious credential material into tracked release sources.
secret_patterns = (r"sk-[A-Za-z0-9]{20,}", r"AKIA[0-9A-Z]{16}")
for path in list((ROOT / "Backend").rglob("*.py")) + list((ROOT / "Runner").rglob("*.py")):
    source = path.read_text(encoding="utf-8", errors="replace")
    for pattern in secret_patterns:
        if re.search(pattern, source):
            errors.append(f"possible credential material in source: {path.relative_to(ROOT)}")

required_release_files = (
    "Frontend/package.json",
    "Frontend/package-lock.json",
    "Backend/requirements.txt",
    "Runner/requirements.txt",
    "Runner/test_main.py",
    ".github/workflows/ci.yml",
    "e2e/smoke.mjs",
    "scripts/production_validate.mjs",
)
for rel in required_release_files:
    if not (ROOT / rel).exists():
        errors.append(f"required release file missing: {rel}")

for rel in ("README.md", "FINAL_RELEASE.md", "DEPLOYMENT.md"):
    text = (ROOT / rel).read_text(encoding="utf-8", errors="replace").lower()
    if "prototype / mvp" in text or "professional mvp" in text:
        errors.append(f"obsolete MVP release language remains in {rel}")

if errors:
    print("RELEASE GATE: FAIL")
    for error in errors:
        print(f"- {error}")
    sys.exit(1)

print("RELEASE GATE: PASS")
print("Static package checks passed. Run the real CI/frontend build/backend tests on the target environment.")
