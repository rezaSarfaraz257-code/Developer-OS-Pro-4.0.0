/** Pure validators for the read-only live SaaS smoke monitor. */
export const PLAN_NAMES = Object.freeze(["free", "pro", "team", "enterprise"]);
export const REQUIRED_READINESS_CHECKS = Object.freeze([
  "database", "secret_key", "allowed_hosts", "email", "cache", "worker",
]);
export const FEATURE_KEYS = Object.freeze([
  "web_ide", "ai_assistant", "api_keys", "organizations", "collaboration",
  "governance", "audit_log", "seat_billing", "enterprise_controls",
]);

export function isHtml({ response, body, result }) {
  return response.ok && result.contentType.toLowerCase().includes("text/html") && body.length > 100
    ? true : `expected non-empty HTML; received HTTP ${response.status} (${result.contentType || "no content-type"})`;
}

export function isSpaShell({ response, body, result }) {
  return isHtml({ response, body, result }) === true &&
    /<div[^>]+id=["'](?:root|app)["']/i.test(body)
    ? true : `route did not return a valid SPA shell (HTTP ${response.status})`;
}

export function isLive({ response, body }) {
  if (!response.ok) return `HTTP ${response.status}`;
  let json;
  try { json = JSON.parse(body); } catch { return "liveness response was not valid JSON"; }
  return json.status === "alive" && json.service === "developer-os-api" &&
    typeof json.version === "string" && json.version.length > 0 &&
    typeof json.release_sha === "string" && json.release_sha.length > 0
    ? true : "liveness contract is missing process identity or reports a non-alive service";
}

export function isHealthy({ response, body }) {
  if (!response.ok) return `HTTP ${response.status}`;
  let json;
  try { json = JSON.parse(body); } catch { return "health response was not valid JSON"; }
  return json.status === "ok" && json.database === "ok"
    ? true : "health contract reports a degraded service or database";
}

export function isReady({ response, body }) {
  if (!response.ok) return `HTTP ${response.status}`;
  let json;
  try { json = JSON.parse(body); } catch { return "readiness response was not valid JSON"; }
  const checks = json.checks || {};
  const missing = REQUIRED_READINESS_CHECKS.filter(name => checks[name] !== true);
  return json.status === "ready" && missing.length === 0
    ? true : `readiness contract has failed or missing required checks: ${missing.join(", ") || "status is not ready"}`;
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
  if (!stripe || ["pro", "team", "enterprise"].some(name => typeof stripe[name] !== "boolean")) {
    return "pricing response is missing valid Stripe configuration status flags for Pro, Team, or Enterprise";
  }
  return true;
}

export function validateBillingConfig({ response, body }) {
  if (!response.ok) return `HTTP ${response.status}`;
  let json;
  try { json = JSON.parse(body); } catch { return "response was not valid JSON"; }
  return ["pro", "team", "enterprise"].every(name => json?.stripe_configured?.[name] === true)
    ? true : "Stripe price IDs for Pro, Team, and Enterprise are not all configured in the deployed API";
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
  return true;
}


/** Explain broad origin-level failures without exposing response bodies or secrets. */
export function diagnoseChecks(checks) {
  const apiNames = new Set([
    "api-security-headers", "api-liveness", "api-health", "api-readiness",
    "api-pricing-contract", "billing-provider-config",
  ]);
  const apiChecks = checks.filter(item => apiNames.has(item.name));
  const originUnavailable = apiChecks.length > 0 && apiChecks.every(item =>
    !item.ok && (item.status === null || item.status >= 500));
  if (!originUnavailable) return [];
  return [{
    severity: "critical",
    code: "API_ORIGIN_UNAVAILABLE",
    message: "Every API probe failed with an HTTP 5xx response or connection timeout. This points to API-origin/service availability rather than a single route contract.",
    action: "Confirm the configured production API origin, then inspect the Render API service's latest deploy, service events, runtime logs, health checks, and suspension/resource state. Keep the smoke checks strict.",
  }];
}
