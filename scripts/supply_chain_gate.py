#!/usr/bin/env python3
"""Deterministic supply-chain policy checks.

The gate intentionally does not mutate dependencies. Vulnerability scanners
remain responsible for advisories; this gate verifies repository hygiene and
lockfile integrity before those scanners run.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
errors: list[str] = []

LOCK = ROOT / "Frontend/package-lock.json"
PKG = ROOT / "Frontend/package.json"
REQ = ROOT / "Backend/requirements.txt"

if not PKG.exists() or not LOCK.exists():
    errors.append("Frontend package manifest/lockfile pair is incomplete")
else:
    try:
        package = json.loads(PKG.read_text(encoding="utf-8"))
        lock = json.loads(LOCK.read_text(encoding="utf-8"))
        if lock.get("lockfileVersion") not in {2, 3}:
            errors.append(f"unsupported npm lockfileVersion: {lock.get('lockfileVersion')}")
        root = lock.get("packages", {}).get("")
        if not isinstance(root, dict):
            errors.append("npm lockfile root package metadata is missing")
        else:
            for section in ("dependencies", "devDependencies", "optionalDependencies"):
                declared = package.get(section, {})
                locked = root.get(section, {})
                missing = sorted(set(declared) - set(locked))
                if missing:
                    errors.append(f"npm lockfile missing declared {section}: {', '.join(missing)}")
    except Exception as exc:
        errors.append(f"npm manifest/lockfile parse failed: {exc}")

if REQ.exists():
    for number, line in enumerate(REQ.read_text(encoding="utf-8").splitlines(), 1):
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        if "://" in value and not value.startswith(("http://", "https://")):
            errors.append(f"unsupported dependency source at requirements.txt:{number}")
        if value.startswith(("-e ", "--editable ")):
            errors.append(f"editable dependency forbidden at requirements.txt:{number}")

for rel in ("Frontend/package-lock.json", "Backend/requirements.txt", "Runner/requirements.txt"):
    path = ROOT / rel
    if not path.exists():
        errors.append(f"dependency manifest missing: {rel}")

if errors:
    print("SUPPLY CHAIN GATE: FAIL")
    for error in errors:
        print(f"- {error}")
    raise SystemExit(1)

digest = hashlib.sha256(LOCK.read_bytes()).hexdigest()
print("SUPPLY CHAIN GATE: PASS")
print(f"npm_lock_sha256={digest}")
