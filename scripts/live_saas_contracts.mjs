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
  // On scheduled/manual CI runs, compare the deployed API's own identity to
  // the exact repository revision being monitored. A well-formed but stale
  // SHA must not be reported as a current-release pass.
  const expectedSha = typeof result.expectedReleaseSha === "string"
    ? result.expectedReleaseSha.trim().toLowerCase() : "";
  if (expectedSha && (!/^[0-9a-f]{40}$/.test(expectedSha) ||
      json.release_sha.toLowerCase() !== expectedSha)) {
    return "deployed release SHA does not match the expected repository revision";
  }
  return true;
}

export function isHealthy({ response, body }) {
  if (!response.ok) return `HTTP ${response.status}`;
  let json;
  try { json = JSON.parse(body); } catch { return "health response was not valid JSON"; }
  return json.status === "ok" && json.database === "ok" &&
    json.version === EXPECTED_RELEASE_VERSION
    ? true : `health contract reports a degraded service/database or unexpected release version (expected ${EXPECTED_RELEASE_VERSION})`;
}

export function isReady({ response, body }) {
  if (!response.ok) return `HTTP ${response.status}`;
  let json;
  try { json = JSON.parse(body); } catch { return "readiness response was not valid JSON"; }
  const checks = json.checks || {};
  const missing = REQUIRED_READINESS_CHECKS.filter(name => checks[name] !== true);
  const versionMatches = json.version === EXPECTED_RELEASE_VERSION;
  const statusProblem = json.status === "ready" ? "" : "status is not ready";
  return json.status === "ready" && missing.length === 0 && versionMatches
    ? true : `readiness contract has failed or missing required checks: ${missing.join(", ") || "none"}; ${statusProblem}; release version must be ${EXPECTED_RELEASE_VERSION}`;
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
  if (!/^(no-referrer|strict-origin|strict-origin-when-cross-origin)$/i.test(headers["referrer-policy"] || "")) {
    missing.push("referrer-policy");
  }
  return missing.length ? `missing or weak security headers: ${missing.join(", ")}` : true;
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
    if (item && !item.ok && item.status !== null && item.status >= 200 && item.status < 300) {
      diagnostics.push({
        severity: "critical",
        code: "FRONTEND_ROUTE_CONTRACT_FAILED",
        message: `The ${name} URL returned a successful HTTP status but did not satisfy the expected SPA-shell contract.`,
        action: "Inspect the deployed frontend routing and returned HTML shell. Do not accept HTTP 200 as proof that the Web IDE route loads correctly.",
      });
    }
  }

  const liveness = byName.get("api-liveness");
  if (liveness && !liveness.ok && liveness.status !== null && liveness.status >= 200 && liveness.status < 300) {
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
    if (item && !item.ok && item.status !== null && item.status >= 200 && item.status < 300) {
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
