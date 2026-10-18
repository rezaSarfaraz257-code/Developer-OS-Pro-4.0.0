# Render frontend security headers

The public smoke monitor checks **actual HTTP response headers**, not only HTML metadata. The frontend's `index.html` includes a safe Referrer-Policy meta fallback, but this cannot set `X-Frame-Options` and does not replace response headers.

## Required response-header rules

In the Render Dashboard, open the existing static site serving `https://developer-os-version-3-0-5.onrender.com`, then **Settings → Headers** (wording may vary). Add the following rules for path `/*`:

| Header | Value |
|---|---|
| `Strict-Transport-Security` | `max-age=63072000; includeSubDomains; preload` |
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |

Do not add duplicate rules with conflicting values. If the service already has header rules, edit or reconcile them rather than stacking conflicting duplicates.

## Verification

After saving the rules, verify the **actual public response** for `/`, `/ide`, and `/ide/` and rerun the GitHub Actions workflow **Live SaaS Smoke Monitor** on `master`. The workflow should fail closed until all required headers are present.

The `X-Frame-Options: DENY` policy prevents framing the site. If product requirements later need trusted embedding, replace it only with a reviewed, restrictive `Content-Security-Policy: frame-ancestors ...` policy and update the monitor to validate that explicit policy; do not silently accept missing frame protection.

## Why this requires host configuration

Render static sites are served by a CDN and cannot add arbitrary response headers from React or an HTML meta tag. The host-level header configuration is therefore authoritative. A green local frontend build alone does not prove that the deployed response has these headers.
