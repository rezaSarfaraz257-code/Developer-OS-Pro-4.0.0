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
        (plan.annual_usd !== undefined && (!Number.isFinite(plan.annual_usd) || plan.annual_usd < 0))) {
      return `pricing plan '${name}' has an invalid public contract`;
    }
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
    return "pricing response is missing valid Stripe configuration status flags";
  }
  return true;
}

export function validateBillingConfig({ response, body }) {
  if (!response.ok) return `HTTP ${response.status}`;
  let json;
  try { json = JSON.parse(body); } catch { return "response was not valid JSON"; }
  return json?.stripe_configured?.pro === true && json?.stripe_configured?.team === true
    ? true : "Stripe price IDs for Pro and Team are not configured in the deployed API";
}
