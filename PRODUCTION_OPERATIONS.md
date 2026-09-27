# Developer OS Production Operations

## Deployment

1. Provision PostgreSQL, Redis and optional S3-compatible storage.
2. Create a production `.env` from `.env.example`.
3. Set a strong `DJANGO_SECRET_KEY`, runner token, GitHub secrets, Stripe secrets and SMTP credentials.
4. Set `EMAIL_VERIFICATION_REQUIRED=true`.
5. Set `SECURE_SSL_REDIRECT=true` behind a TLS reverse proxy.
6. Set `ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS`, `CSRF_TRUSTED_ORIGINS` to exact production origins.
7. Run migrations before starting application workers.
8. Start backend, worker, runner, frontend/reverse proxy and Redis.
9. Run readiness and smoke checks.

## Background jobs

The application uses a durable database queue. Start the worker with:

```bash
python manage.py worker --sleep 1
```

Jobs retry with exponential backoff and become `failed` after their configured retry budget.

## Backups

Use the existing `scripts/backup_postgres.sh` and `scripts/restore_postgres.sh` with encrypted off-site backup storage. Test restores on a schedule; a backup that has never been restored is not a verified recovery mechanism.

## Incident response

Use the `Incident` model and `/api/status/` for customer-visible incidents. Record start time, severity, investigation, mitigation, monitoring and resolution. Keep a separate incident timeline in your operational system when the deployment is large enough to require it.

## Security

Run CodeQL, dependency review, Django deploy checks, dependency scanning, security tests and an independent penetration test before exposing the service to untrusted users at scale.
