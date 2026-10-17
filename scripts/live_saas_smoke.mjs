#!/usr/bin/env node
/**
 * Read-only live SaaS smoke monitor.
 * No credentials, account creation, billing calls, or state mutations.
 */
import fs from "node:fs";
import { isHtml, isSpaShell, isLive, isHealthy, isReady, validatePricing, validateBillingConfig, validateSecurityHeaders, validateNoStoreCacheHeaders, validateFinalUrl, diagnoseChecks, retryDelayMs, isRetryableReleaseMismatch, releaseMismatchRetryDelayMs } from "./live_saas_contracts.mjs";

function requireHttpsOrigin(name, value) {
  let parsed;
  try { parsed = new URL(value || ""); } catch { throw new Error(name + " must be a valid HTTPS origin."); }
  if (parsed.protocol !== "https:" || parsed.username || parsed.password ||
      parsed.pathname !== "/" || parsed.search || parsed.hash) {
    throw new Error(name + " must be an HTTPS origin without credentials, path, query, or fragment.");
  }
  return parsed.origin;
}

const frontendBase = requireHttpsOrigin("FRONTEND_URL", process.env.FRONTEND_URL || "");
const apiBase = requireHttpsOrigin("API_URL", process.env.API_URL || "");
const checks = [];
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const maxAttempts = 4;
const retryableHttpStatus = status => status === 408 || status === 425 || status === 429 || status >= 500;

async function check(name, url, validate, timeoutMs = 12000) {
  const started = Date.now();
  const attempts = [];
  let finalResult = null;
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    const attemptStarted = Date.now();
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(url, {
        redirect: "follow",
        signal: controller.signal,
        headers: { "User-Agent": "DeveloperOS-LiveSmoke/2.0", "Accept": "application/json, text/html;q=0.9, */*;q=0.8" },
      });
      const body = await response.text();
      const result = {
        name, url, status: response.status, statusText: response.statusText, durationMs: Date.now() - attemptStarted,
        attempt, contentType: response.headers.get("content-type") || "",
        platformHeaders: Object.fromEntries(["server", "retry-after", "x-render-routing", "x-request-id"].map(header => [header, response.headers.get(header) || ""])) ,
        securityHeaders: Object.fromEntries(["strict-transport-security", "x-content-type-options", "x-frame-options", "referrer-policy"].map(header => [header, response.headers.get(header) || ""])),
        cacheHeaders: Object.fromEntries(["cache-control", "pragma"].map(header => [header, response.headers.get(header) || ""])),
        finalUrl: response.url,
        expectedReleaseSha: process.env.EXPECTED_RELEASE_SHA || "",
      };
      const destinationValidation = validateFinalUrl({ response, body, result });
      const validation = destinationValidation === true ? validate({ response, body, result }) : destinationValidation;
      const attemptRecord = {
        attempt,
        timestamp: new Date().toISOString(),
        durationMs: result.durationMs,
        status: response.status,
        ok: validation === true,
        error: validation === true ? null : String(validation),
      };
      attempts.push(attemptRecord);
      finalResult = { ...result, ok: validation === true, error: validation === true ? null : String(validation) };
      if (validation === true) break;

      // Retry transient HTTP failures and a healthy-but-stale release identity
      // during rollout. Other contract failures (for example missing Stripe
      // configuration on HTTP 200) remain deterministic and fail fast.
      if ((!retryableHttpStatus(response.status) && !isRetryableReleaseMismatch(validation)) || attempt === maxAttempts) break;
    } catch (error) {
      const errorText = error?.name === "AbortError" ? "timeout after " + timeoutMs + "ms" : String(error?.message || error);
      attempts.push({
        attempt,
        timestamp: new Date().toISOString(),
        durationMs: Date.now() - attemptStarted,
        status: null,
        ok: false,
        error: errorText,
      });
      finalResult = {
        name, url, status: null, durationMs: Date.now() - started,
        attempt, ok: false, error: errorText,
      };
      if (attempt === maxAttempts) break;
    } finally {
      clearTimeout(timer);
    }
    const lastAttempt = attempts.at(-1);
    const retryStatus = lastAttempt?.status ?? null;
    const delayMs = isRetryableReleaseMismatch(lastAttempt?.error)
      ? releaseMismatchRetryDelayMs(attempt)
      : retryDelayMs(retryStatus, attempt);
    if (delayMs > 0) await sleep(delayMs);
  }

  const recovered = finalResult?.ok === true && attempts.length > 1;
  checks.push({
    ...finalResult,
    durationMs: Date.now() - started,
    attempts: attempts.length,
    recovered,
    attemptHistory: attempts,
  });
}

await check("frontend-root", frontendBase + "/", isHtml);
await check("frontend-security-headers", frontendBase + "/", validateSecurityHeaders);
await check("api-security-headers", apiBase + "/api/health/", validateSecurityHeaders);
await check("web-ide-route", frontendBase + "/ide", isSpaShell);
await check("web-ide-trailing-slash", frontendBase + "/ide/", isSpaShell);
await check("api-liveness", apiBase + "/api/health/live/", isLive);
await check("api-health", apiBase + "/api/health/", isHealthy);
await check("api-readiness", apiBase + "/api/health/ready/", isReady);
await check("api-liveness-cache-policy", apiBase + "/api/health/live/", validateNoStoreCacheHeaders);
await check("api-health-cache-policy", apiBase + "/api/health/", validateNoStoreCacheHeaders);
await check("api-readiness-cache-policy", apiBase + "/api/health/ready/", validateNoStoreCacheHeaders);
await check("api-pricing-contract", apiBase + "/api/pricing/", validatePricing, 15000);
await check("billing-provider-config", apiBase + "/api/pricing/", validateBillingConfig, 15000);

const report = {
  schemaVersion: 2,
  monitor: "Developer OS Live SaaS Observability",
  timestamp: new Date().toISOString(),
  frontendBase,
  apiBase,
  durationMs: checks.reduce((total, item) => total + item.durationMs, 0),
  summary: {
    total: checks.length,
    passed: checks.filter(item => item.ok).length,
    failed: checks.filter(item => !item.ok).length,
    recovered: checks.filter(item => item.recovered).length,
  },
  ok: checks.length === 13 && checks.every(item => item.ok),
  diagnostics: diagnoseChecks(checks),
  checks,
};
fs.writeFileSync("live-saas-smoke.json", JSON.stringify(report, null, 2) + "\n", { mode: 0o600 });
console.log(JSON.stringify(report, null, 2));
if (!report.ok) process.exitCode = 1;
