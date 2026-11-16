import test from "node:test";
import assert from "node:assert/strict";
import { renderLiveSaasSummary } from "./live_saas_summary.mjs";

const baseReport = () => ({
  ok: false,
  timestamp: "2026-11-17T12:00:00.000Z",
  durationMs: 4200,
  frontendBase: "https://frontend.example",
  apiBase: "https://api.example",
  summary: { total: 2, passed: 1, failed: 1, recovered: 0 },
  diagnostics: [{ severity: "critical", code: "API_READINESS_BLOCKED", message: "Readiness failed", action: "Inspect safe service diagnostics." }],
  checks: [
    { name: "frontend-root", ok: true, status: 200, attempts: 1, durationMs: 100, error: null },
    {
      name: "api-readiness",
      ok: false,
      status: 503,
      attempts: 2,
      durationMs: 4100,
      error: "HTTP 503; failed or missing checks: cache, runner",
      healthDiagnostics: {
        diagnostics: {
          cache: "cache_backend_error:TimeoutError",
          runner: "runner_http_status:503",
          secret: "redis://user:password@internal-host",
          injected: "token-value",
        },
      },
    },
  ],
});

test("renders summary, health diagnostics, and per-check evidence", () => {
  const output = renderLiveSaasSummary(baseReport());
  assert.match(output, /Developer OS — Live SaaS Observability/);
  assert.match(output, /Checks passed \| 1\/2/);
  assert.match(output, /Sanitized dependency diagnostics/);
  assert.match(output, /\*\*api-readiness \/ cache:\*\* cache&#95;backend&#95;error:TimeoutError/);
  assert.match(output, /\*\*api-readiness \/ runner:\*\* runner&#95;http&#95;status:503/);
  assert.match(output, /api-readiness \| 503 \| 2 \| 4100 ms \| FAIL/);
});

test("never renders unknown diagnostic keys or secret-like diagnostic values", () => {
  const output = renderLiveSaasSummary(baseReport());
  assert.doesNotMatch(output, /redis:\/\/|password|internal-host|token-value|injected/);
  assert.doesNotMatch(output, /\| secret:/);
});

test("escapes HTML, Markdown links, formatting, and multiline table injection", () => {
  const report = baseReport();
  report.checks[1].error = "<script>alert(1)</script> | forged row\n| injected [open](https://evil.example) ![img](https://evil.example/x.png) \`code\` **bold**";
  const output = renderLiveSaasSummary(report);
  assert.doesNotMatch(output, /<script>|\]\(https:\/\/evil\.example|\`code\`|\*\*bold\*\*/);
  assert.doesNotMatch(output, /\n\| injected/);
  assert.match(output, /&lt;script&gt;alert&#40;1&#41;&lt;\/script&gt; &#124; forged row &#124; injected/);
  assert.ok(output.includes("&#91;open&#93;&#40;https://evil&#46;example&#41;"));
});

test("omits optional diagnostic sections when no diagnostics are present", () => {
  const report = baseReport();
  report.diagnostics = [];
  report.checks[1].healthDiagnostics = null;
  const output = renderLiveSaasSummary(report);
  assert.doesNotMatch(output, /## Diagnostics/);
  assert.doesNotMatch(output, /Sanitized dependency diagnostics/);
});

test("rejects malformed report structures before rendering", () => {
  assert.throws(() => renderLiveSaasSummary(null), /Invalid live SaaS smoke report/);
  assert.throws(() => renderLiveSaasSummary({ checks: {} }), /Invalid live SaaS smoke report/);
  assert.throws(() => renderLiveSaasSummary({ ...baseReport(), checks: [null] }), /Invalid live SaaS smoke report/);
  assert.throws(() => renderLiveSaasSummary({ ...baseReport(), diagnostics: [null] }), /Invalid live SaaS smoke report/);
  assert.throws(() => renderLiveSaasSummary({ ...baseReport(), summary: [] }), /Invalid live SaaS smoke report/);
});
