import test from "node:test";
import assert from "node:assert/strict";
import {
  isHtml, isSpaShell, isLive, isHealthy, isReady, validatePricing, validateBillingConfig, validateSecurityHeaders, validateFinalUrl,
  PLAN_NAMES, REQUIRED_READINESS_CHECKS, FEATURE_KEYS, diagnoseChecks, retryDelayMs,
} from "./live_saas_contracts.mjs";

const response = (ok = true, status = ok ? 200 : 503) => ({ ok, status });
const jsonInput = (value, ok = true) => ({ response: response(ok), body: JSON.stringify(value) });

function validPricing() {
  const plans = Object.fromEntries(PLAN_NAMES.map((name, index) => [name, {
    label: name[0].toUpperCase() + name.slice(1),
    billing_model: ({ free: "free", pro: "per_user", team: "per_seat", enterprise: "custom" })[name],
    features: ["Web IDE"],
    monthly_usd: name === "pro" ? 29 : name === "team" ? 15 : index === 0 ? 0 : index * 10,
    ...(name === "pro" ? { annual_usd: 290 } : {}),
    ...(name === "team" ? { annual_usd: 150 } : {}),
  }]));
  const entitlements = Object.fromEntries(PLAN_NAMES.map((name, index) => [name, {
    ide_runs_month: index * 100,
    ai_messages_month: index * 200,
  }]));
  const feature_matrix = Object.fromEntries(PLAN_NAMES.map(name => [
    name, Object.fromEntries(FEATURE_KEYS.map(key => [key, false])),
  ]));
  return {
    currency: "USD", plans, entitlements, feature_matrix,
    stripe_configured: { pro: true, team: true, enterprise: false, pro_annual: true, team_annual: true },
  };
}

test("HTML validator rejects non-HTML and empty responses", () => {
  assert.equal(isHtml({ response: response(), body: "<html>" + "x".repeat(110),
    result: { contentType: "text/html; charset=utf-8" } }), true);
  assert.match(isHtml({ response: response(), body: "not html",
    result: { contentType: "application/json" } }), /expected non-empty HTML/);
  assert.match(isHtml({ response: response(false), body: "x".repeat(200),
    result: { contentType: "text/html" } }), /HTTP 503/);
});

test("IDE route validator requires an SPA root element", () => {
  const valid = { response: response(), body: '<html><div id="root"></div></html>' + "x".repeat(110),
    result: { contentType: "text/html" } };
  assert.equal(isSpaShell(valid), true);
  assert.match(isSpaShell({ ...valid, body: "<html>" + "x".repeat(110) }), /valid SPA shell/);
});

test("health contract fails closed for invalid JSON or degraded database", () => {
  assert.equal(isHealthy(jsonInput({ status: "ok", database: "ok", version: "4.0.0" })), true);
  assert.match(isHealthy(jsonInput({ status: "ok", database: "ok", version: "1.0.0" })), /unexpected release version/);
  assert.match(isHealthy(jsonInput({ status: "ok", database: "error" })), /degraded/);
  assert.match(isHealthy({ response: response(), body: "not-json" }), /valid JSON/);
  assert.match(isHealthy({ response: response(false), body: "{}" }), /HTTP 503/);
});

test("readiness contract requires every named production check", () => {
  const checks = Object.fromEntries(REQUIRED_READINESS_CHECKS.map(name => [name, true]));
  assert.equal(isReady(jsonInput({ status: "ready", checks, version: "4.0.0" })), true);
  assert.match(isReady(jsonInput({ status: "ready", checks, version: "1.0.0" })), /release version must be 4.0.0/);
  for (const missing of REQUIRED_READINESS_CHECKS) {
    const incomplete = { ...checks, [missing]: false };
    assert.match(isReady(jsonInput({ status: "ready", checks: incomplete })), new RegExp(missing));
  }
  assert.match(isReady(jsonInput({ status: "degraded", checks })), /status is not ready/);
});

