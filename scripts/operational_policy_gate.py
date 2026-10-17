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
if "PYTHONHASHSEED=random" not in dockerfile:
    errors.append("Backend container must randomize Python hash seeds for process-level hardening")

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

health = (ROOT / "Backend/api/operational_health.py").read_text(encoding="utf-8", errors="replace")
for marker in ('RELEASE_VERSION', 'RELEASE_SHA', 'Cache-Control', 'no-store'):
    if marker not in health:
        errors.append(f"liveness endpoint missing release/cache contract marker: {marker}")
health_tests = ROOT / "Backend/api/test_health.py"
if not health_tests.exists():
    errors.append("liveness endpoint regression tests must remain present")
else:
    health_test_source = health_tests.read_text(encoding="utf-8", errors="replace")
    for marker in (
        "test_liveness_returns_process_contract_and_no_store_cache",
        "test_liveness_has_safe_defaults",
        "test_liveness_rejects_non_get_requests",
    ):
        if marker not in health_test_source:
            errors.append(f"liveness regression test missing: {marker}")

env_example = (ROOT / ".env.example").read_text(encoding="utf-8", errors="replace")
for marker in ("RELEASE_VERSION=", "RELEASE_SHA="):
    if marker not in env_example:
        errors.append(f".env.example must document release identity: {marker}")

slo_doc = (ROOT / "docs/RELIABILITY_SLO.md").read_text(encoding="utf-8", errors="replace") if (ROOT / "docs/RELIABILITY_SLO.md").exists() else ""
for marker in ("Service-level objectives", "Error-budget policy", "Recovery point objective", "Recovery time objective"):
    if marker not in slo_doc:
        errors.append(f"reliability contract missing required section: {marker}")

operations_doc = (ROOT / "docs/OPERATIONS.md").read_text(encoding="utf-8", errors="replace") if (ROOT / "docs/OPERATIONS.md").exists() else ""
for marker in ("release", "backup", "rollback", "runner"):
    if marker.lower() not in operations_doc.lower():
        errors.append(f"operations runbook missing required operational topic: {marker}")

compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8", errors="replace")
if "runner_internal:" not in compose or "internal: true" not in compose:
    errors.append("Runner network must remain internal-only")
if "RUNNER_ALLOW_NETWORK: ${RUNNER_ALLOW_NETWORK:-false}" not in compose:
    errors.append("Runner outbound network must default to disabled")
if "SEED_CATALOG_ON_STARTUP: ${SEED_CATALOG_ON_STARTUP:-false}" not in compose:
    errors.append("Production Compose must not mutate catalog data by default at startup")

logging_module = (ROOT / "Backend/api/logging.py").read_text(encoding="utf-8", errors="replace")
for marker in ("_SECRET_PATTERNS", "[REDACTED]", "record.exc_info"):
    if marker not in logging_module:
        errors.append(f"structured logging must retain secret redaction and exception handling: {marker}")
logging_tests = ROOT / "Backend/api/test_logging.py"
if not logging_tests.exists():
    errors.append("structured logging redaction tests must remain present")

required_files = (
    ".github/workflows/ci.yml",
    ".github/workflows/security.yml",
    ".github/workflows/container-security.yml",
    ".github/workflows/dependency-review.yml",
    ".github/workflows/scorecard.yml",
    ".github/workflows/node-compatibility.yml",
    ".github/workflows/production-e2e.yml",
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
for marker in ("anchore/sbom-action@v0.24.0", "format: spdx-json", "aquasecurity/trivy-action@v0.36.0", "severity: CRITICAL,HIGH", "scanners: vuln,secret,misconfig", "exit-code: \"1\""):
    if marker not in container_workflow:
        errors.append(f"container security workflow missing required blocking/evidence control: {marker}")
if "if: always()" not in container_workflow:
    errors.append("container security findings must still be uploaded when the blocking scan fails")

scorecard_workflow = (ROOT / ".github/workflows/scorecard.yml").read_text(encoding="utf-8", errors="replace") if (ROOT / ".github/workflows/scorecard.yml").exists() else ""
if "publish_results: true" not in scorecard_workflow or "id-token: write" not in scorecard_workflow:
    errors.append("OpenSSF Scorecard must publish authenticated supply-chain results using OIDC")

dep_review = (ROOT / ".github/workflows/dependency-review.yml").read_text(encoding="utf-8", errors="replace")
if "actions/dependency-review-action@v5" not in dep_review:
    errors.append("Dependency Review must use the current action major")

node_compatibility = (ROOT / ".github/workflows/node-compatibility.yml").read_text(encoding="utf-8", errors="replace")
for marker in ('node: ["22", "24"]', "npm ci", "npm run lint", "npm run build", "npm test"):
    if marker not in node_compatibility:
        errors.append(f"Node LTS compatibility workflow missing required validation: {marker}")

production_e2e = (ROOT / ".github/workflows/production-e2e.yml").read_text(encoding="utf-8", errors="replace")
for marker in ("PRODUCTION_BASE_URL", "PRODUCTION_BILLING_WEBHOOK_SECRET", "environment:", "production_validate.mjs"):
    if marker not in production_e2e:
        errors.append(f"production black-box workflow missing fail-closed control: {marker}")

ci_workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8", errors="replace")
if "python scripts/operational_policy_gate.py" not in ci_workflow:
    errors.append("main CI must execute the operational policy gate")
if "python scripts/release_gate.py" not in ci_workflow:
    errors.append("main CI must execute the static release gate")
if "sha256sum -c" not in ci_workflow:
    errors.append("backup restore CI must verify the backup checksum before restore")

if errors:
    print("OPERATIONAL POLICY GATE: FAIL")
    for error in errors:
        print(f"- {error}")
    sys.exit(1)

print("OPERATIONAL POLICY GATE: PASS")
