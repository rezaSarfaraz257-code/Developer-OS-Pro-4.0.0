# Production email worker for Developer OS

## Why this service is required

Registration, verification resend, password reset, and security alerts are stored as durable `BackgroundJob` rows. A successful HTTP response means the request was accepted/queued; it does **not** mean an email has been delivered. The `python manage.py worker` process claims queued jobs and sends them through Django's configured email backend.

The repository's `render.yaml` currently defines the IDE runner only; it does not declare the Django email/background-job worker. If the Render deployment has no running worker, queued verification messages will remain queued.

## Lower-cost single-service option

If an additional always-on Render Background Worker is not affordable, the API container now supports a supervised, opt-in worker process. Set this environment variable on the **existing Django API service**:

```text
RUN_BACKGROUND_WORKER_IN_WEB=true
```

Deploy the commit containing this support. The entrypoint starts `python manage.py worker --sleep 1` alongside Gunicorn after migrations complete. If either process exits, the entrypoint stops the other process and exits non-zero so the container restarts instead of silently claiming readiness. The normal database heartbeat and readiness checks remain authoritative; this mode does not fake a heartbeat or bypass a failed database connection.

This is a cost-saving compromise, not the preferred scaling model: every API replica runs one worker, so size the database connection limit and replica count accordingly. The job-claiming code uses database row locks with `skip_locked` to prevent workers from claiming the same queued job. If a dedicated worker already runs, leave this option disabled unless you intentionally want multiple consumers.

## Render configuration

Create an always-on **Background Worker** service for this same repository:

- Runtime: Docker
- Dockerfile path from repository root: `Backend/Dockerfile`
- Docker context: `Backend`
- Start command: `python manage.py worker --sleep 1`
- Region: use the same region as the `DOS` API and PostgreSQL database where possible.
- Do not configure an HTTP health-check port for this worker.

Copy the **same environment variables and values** from the existing `DOS` backend service into the worker. Do not paste secrets into GitHub or this document. In particular, the worker must connect to the same PostgreSQL database and Redis/cache as the API.

Verify these settings exist and are correct in the backend and worker environments:

- `ENVIRONMENT=production` and `DEBUG=false`
- `DJANGO_SECRET_KEY` (same value as the API)
- `DATABASE_URL` (same PostgreSQL database as the API)
- `CACHE_URL` (same configured cache/Redis as the API)
- `FRONTEND_URL` (the live HTTPS frontend origin)
- `EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend`
- `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`
- `EMAIL_USE_TLS` / `EMAIL_USE_SSL` configured for the selected SMTP provider, not both enabled simultaneously
- `DEFAULT_FROM_EMAIL` using a sender address permitted by the provider
- `ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS`, and `CSRF_TRUSTED_ORIGINS` matching the production API/frontend setup

Do not switch production to the console email backend. It prints email content to process logs rather than delivering messages. A real SMTP provider and its credentials are required. Render Background Workers may require a paid always-on instance; do not substitute a short-lived web request or a scheduled one-off task for a durable worker.

## Verification checklist

1. Deploy the worker and confirm its logs show the command remains running without repeated crashes.
2. Create a test account using an address you control.
3. Confirm a new `email` job moves from `queued` to `running` and then `succeeded`.
4. If it fails, inspect the worker's server-side error for SMTP authentication, connection, sender-domain, or provider rejection errors. Never expose the verification token or SMTP password in logs or screenshots.
5. Confirm the email arrives and that the link verifies once; reusing the same link must fail.
6. Check spam/junk and the provider's delivery logs if the job succeeds but the mailbox remains empty. A successful SMTP handoff is not a guarantee of inbox placement.