test("pricing contract accepts complete plans and rejects missing or invalid contracts", () => {
  const valid = validPricing();
  assert.equal(validatePricing(jsonInput(valid)), true);
  assert.match(validatePricing(jsonInput({ ...valid, plans: { free: valid.plans.free } })), /plan definitions/);
  assert.match(validatePricing(jsonInput({ ...valid, currency: "" })), /currency/);
  assert.match(validatePricing(jsonInput({ ...valid, plans: { ...valid.plans, team: { ...valid.plans.team, monthly_usd: 16, annual_usd: 160 } } })), /published Pro\/Team pricing/);
  assert.match(validatePricing(jsonInput({ ...valid, plans: { ...valid.plans, pro: { ...valid.plans.pro, billing_model: "" } } })), /invalid public contract/);
  assert.match(validatePricing(jsonInput({ ...valid, plans: { ...valid.plans, pro: { ...valid.plans.pro, annual_usd: -1 } } })), /invalid public contract/);
  assert.match(validatePricing(jsonInput({ ...valid, plans: { ...valid.plans, pro: { ...valid.plans.pro, features: [""] } } })), /invalid public contract/);
  assert.match(validatePricing(jsonInput({ ...valid, entitlements: { ...valid.entitlements, pro: { ide_runs_month: -1, ai_messages_month: 10 } } })), /entitlements/);
  assert.match(validatePricing(jsonInput({ ...valid, entitlements: { ...valid.entitlements, pro: { ide_runs_month: 1.5, ai_messages_month: 10 } } })), /entitlements/);
  assert.match(validatePricing(jsonInput({ ...valid, feature_matrix: { ...valid.feature_matrix, team: { web_ide: true } } })), /feature flags/);
  assert.match(validatePricing(jsonInput({ ...valid, stripe_configured: { ...valid.stripe_configured, pro: "yes" } })), /Stripe configuration/);
  assert.match(validatePricing(jsonInput({ ...valid, stripe_configured: { pro: true, team: true, enterprise: true } })), /monthly\/annual Stripe configuration/);
  assert.match(validatePricing(jsonInput({ ...valid, stripe_configured: { ...valid.stripe_configured, team_annual: undefined } })), /monthly\/annual Stripe configuration/);
  assert.match(validatePricing(jsonInput({ ...valid, plans: { ...valid.plans, team: { ...valid.plans.team, annual_usd: 0 } } })), /invalid public contract/);
  assert.match(validatePricing(jsonInput({ ...valid, plans: { ...valid.plans, team: { ...valid.plans.team, annual_usd: 149 } } })), /invalid public contract/);
  assert.match(validatePricing({ response: response(), body: "not-json" }), /valid JSON/);
  assert.match(validatePricing({ response: response(false), body: "{}" }), /HTTP 503/);
});

test("billing readiness requires configured price IDs for every paid plan", () => {
  const configured = { pro: true, team: true, enterprise: true, pro_annual: true, team_annual: true };
  assert.equal(validateBillingConfig(jsonInput({ stripe_configured: configured })), true);
  for (const missing of Object.keys(configured)) {
    assert.match(validateBillingConfig(jsonInput({ stripe_configured: { ...configured, [missing]: false } })), /Pro, Team \(monthly and annual\), and Enterprise/);
  }
  assert.match(validateBillingConfig({ response: response(), body: "not-json" }), /valid JSON/);
});

test("security header monitor rejects weak browser-facing headers", () => {
  const secure = {
    response: response(),
    result: { securityHeaders: {
      "strict-transport-security": "max-age=31536000; includeSubDomains",
      "x-content-type-options": "nosniff",
      "x-frame-options": "DENY",
      "referrer-policy": "strict-origin-when-cross-origin",
    } },
  };
  assert.equal(validateSecurityHeaders(secure), true);
  assert.equal(validateSecurityHeaders({ ...secure, result: { securityHeaders: {
    ...secure.result.securityHeaders, "referrer-policy": "same-origin",
  } } }), true);
  assert.match(validateSecurityHeaders({ ...secure, result: { securityHeaders: {
    ...secure.result.securityHeaders, "x-frame-options": "ALLOWALL",
  } } }), /x-frame-options/);
  assert.match(validateSecurityHeaders({ ...secure, result: { securityHeaders: {
    ...secure.result.securityHeaders, "strict-transport-security": "max-age=60",
  } } }), /strict-transport-security/);
  assert.match(validateSecurityHeaders({ response: response(false), result: secure.result }), /HTTP 503/);
});


