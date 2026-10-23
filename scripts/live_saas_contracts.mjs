/** Pure validators for the read-only live SaaS smoke monitor. */
export const PLAN_NAMES = Object.freeze(["free", "pro", "team", "enterprise"]);
export const REQUIRED_READINESS_CHECKS = Object.freeze([
  "database", "secret_key", "allowed_hosts", "email", "cache", "worker",
]);
export const FEATURE_KEYS = Object.freeze([
  "web_ide", "ai_assistant", "api_keys", "organizations", "collaboration",
  "governance", "audit_log", "seat_billing", "enterprise_controls",
]);

/** Backoff for transient responses only; permanent contract failures are never retried. */
/** A newly pushed release may need time to propagate to the production host. */
export function isRetryableReleaseMismatch(validation) {
  return typeof validation === "string" && /release SHA does not match the expected repository revision/.test(validation);
}

/** Bounded rollout wait for a healthy old release; unrelated contract failures stay fail-fast. */
export function releaseMismatchRetryDelayMs(completedAttempt) {
  const schedule = [10000, 20000, 30000];
  const attempt = Math.max(1, Math.min(schedule.length, Number(completedAttempt) || 1));
  return schedule[attempt - 1];
}

export function retryDelayMs(status, completedAttempt) {
  const attempt = Math.max(1, Math.min(3, Number(completedAttempt) || 1));
  const schedules = status === 503
    ? [5000, 12000, 20000]
    : status === null || status === undefined
      ? [1500, 3500, 7000]
      : [1000, 2500, 6000];
  const retryable = status === null || status === undefined || status === 408 || status === 425 || status === 429 || status >= 500;
  return retryable ? schedules[attempt - 1] : 0;
}

export function isHtml({ response, body, result }) {
  return response.ok && result.contentType.toLowerCase().includes("text/html") && body.length > 100
    ? true : `expected non-empty HTML; received HTTP ${response.status} (${result.contentType || "no content-type"})`;
}

