import test from "node:test";
import assert from "node:assert/strict";
import {
  isHtml, isSpaShell, isHealthy, isReady, validatePricing, validateBillingConfig,
  PLAN_NAMES, REQUIRED_READINESS_CHECKS, FEATURE_KEYS,
} from "./live_saas_contracts.mjs";

const response = (ok = true, status = ok ? 200 : 503) => ({ ok, status });
const jsonInput = (value, ok = true) => ({ response: response(ok), body: JSON.stringify(value) });

function validPricing() {
  const plans = Object.fromEntries(PLAN_NAMES.map((name, index) => [name, {
    label: name[0].toUpperCase() + name.slice(1),
    billing_model: ({ free: "free", pro: "per_user", team: "per_seat", enterprise: "custom" })[name],
    features: ["Web IDE"],
    monthly_usd: index === 0 ? 0 : index * 10,
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
    stripe_configured: { pro: true, team: true, enterprise: false },
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
  assert.equal(isHealthy(jsonInput({ status: "ok", database: "ok" })), true);
  assert.match(isHealthy(jsonInput({ status: "ok", database: "error" })), /degraded/);
  assert.match(isHealthy({ response: response(), body: "not-json" }), /valid JSON/);
  assert.match(isHealthy({ response: response(false), body: "{}" }), /HTTP 503/);
});

test("readiness contract requires every named production check", () => {
  const checks = Object.fromEntries(REQUIRED_READINESS_CHECKS.map(name => [name, true]));
  assert.equal(isReady(jsonInput({ status: "ready", checks })), true);
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
  assert.match(validatePricing(jsonInput({ ...valid, plans: { ...valid.plans, pro: { ...valid.plans.pro, billing_model: "" } } })), /invalid public contract/);
  assert.match(validatePricing(jsonInput({ ...valid, plans: { ...valid.plans, pro: { ...valid.plans.pro, annual_usd: -1 } } })), /invalid public contract/);
  assert.match(validatePricing(jsonInput({ ...valid, plans: { ...valid.plans, pro: { ...valid.plans.pro, features: [""] } } })), /invalid public contract/);
  assert.match(validatePricing(jsonInput({ ...valid, entitlements: { ...valid.entitlements, pro: { ide_runs_month: -1, ai_messages_month: 10 } } })), /entitlements/);
  assert.match(validatePricing(jsonInput({ ...valid, entitlements: { ...valid.entitlements, pro: { ide_runs_month: 1.5, ai_messages_month: 10 } } })), /entitlements/);
  assert.match(validatePricing(jsonInput({ ...valid, feature_matrix: { ...valid.feature_matrix, team: { web_ide: true } } })), /feature flags/);
  assert.match(validatePricing(jsonInput({ ...valid, stripe_configured: { pro: "yes", team: true, enterprise: false } })), /Stripe configuration/);
  assert.match(validatePricing({ response: response(), body: "not-json" }), /valid JSON/);
  assert.match(validatePricing({ response: response(false), body: "{}" }), /HTTP 503/);
});

test("billing readiness requires configured Pro and Team price IDs", () => {
  assert.equal(validateBillingConfig(jsonInput({ stripe_configured: { pro: true, team: true } })), true);
  assert.match(validateBillingConfig(jsonInput({ stripe_configured: { pro: false, team: true } })), /not configured/);
  assert.match(validateBillingConfig({ response: response(), body: "not-json" }), /valid JSON/);
});
