#!/usr/bin/env python3
"""Fail-fast production configuration validator."""
import os, sys

required = [
    "DJANGO_SECRET_KEY", "DATABASE_URL", "ALLOWED_HOSTS", "CORS_ALLOWED_ORIGINS",
    "CSRF_TRUSTED_ORIGINS", "IDE_RUNNER_TOKEN", "FRONTEND_URL", "DEFAULT_FROM_EMAIL",
]
stripe_keys = [
    "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "STRIPE_PRICE_PRO",
    "STRIPE_PRICE_PRO_ANNUAL", "STRIPE_PRICE_TEAM", "STRIPE_PRICE_TEAM_ANNUAL",
    "STRIPE_PRICE_ENTERPRISE",
]
conditional = {
    "Stripe": stripe_keys,
    "SMTP": ["EMAIL_HOST", "EMAIL_HOST_USER", "EMAIL_HOST_PASSWORD"],
    "S3": ["S3_BUCKET", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY"],
}
missing = [x for x in required if not os.getenv(x)]
if missing:
    print("MISSING REQUIRED:", ", ".join(missing)); sys.exit(2)

email_verification_required = os.getenv("EMAIL_VERIFICATION_REQUIRED", "true").lower() in {"1", "true", "yes", "on"}
if not email_verification_required:
    print("WARNING: EMAIL_VERIFICATION_REQUIRED is disabled.")
else:
    smtp_keys = conditional["SMTP"]
    missing_smtp = [key for key in smtp_keys if not os.getenv(key)]
    if missing_smtp:
        print("MISSING REQUIRED EMAIL DELIVERY:", ", ".join(missing_smtp))
        sys.exit(4)
    if os.getenv("EMAIL_BACKEND", "").endswith("console.EmailBackend"):
        print("INVALID PRODUCTION EMAIL_BACKEND: console email delivery is not production-safe.")
        sys.exit(4)

# Production billing is required by default so a deployment cannot silently
# pass preflight while every paid plan is unavailable. Non-production checks
# remain opt-in; an explicit false is reserved for deliberate no-billing
# deployments and must not be used for a release intended to accept payments.
stripe_default = "true" if os.getenv("ENVIRONMENT", "").strip().lower() in {"production", "prod"} else "false"
stripe_required = os.getenv("REQUIRE_STRIPE_BILLING", stripe_default).strip().lower() in {"1", "true", "yes", "on"}
stripe_configured = [bool(os.getenv(key, "").strip()) for key in stripe_keys]
if stripe_required and not all(stripe_configured):
    absent = [key for key, configured in zip(stripe_keys, stripe_configured) if not configured]
    print("MISSING REQUIRED STRIPE BILLING CONFIG:", ", ".join(absent))
    sys.exit(3)

for name, keys in conditional.items():
    configured = sum(bool(os.getenv(k, "").strip()) for k in keys)
    if configured and configured != len(keys):
        print(f"INCOMPLETE {name} CONFIG:", ", ".join(k for k in keys if not os.getenv(k, "").strip())); sys.exit(3)

if all(stripe_configured):
    malformed = []
    if not os.getenv("STRIPE_SECRET_KEY", "").startswith(("sk_test_", "sk_live_")):
        malformed.append("STRIPE_SECRET_KEY (expected sk_test_ or sk_live_ prefix)")
    if not os.getenv("STRIPE_WEBHOOK_SECRET", "").startswith("whsec_"):
        malformed.append("STRIPE_WEBHOOK_SECRET (expected whsec_ prefix)")
    for key in ("STRIPE_PRICE_PRO", "STRIPE_PRICE_PRO_ANNUAL", "STRIPE_PRICE_TEAM", "STRIPE_PRICE_TEAM_ANNUAL", "STRIPE_PRICE_ENTERPRISE"):
        if not os.getenv(key, "").startswith("price_"):
            malformed.append(f"{key} (expected price_ prefix)")
    if malformed:
        print("INVALID STRIPE CONFIG:", "; ".join(malformed))
        sys.exit(3)

print("PRODUCTION PREFLIGHT: PASS")
