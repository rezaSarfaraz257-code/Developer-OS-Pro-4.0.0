#!/usr/bin/env node
/**
 * Read-only live SaaS smoke monitor.
 * No credentials, account creation, billing calls, or state mutations.
 */
import fs from "node:fs";

const frontendBase = (process.env.FRONTEND_URL || "").replace(/\/$/, "");
const apiBase = (process.env.API_URL || "").replace(/\/$/, "");
if (!/^https:\/\//i.test(frontendBase) || !/^https:\/\//i.test(apiBase)) {
  throw new Error("FRONTEND_URL and API_URL must both be HTTPS origins.");
}

const checks = [];
async function check(name, url, validate, timeoutMs = 12000) {
  const started = Date.now();
  let lastError = "request failed";
  for (let attempt = 1; attempt <= 2; attempt += 1) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(url, {
        redirect: "follow",
        signal: controller.signal,
        headers: { "User-Agent": "DeveloperOS-LiveSmoke/1.0" },
      });
      const body = await response.text();
      const result = {
        name, url, status: response.status, durationMs: Date.now() - started,
        attempt, contentType: response.headers.get("content-type") || "",
      };
      const validation = validate({ response, body, result });
      if (validation === true) {
        checks.push({ ...result, ok: true });
        return;
      }
      lastError = typeof validation === "string" ? validation : `HTTP ${response.status}`;
      if (attempt === 2) checks.push({ ...result, ok: false, error: lastError });
    } catch (error) {
      lastError = error?.name === "AbortError" ? `timeout after ${timeoutMs}ms` : String(error?.message || error);
      if (attempt === 2) checks.push({
        name, url, status: null, durationMs: Date.now() - started,
        attempt, ok: false, error: lastError,
      });
    } finally {
      clearTimeout(timer);
    }
    if (attempt === 1) await new Promise(resolve => setTimeout(resolve, 700));
  }
}

const isHtml = ({ response, body, result }) =>
  response.ok && result.contentType.toLowerCase().includes("text/html") && body.length > 100
    ? true : `expected non-empty HTML; received HTTP ${response.status} (${result.contentType || "no content-type"})`;
const isHealthy = ({ response, body }) => {
  if (!response.ok) return `HTTP ${response.status}`;
  let json;
  try { json = JSON.parse(body); } catch { return "health response was not valid JSON"; }
  return json.status === "ok" && json.database === "ok"
    ? true : "health contract reports a degraded service or database";
};
const isReady = ({ response, body }) => {
  if (!response.ok) return `HTTP ${response.status}`;
  let json;
  try { json = JSON.parse(body); } catch { return "readiness response was not valid JSON"; }
  const checks = json.checks || {};
  return json.status === "ready" && Object.keys(checks).length > 0 && Object.values(checks).every(value => value === true)
    ? true : "readiness contract has failed or missing checks";
};

await check("frontend-root", `${frontendBase}/`, isHtml);
await check("web-ide-route", `${frontendBase}/ide`, ({ response, body, result }) =>
  isHtml({ response, body, result }) === true &&
  /<div[^>]+id=["'](?:root|app)["']/i.test(body)
    ? true : `IDE route did not return a valid SPA shell (HTTP ${response.status})`);
await check("web-ide-trailing-slash", `${frontendBase}/ide/`, ({ response, body, result }) =>
  isHtml({ response, body, result }) === true &&
  /<div[^>]+id=["'](?:root|app)["']/i.test(body)
    ? true : `IDE trailing-slash route did not return a valid SPA shell (HTTP ${response.status})`);
await check("api-health", `${apiBase}/api/health/`, isHealthy);
await check("api-readiness", `${apiBase}/api/health/ready/`, isReady);
await check("api-pricing-contract", `${apiBase}/api/pricing/`, ({ response, body }) => {
  if (!response.ok) return `HTTP ${response.status}`;
  let json;
  try { json = JSON.parse(body); } catch { return "response was not valid JSON"; }
  const planNames = ["free", "pro", "team", "enterprise"];
  const plans = json?.plans;
  if (!plans || planNames.some(name => !plans[name])) {
    return "pricing response is missing one or more plan definitions";
  }
  if (typeof json.currency !== "string" || !json.currency.trim()) {
    return "pricing response is missing its currency";
  }
  for (const name of planNames) {
    const plan = plans[name];
    if (typeof plan.label !== "string" || !Array.isArray(plan.features) ||
        !Number.isFinite(plan.monthly_usd) || plan.monthly_usd < 0) {
      return `pricing plan '${name}' has an invalid public contract`;
    }
  }
  const entitlements = json.entitlements;
  if (!entitlements || planNames.some(name => !entitlements[name] ||
      !Number.isFinite(entitlements[name].ide_runs_month) ||
      !Number.isFinite(entitlements[name].ai_messages_month))) {
    return "pricing response is missing valid per-plan IDE/AI entitlements";
  }
  return true;
}, 15000);
await check("billing-provider-config", `${apiBase}/api/pricing/`, ({ response, body }) => {
  if (!response.ok) return `HTTP ${response.status}`;
  let json;
  try { json = JSON.parse(body); } catch { return "response was not valid JSON"; }
  return json?.stripe_configured?.pro === true && json?.stripe_configured?.team === true
    ? true : "Stripe price IDs for Pro and Team are not configured in the deployed API";
}, 15000);

const report = {
  schemaVersion: 1,
  timestamp: new Date().toISOString(),
  frontendBase,
  apiBase,
  ok: checks.length === 7 && checks.every(item => item.ok),
  checks,
};
fs.writeFileSync("live-saas-smoke.json", JSON.stringify(report, null, 2) + "\n", { mode: 0o600 });
console.log(JSON.stringify(report, null, 2));
if (!report.ok) process.exitCode = 1;
