#!/usr/bin/env python3
"""Fail-fast production configuration validator."""
import os, sys

required = [
    "DJANGO_SECRET_KEY", "DATABASE_URL", "ALLOWED_HOSTS", "CORS_ALLOWED_ORIGINS",
    "CSRF_TRUSTED_ORIGINS", "IDE_RUNNER_TOKEN", "FRONTEND_URL", "DEFAULT_FROM_EMAIL",
]
conditional = {
    "Stripe": ["STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "STRIPE_PRICE_PRO", "STRIPE_PRICE_TEAM"],
    "SMTP": ["EMAIL_HOST", "EMAIL_HOST_USER", "EMAIL_HOST_PASSWORD"],
    "S3": ["S3_BUCKET", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY"],
}
missing = [x for x in required if not os.getenv(x)]
if missing:
    print("MISSING REQUIRED:", ", ".join(missing)); sys.exit(2)
if os.getenv("EMAIL_VERIFICATION_REQUIRED", "true").lower() not in {"1","true","yes","on"}:
    print("WARNING: EMAIL_VERIFICATION_REQUIRED is disabled.")
for name, keys in conditional.items():
    configured = sum(bool(os.getenv(k)) for k in keys)
    if configured and configured != len(keys):
        print(f"INCOMPLETE {name} CONFIG:", ", ".join(k for k in keys if not os.getenv(k))); sys.exit(3)
print("PRODUCTION PREFLIGHT: PASS")
