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

settings = (ROOT / "Backend/x/settings.py").read_text(encoding="utf-8", errors="replace")
if "DB_POOL_MAX_SIZE > 32" not in settings:
    errors.append("Production DB pool must have a hard per-process maximum of 32 connections")
if "DB_POOL_TIMEOUT <= 60" not in settings:
    errors.append("Production DB pool timeout must have a hard upper bound of 60 seconds")

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

required_files = (
    ".github/workflows/ci.yml",
    ".github/workflows/security.yml",
    ".github/workflows/container-security.yml",
    ".github/workflows/dependency-review.yml",
    ".github/workflows/scorecard.yml",
    ".github/dependabot.yml",
    "SECURITY.md",
)
for rel in required_files:
    if not (ROOT / rel).exists():
        errors.append(f"required operational supply-chain control missing: {rel}")

security_workflow = (ROOT / ".github/workflows/security.yml").read_text(encoding="utf-8", errors="replace")
for marker in ("actions/checkout@v7", "actions/setup-python@v7", "actions/setup-node@v7", "github/codeql-action/init@v4", "github/codeql-action/analyze@v4"):
    if marker not in security_workflow:
        errors.append(f"security workflow is not using the current hardened action baseline: {marker}")

container_workflow = (ROOT / ".github/workflows/container-security.yml").read_text(encoding="utf-8", errors="replace") if (ROOT / ".github/workflows/container-security.yml").exists() else ""
for marker in ("aquasecurity/trivy-action@v0.36.0", "severity: CRITICAL,HIGH", "scanners: vuln,secret,misconfig"):
    if marker not in container_workflow:
        errors.append(f"container security workflow missing required hardened scan control: {marker}")

scorecard_workflow = (ROOT / ".github/workflows/scorecard.yml").read_text(encoding="utf-8", errors="replace") if (ROOT / ".github/workflows/scorecard.yml").exists() else ""
if "publish_results: true" not in scorecard_workflow or "id-token: write" not in scorecard_workflow:
    errors.append("OpenSSF Scorecard must publish authenticated supply-chain results using OIDC")

dep_review = (ROOT / ".github/workflows/dependency-review.yml").read_text(encoding="utf-8", errors="replace")
if "actions/dependency-review-action@v5" not in dep_review:
    errors.append("Dependency Review must use the current Node 24 action major")

if errors:
    print("OPERATIONAL POLICY GATE: FAIL")
    for error in errors:
        print(f"- {error}")
    sys.exit(1)

print("OPERATIONAL POLICY GATE: PASS")
