#!/usr/bin/env node
/**
 * Read-only production preflight. No authentication, writes, migrations,
 * billing events, or destructive operations are performed here.
 */
import dns from "node:dns/promises";
import tls from "node:tls";

const base = (process.env.PRODUCTION_BASE_URL || "").replace(/\/$/, "");
const frontend = (process.env.PRODUCTION_FRONTEND_URL || "").replace(/\/$/, "");
const maxHealthMs = Number(process.env.PRODUCTION_HEALTH_BUDGET_MS || 2000);
const maxFrontendMs = Number(process.env.PRODUCTION_FRONTEND_BUDGET_MS || 3000);
const minCertDays = Number(process.env.PRODUCTION_MIN_TLS_DAYS || 14);

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function timedFetch(url, budgetMs) {
  const started = performance.now();
  const response = await fetch(url, {
    redirect: "follow",
    signal: AbortSignal.timeout(Math.max(budgetMs, 10000)),
    headers: { "User-Agent": "DeveloperOS-ProductionPreflight/1.0" },
  });
  const elapsedMs = Math.round(performance.now() - started);
  assert(response.ok, `${url} -> HTTP ${response.status}`);
  assert(elapsedMs <= budgetMs, `${url} exceeded ${budgetMs}ms budget: ${elapsedMs}ms`);
  return { response, elapsedMs };
}

async function dnsAndTls(url) {
  const parsed = new URL(url);
  assert(parsed.protocol === "https:", `${url} must use HTTPS`);
  const addresses = await dns.lookup(parsed.hostname, { all: true });
  assert(addresses.length > 0, `DNS returned no addresses for ${parsed.hostname}`);
  const certificate = await new Promise((resolve, reject) => {
    const socket = tls.connect({ host: parsed.hostname, port: 443, servername: parsed.hostname, rejectUnauthorized: true }, () => {
      try {
        const cert = socket.getPeerCertificate();
        socket.end();
        resolve(cert);
      } catch (error) { socket.destroy(); reject(error); }
    });
    socket.setTimeout(10000, () => { socket.destroy(new Error("TLS handshake timeout")); });
    socket.on("error", reject);
  });
  const expiresAt = new Date(certificate.valid_to);
  const daysRemaining = (expiresAt.getTime() - Date.now()) / 86400000;
  assert(Number.isFinite(daysRemaining) && daysRemaining >= minCertDays,
    `TLS certificate for ${parsed.hostname} expires too soon: ${daysRemaining.toFixed(1)} days`);
  return { hostname: parsed.hostname, addresses: addresses.map(x => x.address), tlsExpires: expiresAt.toISOString(), tlsDaysRemaining: Number(daysRemaining.toFixed(1)) };
}

assert(base && frontend, "PRODUCTION_BASE_URL and PRODUCTION_FRONTEND_URL are required.");
const [apiTarget, frontendTarget] = await Promise.all([dnsAndTls(base), dnsAndTls(frontend)]);
const health = await timedFetch(`${base}/api/health/`, maxHealthMs);
const ready = await timedFetch(`${base}/api/health/ready/`, maxHealthMs);
const frontendRoot = await timedFetch(`${frontend}/`, maxFrontendMs);

const requiredHeaders = {
  "x-content-type-options": "nosniff",
  "referrer-policy": "same-origin",
  "x-frame-options": "DENY",
};
for (const [name, expected] of Object.entries(requiredHeaders)) {
  assert((ready.response.headers.get(name) || "").toLowerCase() === expected, `Incorrect ${name} header`);
}
assert((ready.response.headers.get("strict-transport-security") || "").toLowerCase().includes("max-age="), "HSTS is missing");
const contentType = frontendRoot.response.headers.get("content-type") || "";
assert(contentType.includes("text/html"), `Frontend root is not HTML: ${contentType}`);

const report = {
  status: "PASS",
  checkedAt: new Date().toISOString(),
  targets: { api: base, frontend },
  dns: { api: apiTarget, frontend: frontendTarget },
  latencyMs: { health: health.elapsedMs, readiness: ready.elapsedMs, frontend: frontendRoot.elapsedMs },
  budgetsMs: { health: maxHealthMs, frontend: maxFrontendMs },
  tlsMinDays: minCertDays,
};
console.log(JSON.stringify(report, null, 2));