test("final destination must remain HTTPS and contain no embedded credentials", () => {
  assert.equal(validateFinalUrl({ response: response(), result: { finalUrl: "https://example.test/health" } }), true);
  assert.match(validateFinalUrl({ response: response(), result: { finalUrl: "http://example.test/health" } }), /non-HTTPS/);
  assert.match(validateFinalUrl({ response: response(), result: { finalUrl: "https://user:pass@example.test/health" } }), /credentials/);
  assert.match(validateFinalUrl({ response: response(), result: { finalUrl: "not a url" } }), /invalid/);
  assert.equal(validateFinalUrl({ response: response(), result: {
    url: "https://example.test/ide", finalUrl: "https://example.test/ide/",
  } }), true);
  assert.match(validateFinalUrl({ response: response(), result: {
    url: "https://example.test/ide", finalUrl: "https://untrusted.example/ide",
  } }), /unexpected origin/);
});


test("liveness contract validates process identity without requiring dependencies", () => {
  assert.equal(isLive(jsonInput({ status: "alive", service: "developer-os-api", version: "4.0.0", release_sha: "abc1234" })), true);
  assert.equal(isLive({ ...jsonInput({ status: "alive", service: "developer-os-api", version: "4.0.0", release_sha: "a".repeat(40) }), result: { expectedReleaseSha: "a".repeat(40) } }), true);
  assert.match(isLive({ ...jsonInput({ status: "alive", service: "developer-os-api", version: "4.0.0", release_sha: "a".repeat(40) }), result: { expectedReleaseSha: "b".repeat(40) } }), /does not match the expected repository revision/);
  assert.match(isLive({ ...jsonInput({ status: "alive", service: "developer-os-api", version: "4.0.0", release_sha: "a".repeat(40) }), result: { expectedReleaseSha: "unknown" } }), /does not match the expected repository revision/);
  assert.match(isLive(jsonInput({ status: "alive", service: "developer-os-api", version: "4.0.0", release_sha: "unknown" })), /real 7–40 character hexadecimal release SHA/);
  assert.match(isLive(jsonInput({ status: "alive", service: "developer-os-api", version: "1.0.0", release_sha: "abc1234" })), /expected release version/);
  assert.match(isLive(jsonInput({ status: "ok", database: "ok" })), /liveness contract/);
  assert.match(isLive(jsonInput({ status: "alive", service: "developer-os-api", version: "", release_sha: "abc1234" })), /liveness contract/);
  assert.match(isLive({ response: response(false, 404), body: "{}" }), /HTTP 404/);
  assert.match(isLive({ response: response(), body: "not-json" }), /valid JSON/);
});


test("origin-level diagnosis appears only when all API probes fail at the origin", () => {
  const apiNames = ["api-security-headers", "api-liveness", "api-health", "api-readiness", "api-pricing-contract", "billing-provider-config"];
  const outage = apiNames.map(name => ({ name, ok: false, status: 503 }));
  const diagnosis = diagnoseChecks(outage);
  assert.equal(diagnosis.length, 1);
  assert.equal(diagnosis[0].code, "API_ORIGIN_UNAVAILABLE");
  assert.match(diagnosis[0].action, /Render API service/);
  const partialOriginRecovery = diagnoseChecks([...outage.slice(1), { name: "api-liveness", ok: true, status: 200 }]);
  assert.ok(partialOriginRecovery.some(item => item.code === "API_ORIGIN_DEGRADED"));
  assert.equal(partialOriginRecovery.some(item => item.code === "API_ORIGIN_UNAVAILABLE"), false);
  const routeFailures = diagnoseChecks(apiNames.map(name => ({ name, ok: false, status: 404 })));
  assert.equal(routeFailures.some(item => item.code === "API_ORIGIN_UNAVAILABLE"), false);
  assert.equal(routeFailures.some(item => item.code === "API_ORIGIN_DEGRADED"), false);
  assert.ok(routeFailures.some(item => item.code === "API_LIVENESS_ROUTE_MISSING"));

  // One stale/missing route must not hide a broad outage in independent probes.
  const mixedIncident = diagnoseChecks([
    { name: "api-security-headers", ok: false, status: 503 },
    { name: "api-liveness", ok: false, status: 404 },
    { name: "api-health", ok: false, status: 503 },
    { name: "api-readiness", ok: false, status: 503 },
    { name: "api-pricing-contract", ok: false, status: 503 },
    { name: "billing-provider-config", ok: false, status: 503 },
  ]);
  assert.ok(mixedIncident.some(item => item.code === "API_ORIGIN_DEGRADED"));
  assert.ok(mixedIncident.some(item => item.code === "API_LIVENESS_ROUTE_MISSING"));
});

