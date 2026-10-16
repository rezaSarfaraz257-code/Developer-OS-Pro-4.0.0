# Stripe production billing activation

This checklist is intentionally separate from application code: live Stripe credentials and Price IDs belong in the hosting provider's protected environment settings, never in Git.

## 1. Create the Stripe catalog

In the Stripe Dashboard, select the intended mode (Test while validating, Live only when ready for real payments). Create three recurring Prices that match the product's billing cadence and currency:

- Pro — USD 29/month: copy its **price_...** identifier into **STRIPE_PRICE_PRO**.
- Pro annual — USD 290/year: copy its annual recurring **price_...** identifier into **STRIPE_PRICE_PRO_ANNUAL**.
- Team — USD 15 per seat/month: copy its **price_...** identifier into **STRIPE_PRICE_TEAM**.
- Team annual — USD 150 per seat/year: copy its annual recurring **price_...** identifier into **STRIPE_PRICE_TEAM_ANNUAL**.
- Enterprise — starting at USD 299/month: create a recurring Price that matches the configured offer and copy its **price_...** identifier into **STRIPE_PRICE_ENTERPRISE**.

Do not use Product IDs (**prod_...**) where a Price ID (**price_...**) is required. Price IDs are mode-specific: test-mode IDs do not replace live-mode IDs.

## 2. Set protected Render environment variables

On the backend web service, add these values using Render's Environment settings:

- **STRIPE_SECRET_KEY**: secret key from the same Stripe mode as the Prices (**sk_test_...** for test, **sk_live_...** for live).
- **STRIPE_WEBHOOK_SECRET**: signing secret generated for the webhook endpoint below (**whsec_...**).
- **STRIPE_PRICE_PRO** and **STRIPE_PRICE_PRO_ANNUAL**
- **STRIPE_PRICE_TEAM** and **STRIPE_PRICE_TEAM_ANNUAL**
- **STRIPE_PRICE_ENTERPRISE**
- **REQUIRE_STRIPE_BILLING=true**

Save and redeploy/restart the backend so the process receives the new environment. Never paste secret keys into GitHub issues, source code, chat, or client-side **VITE_*** variables.

## 3. Configure Stripe webhook delivery

Create a Stripe webhook endpoint pointing to:

    https://dos-o91y.onrender.com/api/billing/webhook/

Subscribe to the event types handled by the current webhook implementation (at minimum, confirm Checkout completion and subscription/invoice lifecycle events against **Backend/api/views.py** and the Stripe dashboard's endpoint test). Copy this endpoint's signing secret into **STRIPE_WEBHOOK_SECRET**. Do not reuse a webhook secret from a different endpoint or Stripe mode.

## 4. Validate before enabling live sales

Run the repository's preflight with the same environment as the backend:

    python scripts/production_preflight.py

With **REQUIRE_STRIPE_BILLING=true**, the preflight fails closed if any advertised monthly or annual Stripe Price ID, secret, or other required Stripe variable is absent, incomplete, or has an unexpected identifier prefix. The preflight validates presence and basic shape; it cannot prove that a Price exists in the selected Stripe account or that webhook delivery succeeds.

After deploy, verify:

1. **GET /api/pricing/** reports **stripe_configured.pro**, **team**, and **enterprise** as **true**.
2. An authenticated checkout request for each paid plan returns a Stripe-hosted Checkout URL (do not count a UI button as proof).
3. Stripe test-mode checkout completes and the signed webhook updates subscription state exactly once.
4. Replayed webhook events are idempotent and invalid signatures are rejected.
5. A customer cancellation/payment failure updates the subscription and entitlements as expected.

Keep production sales closed until these checks pass. A healthy Pricing response alone is not proof that payment collection works.
