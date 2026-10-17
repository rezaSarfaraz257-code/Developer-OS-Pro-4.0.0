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

async function timedFetch(url, budgetMs, options = {}) {
  const started = performance.now();
  const response = await fetch(url, {
    redirect: "follow",
    signal: AbortSignal.timeout(Math.max(budgetMs, 10000)),
    headers: { "User-Agent": "DeveloperOS-ProductionPreflight/2.0", ...(options.headers || {}) },
    ...options,
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
    const socket = tls.connect({
      host: parsed.hostname,
      port: 443,
      servername: parsed.hostname,
      minVersion: "TLSv1.2",
      rejectUnauthorized: true,
    }, () => {
      try {
        const cert = socket.getPeerCertificate();
        const protocol = socket.getProtocol();
        socket.end();
        resolve({ cert, protocol });
      } catch (error) { socket.destroy(); reject(error); }
    });
    socket.setTimeout(10000, () => { socket.destroy(new Error("TLS handshake timeout")); });
    socket.on("error", reject);
  });
  const expiresAt = new Date(certificate.cert.valid_to);
  const daysRemaining = (expiresAt.getTime() - Date.now()) / 86400000;
  assert(Number.isFinite(daysRemaining) && daysRemaining >= minCertDays,
    `TLS certificate for ${parsed.hostname} expires too soon: ${daysRemaining.toFixed(1)} days`);
  assert(certificate.protocol === "TLSv1.2" || certificate.protocol === "TLSv1.3",
    `Unsupported TLS protocol negotiated for ${parsed.hostname}: ${certificate.protocol}`);
  return {
    hostname: parsed.hostname,
    addresses: addresses.map(x => x.address),
    tlsProtocol: certificate.protocol,
    tlsExpires: expiresAt.toISOString(),
    tlsDaysRemaining: Number(daysRemaining.toFixed(1)),
  };
}

async function assertHttpRedirectToHttps(url) {
  const parsed = new URL(url);
  const httpUrl = `http://${parsed.host}${parsed.pathname || "/"}`;
  const response = await fetch(httpUrl, {
    redirect: "manual",
    signal: AbortSignal.timeout(10000),
    headers: { "User-Agent": "DeveloperOS-ProductionPreflight/2.0" },
  });
  if (response.status >= 300 && response.status < 400) {
    const location = response.headers.get("location") || "";
    assert(location.startsWith("https://"), `HTTP redirect for ${parsed.hostname} is not HTTPS: ${location}`);
    return { status: response.status, location };
  }
  throw new Error(`HTTP endpoint for ${parsed.hostname} did not redirect to HTTPS (status ${response.status}).`);
}

assert(base && frontend, "PRODUCTION_BASE_URL and PRODUCTION_FRONTEND_URL are required.");
const [apiTarget, frontendTarget] = await Promise.all([dnsAndTls(base), dnsAndTls(frontend)]);
const [apiRedirect, frontendRedirect] = await Promise.all([
  assertHttpRedirectToHttps(base),
  assertHttpRedirectToHttps(frontend),
]);
const health = await timedFetch(`${base}/api/health/`, maxHealthMs);
const ready = await timedFetch(`${base}/api/health/ready/`, maxHealthMs);
const frontendRoot = await timedFetch(`${frontend}/`, maxFrontendMs);

const apiHeaders = {
  "x-content-type-options": "nosniff",
  "referrer-policy": "same-origin",
  "x-frame-options": "DENY",
};
for (const [name, expected] of Object.entries(apiHeaders)) {
  assert((ready.response.headers.get(name) || "").toLowerCase() === expected, `Incorrect API ${name} header`);
}
assert((ready.response.headers.get("strict-transport-security") || "").toLowerCase().includes("max-age="), "API HSTS is missing");

const frontendHeaders = {
  "x-content-type-options": "nosniff",
  "x-frame-options": "DENY",
  "referrer-policy": "strict-origin-when-cross-origin",
  "permissions-policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
  "cross-origin-opener-policy": "same-origin",
  "cross-origin-resource-policy": "same-origin",
};
for (const [name, expected] of Object.entries(frontendHeaders)) {
  assert((frontendRoot.response.headers.get(name) || "").toLowerCase() === expected, `Incorrect frontend ${name} header`);
}
const csp = frontendRoot.response.headers.get("content-security-policy") || "";
for (const directive of ["default-src 'self'", "object-src 'none'", "frame-ancestors 'none'", "base-uri 'self'"]) {
  assert(csp.toLowerCase().includes(directive), `Frontend CSP missing ${directive}`);
}
const contentType = frontendRoot.response.headers.get("content-type") || "";
assert(contentType.includes("text/html"), `Frontend root is not HTML: ${contentType}`);

const report = {
  status: "PASS",
  checkedAt: new Date().toISOString(),
  targets: { api: base, frontend },
  dns: { api: apiTarget, frontend: frontendTarget },
  httpToHttps: { api: apiRedirect, frontend: frontendRedirect },
  latencyMs: { health: health.elapsedMs, readiness: ready.elapsedMs, frontend: frontendRoot.elapsedMs },
  budgetsMs: { health: maxHealthMs, frontend: maxFrontendMs },
  tlsMinDays: minCertDays,
};
console.log(JSON.stringify(report, null, 2));