test("diagnostics identify missing liveness route and incomplete billing configuration", () => {
  const diagnostics = diagnoseChecks([
    { name: "api-liveness", ok: false, status: 404 },
    { name: "billing-provider-config", ok: false, status: 200 },
  ]);
  assert.deepEqual(diagnostics.map(item => item.code), [
    "API_LIVENESS_ROUTE_MISSING",
    "BILLING_PROVIDER_CONFIG_INCOMPLETE",
  ]);
  assert.match(diagnostics[0].action, /do not weaken/);
  assert.match(diagnostics[1].action, /Never print secret values or guess IDs/);
  assert.deepEqual(diagnoseChecks([
    { name: "api-liveness", ok: true, status: 200 },
    { name: "billing-provider-config", ok: true, status: 200 },
  ]), []);
});


test("diagnostics distinguish isolated liveness, health, readiness, and pricing contract failures", () => {
  const liveness = diagnoseChecks([
    { name: "api-liveness", ok: false, status: 503 },
    { name: "api-health", ok: true, status: 200 },
    { name: "api-readiness", ok: true, status: 200 },
    { name: "api-pricing-contract", ok: true, status: 200 },
    { name: "billing-provider-config", ok: true, status: 200 },
  ]);
  assert.ok(liveness.some(item => item.code === "API_LIVENESS_UNHEALTHY"));
  assert.equal(liveness.some(item => item.code === "API_ORIGIN_UNAVAILABLE"), false);

  const timeout = diagnoseChecks([
    { name: "api-liveness", ok: false, status: null },
    { name: "api-health", ok: true, status: 200 },
  ]);
  assert.ok(timeout.some(item => item.code === "API_LIVENESS_UNHEALTHY"));

  const contractFailures = diagnoseChecks([
    { name: "api-health", ok: false, status: 200 },
    { name: "api-readiness", ok: false, status: 200 },
    { name: "api-pricing-contract", ok: false, status: 200 },
  ]);
  assert.deepEqual(contractFailures.map(item => item.code), [
    "API_HEALTH_CONTRACT_FAILED",
    "API_READINESS_CONTRACT_FAILED",
    "PRICING_CONTRACT_INVALID",
  ]);
  assert.ok(contractFailures.every(item => item.severity === "critical"));
});


test("transient 503 responses receive longer bounded retries while permanent failures fail fast", () => {
  assert.equal(retryDelayMs(503, 1), 5000);
  assert.equal(retryDelayMs(503, 2), 12000);
  assert.equal(retryDelayMs(503, 3), 20000);
  assert.equal(retryDelayMs(null, 1), 1500);
  assert.equal(retryDelayMs(429, 2), 2500);
  assert.equal(retryDelayMs(404, 1), 0);
  assert.equal(retryDelayMs(200, 1), 0);
});


test("diagnostics classify frontend availability, SPA shell, and liveness identity failures", () => {
  const frontendUnavailable = diagnoseChecks([
    { name: "frontend-root", ok: false, status: 503 },
  ]);
  assert.equal(frontendUnavailable[0].code, "FRONTEND_ORIGIN_UNAVAILABLE");
  assert.match(frontendUnavailable[0].action, /frontend host/);

  const frontendTimeout = diagnoseChecks([
    { name: "frontend-root", ok: false, status: null },
  ]);
  assert.ok(frontendTimeout.some(item => item.code === "FRONTEND_ORIGIN_UNAVAILABLE"));

  const spaFailure = diagnoseChecks([
    { name: "web-ide-route", ok: false, status: 200 },
    { name: "web-ide-trailing-slash", ok: true, status: 200 },
  ]);
  assert.equal(spaFailure.length, 1);
  assert.equal(spaFailure[0].code, "FRONTEND_ROUTE_CONTRACT_FAILED");
  assert.match(spaFailure[0].message, /SPA-shell contract/);

  const livenessContractFailure = diagnoseChecks([
    { name: "api-liveness", ok: false, status: 200 },
    { name: "api-health", ok: true, status: 200 },
  ]);
  assert.ok(livenessContractFailure.some(item => item.code === "API_LIVENESS_CONTRACT_FAILED"));
  assert.equal(livenessContractFailure.some(item => item.code === "API_LIVENESS_UNHEALTHY"), false);
});