export function isSpaShell({ response, body, result }) {
  return isHtml({ response, body, result }) === true &&
    /<div[^>]+id=["'](?:root|app)["']/i.test(body)
    ? true : `route did not return a valid SPA shell (HTTP ${response.status})`;
}

export const EXPECTED_RELEASE_VERSION = "4.0.0";
const RELEASE_SHA_PATTERN = /^[0-9a-f]{7,40}$/i;
const hasReleaseSha = value => typeof value === "string" && RELEASE_SHA_PATTERN.test(value);
/**
 * Return a strictly allowlisted, secret-free summary for public health probes.
 * Never copy arbitrary response fields into CI artifacts; readiness diagnostics
 * intentionally expose only status/release identity and boolean dependency checks.
 */
export function summarizeHealthPayload(body) {
  let payload;
  try { payload = JSON.parse(body); } catch { return null; }
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return null;
  const summary = {};
  for (const key of ["status", "service", "database", "version"]) {
    if (typeof payload[key] === "string" && payload[key].length <= 64) summary[key] = payload[key];
  }
  if (typeof payload.release_sha === "string" &&
      (RELEASE_SHA_PATTERN.test(payload.release_sha) || payload.release_sha === "unknown")) {
    summary.release_sha = payload.release_sha;
  }
  if (payload.checks && typeof payload.checks === "object" && !Array.isArray(payload.checks)) {
    const checks = {};
    for (const key of REQUIRED_READINESS_CHECKS) {
      if (typeof payload.checks[key] === "boolean") checks[key] = payload.checks[key];
    }
    if (Object.keys(checks).length) summary.checks = checks;
  }
  return Object.keys(summary).length ? summary : null;
}


function matchesExpectedReleaseSha(json, result = {}) {
  const expectedSha = typeof result.expectedReleaseSha === "string"
    ? result.expectedReleaseSha.trim().toLowerCase() : "";
  if (!expectedSha) return true;
  if (!/^[0-9a-f]{40}$/.test(expectedSha)) return false;
  if (typeof json?.release_sha === "string" && json.release_sha.toLowerCase() === expectedSha) {
    return true;
  }
  // A release SHA may differ from HEAD when only monitor/docs files changed.
  // The smoke runner sets this flag only after verifying the deployed Backend/
  // tree matches HEAD and the deployed commit is an ancestor of HEAD.
  return result.releaseRevisionMatchesBackend === true;
}

export function isLive({ response, body, result = {} }) {
  if (!response.ok) return `HTTP ${response.status}`;
  let json;
  try { json = JSON.parse(body); } catch { return "liveness response was not valid JSON"; }
  const validIdentity = json.status === "alive" && json.service === "developer-os-api" &&
    json.version === EXPECTED_RELEASE_VERSION &&
    typeof json.release_sha === "string" && RELEASE_SHA_PATTERN.test(json.release_sha);
  if (!validIdentity) {
    return "liveness contract requires an alive API, expected release version, and a real 7–40 character hexadecimal release SHA";
  }
  // Every public health endpoint is checked against the same expected release
  // revision on scheduled/manual runs; a well-formed stale SHA is not a pass.
  if (!matchesExpectedReleaseSha(json, result)) {
    return "deployed release SHA does not match the expected repository revision";
  }
  return true;
}

export function isHealthy({ response, body, result = {} }) {
  if (!response.ok) return `HTTP ${response.status}`;
  let json;
  try { json = JSON.parse(body); } catch { return "health response was not valid JSON"; }
  const healthy = json.status === "ok" && json.database === "ok" &&
    json.version === EXPECTED_RELEASE_VERSION && hasReleaseSha(json.release_sha);
  if (!healthy) {
    return `health contract reports a degraded service/database or requires release version ${EXPECTED_RELEASE_VERSION} and a real release SHA`;
  }
  return matchesExpectedReleaseSha(json, result)
    ? true : "health release SHA does not match the expected repository revision";
}

export function isReady({ response, body, result = {} }) {
  if (!response.ok) return `HTTP ${response.status}`;
  let json;
  try { json = JSON.parse(body); } catch { return "readiness response was not valid JSON"; }
  const checks = json.checks || {};
  const missing = REQUIRED_READINESS_CHECKS.filter(name => checks[name] !== true);
  const versionMatches = json.version === EXPECTED_RELEASE_VERSION;
  const shaMatches = hasReleaseSha(json.release_sha);
  const statusProblem = json.status === "ready" ? "" : "status is not ready";
  if (!(json.status === "ready" && missing.length === 0 && versionMatches && shaMatches)) {
    return `readiness contract has failed or missing required checks: ${missing.join(", ") || "none"}; ${statusProblem}; release version must be ${EXPECTED_RELEASE_VERSION} and release SHA must be real`;
  }
  return matchesExpectedReleaseSha(json, result)
    ? true : "readiness release SHA does not match the expected repository revision";
}

export function validatePricing({ response, body }) {
  if (!response.ok) return `HTTP ${response.status}`;
  let json;
  try { json = JSON.parse(body); } catch { return "response was not valid JSON"; }
  const plans = json?.plans;
  if (!plans || PLAN_NAMES.some(name => !plans[name])) {
    return "pricing response is missing one or more plan definitions";
  }
  if (typeof json.currency !== "string" || !json.currency.trim()) {
    return "pricing response is missing its currency";
  }
  for (const name of PLAN_NAMES) {
    const plan = plans[name];
    if (typeof plan.label !== "string" || !plan.label.trim() ||
        typeof plan.billing_model !== "string" || !plan.billing_model.trim() ||
        !Array.isArray(plan.features) ||
        plan.features.some(feature => typeof feature !== "string" || !feature.trim()) ||
        !Number.isFinite(plan.monthly_usd) || plan.monthly_usd < 0 ||
        (plan.annual_usd !== undefined && (!Number.isFinite(plan.annual_usd) || plan.annual_usd < 0)) ||
        (["pro", "team"].includes(name) && (!Number.isFinite(plan.annual_usd) || plan.annual_usd <= 0 || Math.abs(plan.annual_usd - plan.monthly_usd * 10) > 0.001))) {
      return `pricing plan '${name}' has an invalid public contract`;
    }
  }
  if (plans.pro.monthly_usd !== 29 || plans.pro.annual_usd !== 290 ||
      plans.team.monthly_usd !== 15 || plans.team.annual_usd !== 150) {
    return "published Pro/Team pricing must be Pro $29/month or $290/year and Team $15/seat/month or $150/seat/year";
  }
  const entitlements = json.entitlements;
  if (!entitlements || PLAN_NAMES.some(name => !entitlements[name] ||
      !Number.isSafeInteger(entitlements[name].ide_runs_month) || entitlements[name].ide_runs_month < 0 ||
      !Number.isSafeInteger(entitlements[name].ai_messages_month) || entitlements[name].ai_messages_month < 0)) {
    return "pricing response is missing valid per-plan IDE/AI entitlements";
  }
  const featureMatrix = json.feature_matrix;
  if (!featureMatrix || PLAN_NAMES.some(name => !featureMatrix[name] ||
      FEATURE_KEYS.some(key => typeof featureMatrix[name][key] !== "boolean"))) {
    return "pricing response is missing boolean feature flags for one or more plans";
  }
  const stripe = json.stripe_configured;
  if (!stripe || ["pro", "team", "enterprise", "pro_annual", "team_annual"].some(name => typeof stripe[name] !== "boolean")) {
    return "pricing response is missing valid monthly/annual Stripe configuration status flags";
  }
  return true;
}

export function validateBillingConfig({ response, body }) {
  if (!response.ok) return `HTTP ${response.status}`;
  let json;
  try { json = JSON.parse(body); } catch { return "response was not valid JSON"; }
  return ["pro", "team", "enterprise", "pro_annual", "team_annual"].every(name => json?.stripe_configured?.[name] === true)
    ? true : "Stripe price IDs for Pro, Team (monthly and annual), and Enterprise are not all configured in the deployed API";
}

/** Validate the security headers visible to an unauthenticated browser. */
export function validateSecurityHeaders({ response, result }) {
  if (!response.ok) return `HTTP ${response.status}`;
  const headers = result.securityHeaders || {};
  const missing = [];
  const hsts = headers["strict-transport-security"] || "";
  const maxAge = Number(hsts.match(/max-age=(\d+)/i)?.[1] || 0);
  if (maxAge < 15552000) missing.push("strict-transport-security (max-age >= 15552000)");
  if ((headers["x-content-type-options"] || "").toLowerCase() !== "nosniff") {
    missing.push("x-content-type-options: nosniff");
  }
  if (!/^(deny|sameorigin)$/i.test(headers["x-frame-options"] || "")) {
    missing.push("x-frame-options: DENY or SAMEORIGIN");
  }
  if (!/^(no-referrer|same-origin|strict-origin|strict-origin-when-cross-origin)$/i.test(headers["referrer-policy"] || "")) {
    missing.push("referrer-policy");
  }
  return missing.length ? `missing or weak security headers: ${missing.join(", ")}` : true;
}

/** Health endpoints must not be cached by browsers or intermediary proxies. */
export function validateNoStoreCacheHeaders({ response, result }) {
  // Cache policy is independent of application health: validate headers even
  // on a legitimate 503 readiness response, rather than double-counting the
  // outage as a cache-policy failure.
  const headers = result.cacheHeaders || {};
  const cacheControl = (headers["cache-control"] || "").toLowerCase();
  // Some proxies/framework middleware expose duplicate Cache-Control fields as
  // a comma-joined value that repeats the field name (for example,
  // "no-store, cache-control: max-age=0"). Normalize that representation
  // without weakening the required no-store and max-age=0 directives.
  const directives = cacheControl.split(",")
    .map(value => value.trim().replace(/^cache-control\s*:\s*/i, ""))
    .filter(Boolean);
  const missing = [];
  if (!directives.includes("no-store")) missing.push("cache-control: no-store");
  if (!directives.some(value => /^max-age=0$/.test(value))) missing.push("cache-control: max-age=0");
  const pragma = (headers.pragma || "").toLowerCase();
  // Some intermediaries incorrectly comma-join distinct response fields. If
  // `Pragma: no-cache` is embedded in the combined value, still require the
  // exact directive rather than failing a valid no-store response.
  if (pragma !== "no-cache" && !directives.includes("pragma: no-cache")) missing.push("pragma: no-cache");
  return missing.length ? `health response has a cacheable or incomplete cache policy: ${missing.join(", ")}` : true;
}

/** Reject HTTPS checks that finish on an insecure or credential-bearing URL. */
export function validateFinalUrl({ response, result }) {
  const finalUrl = result.finalUrl || response.url;
  if (!finalUrl) return "final response URL is unavailable";
  let parsed;
  try { parsed = new URL(finalUrl); } catch { return "final response URL is invalid"; }
  if (parsed.protocol !== "https:") return "request ended on a non-HTTPS URL";
  if (parsed.username || parsed.password) return "final response URL contains embedded credentials";
  if (result.url) {
    let requested;
    try { requested = new URL(result.url); } catch { return "requested URL is invalid"; }
    if (parsed.origin !== requested.origin) return "request redirected to an unexpected origin";
  }
  return true;
}


/** Explain broad origin-level failures without exposing response bodies or secrets. */
export function diagnoseChecks(checks) {
  const diagnostics = [];
  const byName = new Map(checks.map(item => [item.name, item]));
  const apiNames = [
    "api-security-headers", "api-liveness", "api-health", "api-readiness",
    "api-pricing-contract", "billing-provider-config",
  ];
  const apiChecks = apiNames.map(name => byName.get(name)).filter(Boolean);
  // A successful HTTP response with an old release identity is a rollout/release
  // provenance incident, not an API outage. Report it directly and avoid
  // burying the actionable cause under generic contract-failure messages.
  const releaseMismatchChecks = checks.filter(item =>
    !item.ok && /release SHA does not match the expected repository revision/i.test(item.error || ""));
  const releaseMismatchNames = new Set(releaseMismatchChecks.map(item => item.name));
  if (releaseMismatchChecks.length > 0) {
    diagnostics.push({
      severity: "critical",
      code: "LIVE_RELEASE_REVISION_MISMATCH",
      message: `${releaseMismatchChecks.length} live check(s) returned a release SHA different from the repository revision under test: ${[...releaseMismatchNames].join(", ")}.`,
      action: "In Render, verify the API service is linked to the intended repository and master branch. If the deployed Backend/ tree differs from master, deploy the latest commit that changes Backend/ and confirm the runtime release SHA is sourced from the actual deployed commit. Re-run the live monitor. Do not disable or bypass release verification.",
    });
  }
  const originFailures = apiChecks.filter(item =>
    !item.ok && (item.status === null || item.status >= 500));
  const originUnavailable = apiChecks.length > 0 && originFailures.length === apiChecks.length;
  if (originUnavailable) {
    diagnostics.push({
      severity: "critical",
      code: "API_ORIGIN_UNAVAILABLE",
      message: "Every API probe failed with an HTTP 5xx response or connection timeout. This points to API-origin/service availability rather than a single route contract.",
      action: "Confirm the configured production API origin, then inspect the Render API service's latest deploy, service events, runtime logs, health checks, and suspension/resource state. Keep the smoke checks strict.",
    });
    return diagnostics;
  }
  // A single 404 can coexist with an origin-wide incident (for example, a
  // stale route on the deployed release). Do not let that one response hide
  // a broad service outage in the remaining independent API probes.
  const broadOriginFailure = apiChecks.length >= 4 &&
    originFailures.length >= Math.ceil(apiChecks.length * 0.67);
  if (broadOriginFailure) {
    diagnostics.push({
      severity: "critical",
      code: "API_ORIGIN_DEGRADED",
      message: `${originFailures.length} of ${apiChecks.length} independent API probes failed with HTTP 5xx responses or connection timeouts. A route-specific response elsewhere does not rule out a broader API-origin incident.`,
      action: "Confirm the configured production API origin, then inspect the Render API service's latest deploy, service events, runtime logs, health checks, and suspension/resource state. Keep individual route failures visible and do not weaken the smoke checks.",
    });
  }

  const frontendRoot = byName.get("frontend-root");
  if (frontendRoot && !frontendRoot.ok &&
      (frontendRoot.status === null || frontendRoot.status >= 500)) {
    diagnostics.push({
      severity: "critical",
      code: "FRONTEND_ORIGIN_UNAVAILABLE",
      message: "The public frontend root timed out or returned a server error.",
      action: "Check the configured frontend origin and the frontend host's latest deployment, service events, and runtime/build logs. Keep the route check failing until a real public response succeeds.",
    });
  }

  for (const name of ["web-ide-route", "web-ide-trailing-slash"]) {
    const item = byName.get(name);
    if (item && !item.ok && !releaseMismatchNames.has(name) && item.status !== null && item.status >= 200 && item.status < 300) {
      diagnostics.push({
        severity: "critical",
        code: "FRONTEND_ROUTE_CONTRACT_FAILED",
        message: `The ${name} URL returned a successful HTTP status but did not satisfy the expected SPA-shell contract.`,
        action: "Inspect the deployed frontend routing and returned HTML shell. Do not accept HTTP 200 as proof that the Web IDE route loads correctly.",
      });
    }
  }

  const liveness = byName.get("api-liveness");
  if (liveness && !liveness.ok && !releaseMismatchNames.has("api-liveness") && liveness.status !== null && liveness.status >= 200 && liveness.status < 300) {
    diagnostics.push({
      severity: "critical",
      code: "API_LIVENESS_CONTRACT_FAILED",
      message: "The liveness endpoint returned a successful HTTP status, but its JSON process-identity contract failed.",
      action: "Verify that the deployed liveness handler returns status=alive, service identity, version, and release SHA without depending on database or worker health. Keep the validator fail-closed.",
    });
  }
  if (liveness && !liveness.ok && liveness.status === 404) {
    diagnostics.push({
      severity: "critical",
      code: "API_LIVENESS_ROUTE_MISSING",
      message: "The configured API origin responded, but the required liveness route returned HTTP 404.",
      action: "Verify the deployed API route map and release version. Restore or configure the intended liveness endpoint in the owning runtime change; do not weaken this monitor to accept 404.",
    });
  } else if (liveness && !liveness.ok && (liveness.status === null || liveness.status >= 500)) {
    diagnostics.push({
      severity: "critical",
      code: "API_LIVENESS_UNHEALTHY",
      message: "The liveness probe timed out or returned a server error while other API probes did not establish a total origin outage.",
      action: "Compare the liveness failure timestamp with deployment and runtime events. Confirm the route responds independently of database and worker dependencies, and keep the monitor fail-closed.",
    });
  }

  for (const [name, code, label] of [
    ["api-health", "API_HEALTH_CONTRACT_FAILED", "health"],
    ["api-readiness", "API_READINESS_CONTRACT_FAILED", "readiness"],
  ]) {
    const item = byName.get(name);
    if (item && !item.ok && !releaseMismatchNames.has(name) && item.status !== null && item.status >= 200 && item.status < 300) {
      diagnostics.push({
        severity: "critical",
        code,
        message: `The API ${label} endpoint responded successfully at HTTP level, but its required response contract failed.`,
        action: `Inspect the sanitized ${label} failure and authorized service diagnostics. Do not treat HTTP 2xx alone as proof of operational ${label}.`,
      });
    }
  }

  const pricing = byName.get("api-pricing-contract");
  if (pricing && !pricing.ok && pricing.status !== null && pricing.status >= 200 && pricing.status < 300) {
    diagnostics.push({
      severity: "critical",
      code: "PRICING_CONTRACT_INVALID",
      message: "The public pricing endpoint returned a successful HTTP status, but the published plan, entitlement, feature, or billing-status contract is incomplete or invalid.",
      action: "Compare the sanitized validation error with the intended public pricing contract. Correct the owning API/configuration change; do not weaken the validator or expose secret values.",
    });
  }

  const billing = byName.get("billing-provider-config");
  if (billing && !billing.ok && billing.status !== null && billing.status >= 200 && billing.status < 300) {
    diagnostics.push({
      severity: "critical",
      code: "BILLING_PROVIDER_CONFIG_INCOMPLETE",
      message: "The pricing endpoint is reachable, but the required paid-plan billing configuration contract is not satisfied.",
      action: "Check the production Stripe price-ID configuration and the deployed pricing response using authorized configuration access. Never print secret values or guess IDs.",
    });
  }

  for (const name of ["api-security-headers", "frontend-security-headers"]) {
    const item = byName.get(name);
    if (item && !item.ok && item.status !== null && item.status >= 200 && item.status < 300) {
      diagnostics.push({
        severity: "warning",
        code: "SECURITY_HEADERS_INCOMPLETE",
        message: `The ${name} probe found missing or weak browser-facing security headers.`,
        action: "Review the relevant host/proxy security-header policy and verify the resulting public response headers.",
      });
      break;
    }
  }

  return diagnostics;
}
