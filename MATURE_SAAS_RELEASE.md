# Developer OS — Mature SaaS Release Pack

This release adds a real SaaS control plane instead of a percentage-only checklist.

## Implemented in source

- Email verification with hashed, expiring single-use tokens
- Password reset and password change
- Account recovery flow
- MFA/TOTP with hashed backup codes
- Login attempt recording, throttling and suspicious-new-IP security email
- JWT refresh-token session registry and revoke-all
- Transactional email queue backed by a durable database job queue
- Redis-backed production cache adapter
- Organization billing records, seats/quantity, invoice/payment models and Stripe checkout/update integration
- Stripe webhook support for organization subscriptions and invoice/payment lifecycle
- Organization billing portal data
- Usage/entitlement infrastructure
- Background worker with retries and dead-job state
- Data export jobs and account deletion workflow
- S3-compatible object-storage adapter with local fallback and presigned downloads
- Support tickets and admin support view
- Product analytics: activation, event volume, retention proxies and paid conversion
- Public incident/status endpoint and operational metrics endpoint
- Audit/security/session persistence
- Production readiness checks for database, secret, hosts, email, cache and worker state
- Docker Redis + worker services
- CI environment hooks for mature auth and production checks
- Frontend registration, verification, password recovery and MFA login fields

## Production configuration that must be supplied by the host

These are real external dependencies and cannot be fabricated inside a source archive:

1. SMTP provider and verified sender domain
2. Stripe account, products/prices, webhook endpoint and tax configuration where applicable
3. PostgreSQL production instance
4. Redis production instance
5. S3/R2/MinIO bucket and credentials if object storage is used
6. TLS certificate / reverse proxy / DNS
7. Production secrets and secret manager
8. Monitoring/error tracking provider and alert destinations
9. Backup storage and a tested restore target
10. Staging and production environments

## Required production verification

Run on the deployed commit:

```bash
cd Backend
python manage.py check --deploy
python manage.py makemigrations --check --dry-run
python manage.py migrate --noinput
python manage.py test --verbosity 2

cd ../Frontend
npm ci
npm run lint
npm run build
npm test
```

Then run the Docker stack and `node e2e/smoke.mjs <base-url>`.

## Important

A source repository cannot honestly claim that penetration testing, load capacity, RPO/RTO, uptime, incident response, DNS/TLS, external billing, SMTP delivery or disaster recovery have been proven until those external systems are actually configured and exercised. This release provides the application-side implementation and the gates required to validate them.
