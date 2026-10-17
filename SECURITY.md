# Security Policy

Developer OS treats security, tenant isolation, production reliability, and supply-chain integrity as release-blocking concerns.

## Supported releases

Security fixes are prioritized for the current production release and the active production-hardening branch. Older releases may require upgrade before a fix is provided.

## Reporting a vulnerability

Please use GitHub's private **Security Advisories** / vulnerability reporting for this repository when available. Do not disclose exploitable details in a public issue, pull request, discussion, or social post.

Include, when safe to share:

- affected component and version/commit;
- impact and realistic attack scenario;
- reproduction steps or a minimal proof of concept;
- relevant logs without secrets or personal data;
- suggested mitigation, if known.

Do not include passwords, API keys, access tokens, production credentials, private user data, or other secrets in a report.

## Response expectations

Reports are triaged for exploitability and customer impact. Critical issues affecting authentication, authorization, tenant isolation, arbitrary code execution, secret exposure, payment integrity, or the execution sandbox receive the highest priority.

After remediation, the fix should be validated through the applicable automated security, dependency, isolation, and regression gates before release.

## Production security baseline

The production boundary is expected to maintain:

- least-privilege GitHub Actions permissions;
- dependency vulnerability auditing and automated dependency updates;
- CodeQL/static security analysis;
- hardened non-root containers;
- strict runner isolation with network access disabled by default;
- authenticated and idempotent billing webhooks;
- request correlation and structured security logging;
- tested PostgreSQL backup and restore procedures;
- fail-closed production configuration for unsafe secrets, hosts, origins, and infrastructure settings.

Security controls are not considered complete merely because a configuration exists; release confidence requires passing automated validation where the control is testable.
