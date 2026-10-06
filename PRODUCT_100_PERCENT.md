# Developer OS — Global SaaS 4.0.0

## Completion target

This package is the completed product scope for a serious, globally deployable Developer OS SaaS. The release focuses on functional completeness rather than cosmetic placeholders.

## Product surfaces

- Command Center dashboard
- Explore catalog: tools, workflows, resources, favorites
- Projects, project detail, tasks, progress, notes and snippets
- Tags, activity, comments and task dependencies
- Project collaboration
- Organizations and role-based membership
- Expiring organization email invitations with token acceptance
- Governance audit log
- Universal workspace search
- GitHub OAuth with PKCE, repositories, activity sync and disconnect
- Context-aware AI conversations with deterministic fallback
- Metered AI usage and plan enforcement
- Persistent Web IDE workspaces
- Framework/package installation through isolated runner
- IDE execution history
- Metered IDE execution
- API keys with plan limits and one-time secret presentation
- Subscription plans
- Stripe Checkout
- Stripe Billing Portal
- Signed, replay-safe Stripe webhooks
- Cancellation-at-period-end
- Subscription/organization plan synchronization
- Usage dashboard
- Production health/readiness endpoints
- Docker Compose production stack
- Security headers, throttling, encrypted GitHub tokens and production checks
- CI for frontend and backend

## Release-critical hardening completed

1. Removed the duplicate `billing_webhook_api` definition that could silently bypass signature verification.
2. Added a billing event idempotency ledger to prevent replayed webhook events from being applied twice.
3. Added provider event handling for checkout, subscription lifecycle and payment status changes.
4. Added metered usage records and plan-aware limits for AI, IDE executions, API keys and workspaces.
5. Added a real billing portal endpoint.
6. Replaced unsafe local-only paid-plan downgrade behavior with Stripe cancellation-at-period-end when a provider subscription exists.
7. Added organization invitation lifecycle with expiration, role, token and email matching.
8. Added an audit log surface for governance and operational traceability.
9. Hardened runner environment variables and blocked common network/exfiltration/process-control commands.
10. Added migration and regression tests for the new SaaS maturity layer.

## Verification note

The source tree passes Python AST/syntax validation in this build environment. Full Django test execution and Vite production build require the repository dependencies, which are not installed in the offline execution environment used to prepare this archive. The CI workflow remains the authoritative executable verification gate and must pass in GitHub Actions / the target deployment environment.
