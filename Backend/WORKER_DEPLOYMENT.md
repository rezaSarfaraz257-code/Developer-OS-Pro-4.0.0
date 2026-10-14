# Production email worker for Developer OS

## Why this service is required

Registration, verification resend, password reset, and security alerts are stored as durable `BackgroundJob` rows. A successful HTTP response means the request was accepted/queued; it does **not** mean an email has been delivered. The `python manage.py worker` process claims queued jobs and sends them through Django's configured email backend.

The repository's `render.yaml` currently defines the IDE runner only; it does not declare the Django email/background-job worker. If the Render deployment has no running worker, queued verification messages will remain queued.

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


## No-worker alternative: inline email delivery

For small deployments that cannot run an always-on Background Worker, the API can send newly queued email jobs synchronously after the database transaction commits. Set `EMAIL_DELIVERY_MODE=inline` on the existing Django API service to opt in. The default remains `queued`, preserving the worker-based behavior.

This mode requires a real, working SMTP provider configured with `EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, TLS/SSL settings, and a permitted `DEFAULT_FROM_EMAIL`. It does not provide an email service or SMTP credentials for free. SMTP latency occurs during the request, so use it only for low-volume deployments and monitor failed `BackgroundJob` rows. A delivery failure is recorded as `failed`; it is not silently reported as delivered. Only newly created idempotent jobs are attempted, avoiding duplicate sends on a repeated request.

After deploying the code and setting the environment variable, register with a mailbox you control. Confirm the corresponding email job is `succeeded` and the email actually arrives. If the job is `failed`, inspect the stored error securely and fix SMTP configuration before retrying.
