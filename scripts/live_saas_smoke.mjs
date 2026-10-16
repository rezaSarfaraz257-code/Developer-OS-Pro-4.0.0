#!/usr/bin/env node
/**
 * Read-only live SaaS smoke monitor.
 * No credentials, account creation, billing calls, or state mutations.
 */
import fs from "node:fs";
import { isHtml, isSpaShell, isHealthy, isReady, validatePricing, validateBillingConfig, validateSecurityHeaders, validateFinalUrl } from "./live_saas_contracts.mjs";

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
        securityHeaders: Object.fromEntries(["strict-transport-security", "x-content-type-options", "x-frame-options", "referrer-policy"].map(name => [name, response.headers.get(name) || ""])),
        finalUrl: response.url,
      };
      const destinationValidation = validateFinalUrl({ response, body, result });
      const validation = destinationValidation === true ? validate({ response, body, result }) : destinationValidation;
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

await check("frontend-root", `${frontendBase}/`, isHtml);
await check("frontend-security-headers", `${frontendBase}/`, validateSecurityHeaders);
await check("api-security-headers", `${apiBase}/api/health/`, validateSecurityHeaders);
await check("web-ide-route", `${frontendBase}/ide`, isSpaShell);
await check("web-ide-trailing-slash", `${frontendBase}/ide/`, isSpaShell);
await check("api-health", `${apiBase}/api/health/`, isHealthy);
await check("api-readiness", `${apiBase}/api/health/ready/`, isReady);
await check("api-pricing-contract", `${apiBase}/api/pricing/`, validatePricing, 15000);
await check("billing-provider-config", `${apiBase}/api/pricing/`, validateBillingConfig, 15000);

const report = {
  schemaVersion: 1,
  timestamp: new Date().toISOString(),
  frontendBase,
  apiBase,
  ok: checks.length === 9 && checks.every(item => item.ok),
  checks,
};
fs.writeFileSync("live-saas-smoke.json", JSON.stringify(report, null, 2) + "\n", { mode: 0o600 });
console.log(JSON.stringify(report, null, 2));
if (!report.ok) process.exitCode = 1;
