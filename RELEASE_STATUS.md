# Release Status — Developer OS 4.0.0

## Engineering state

The product feature set and production-hardening passes are merged on `master`. The release contract is intentionally fail-closed: source inspection or a successful PR build is not treated as proof of target-environment readiness.

### Implemented

- SaaS organizations, roles and invitations
- Free / Pro / Team / Enterprise pricing and server-authoritative entitlements
- Subscriptions, Stripe checkout/update lifecycle and webhook idempotency
- Usage metering and plan limits
- API key lifecycle
- JWT refresh rotation, session binding and logout blacklist
- GitHub OAuth with PKCE and encrypted GitHub tokens
- Audit/security/session persistence
- Persistent project/workspace filesystem
- Monaco-based Pro IDE with Explorer, terminal, diagnostics, symbols, run/debug, preview, source control, collaboration and AI patch review
- Isolated Runner with resource limits and fail-closed production security
- Context-aware AI engineering actions
- PostgreSQL + Redis production architecture
- Docker Compose production stack and Nginx routing
- Health/readiness endpoints
- Frontend/backend/runner automated CI
- Docker runner integration smoke tests
- Full-stack smoke E2E
- Stripe webhook regression tests
- Security/CodeQL/dependency gates

## Validation contract

| Gate | State |
|---|---|
| Code-level release contract | PASS |
| PR production/security validation | PASS |
| Current master automated run | PENDING FRESH RUN |
| Exact Render deployment E2E | REQUIRES TARGET ENVIRONMENT |
| Real Stripe webhook delivery | REQUIRES TARGET ENVIRONMENT |
| Real AI provider success/timeout/quota paths | REQUIRES TARGET ENVIRONMENT |
| Encrypted backup + restore evidence | REQUIRES TARGET ENVIRONMENT |
| Production monitoring/alerts | REQUIRES TARGET ENVIRONMENT |

The production black-box workflow now fails closed when its required deployment secrets are absent. It also validates the complete Free/Pro/Team/Enterprise pricing, entitlement and feature matrix instead of checking only the Free/Pro happy path.

**100% production-validated is only declared after the fresh master workflow and target-environment gates pass.**

The product is intentionally not treated as an ordinary CRUD website: the architecture preserves isolated execution, server-side entitlement enforcement, real-time collaboration, contextual AI, production observability/recovery, and tier-specific SaaS controls.
