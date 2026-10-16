#!/usr/bin/env python3
"""Fail-fast checks for the production/operational contract.

These checks validate only the operational boundary owned by the
production-hardening layer. They do not replace runtime CI or application tests.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
errors: list[str] = []

requirements = (ROOT / "Backend/requirements.txt").read_text(encoding="utf-8-sig")
if not re.search(r"^psycopg\[.*pool.*\]", requirements, re.MULTILINE):
    errors.append("Backend requirements must include psycopg pool support for bounded production DB pooling")

dockerfile = (ROOT / "Backend/Dockerfile").read_text(encoding="utf-8", errors="replace")
if "HEALTHCHECK" not in dockerfile or "/api/health/ready/" not in dockerfile:
    errors.append("Backend Dockerfile must expose the HTTP readiness healthcheck")
if "USER appuser" not in dockerfile:
    errors.append("Backend container must run as a non-root user")
if 'CMD ["python", "entrypoint.py"]' not in dockerfile:
    errors.append("Backend container must use the serialized production entrypoint")

urls = (ROOT / "Backend/api/urls.py").read_text(encoding="utf-8", errors="replace")
if 'path("health/live/", operational_health.liveness_api' not in urls:
    errors.append("Dedicated dependency-free liveness probe must be registered")
if 'from . import operational_health' not in urls:
    errors.append("Operational health module must be explicitly imported")

slo_doc = (ROOT / "docs/RELIABILITY_SLO.md").read_text(encoding="utf-8", errors="replace") if (ROOT / "docs/RELIABILITY_SLO.md").exists() else ""
for marker in ("Service-level objectives", "Error-budget policy", "Recovery point objective", "Recovery time objective"):
    if marker not in slo_doc:
        errors.append(f"reliability contract missing required section: {marker}")

compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8", errors="replace")
if "runner_internal:" not in compose or "internal: true" not in compose:
    errors.append("Runner network must remain internal-only")
if "RUNNER_ALLOW_NETWORK: ${RUNNER_ALLOW_NETWORK:-false}" not in compose:
    errors.append("Runner outbound network must default to disabled")
if "SEED_CATALOG_ON_STARTUP: ${SEED_CATALOG_ON_STARTUP:-false}" not in compose:
    errors.append("Production Compose must not mutate catalog data by default at startup")

for rel in (
    ".github/workflows/ci.yml",
    ".github/workflows/security.yml",
    ".github/workflows/dependency-review.yml",
    ".github/dependabot.yml",
):
    if not (ROOT / rel).exists():
        errors.append(f"required operational supply-chain control missing: {rel}")

if errors:
    print("OPERATIONAL POLICY GATE: FAIL")
    for error in errors:
        print(f"- {error}")
    sys.exit(1)

print("OPERATIONAL POLICY GATE: PASS")
