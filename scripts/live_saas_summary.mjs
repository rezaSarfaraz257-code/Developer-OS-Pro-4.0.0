#!/usr/bin/env node
/** Render the read-only production smoke report for GitHub Actions. */
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const SAFE_DEPENDENCY_DIAGNOSTIC = /^(?:cache_url_missing|redis_backend_not_configured|cache_roundtrip_mismatch|cache_backend_error:[A-Za-z][A-Za-z0-9_]{0,63}|cache_unavailable|runner_url_missing|runner_token_missing|runner_invalid_response|runner_capability_contract_mismatch|runner_http_status:[1-5][0-9]{2}|runner_request_error:[A-Za-z][A-Za-z0-9_]{0,63})$/;
const isRecord = value => value !== null && typeof value === "object" && !Array.isArray(value);

function cell(value) {
  return String(value ?? "—")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\|/g, "&#124;")
    .replace(/[\r\n]+/g, " ")
    // Prevent untrusted report values from creating Markdown links, images,
    // emphasis, code spans, or headings in the GitHub job summary.
    .replace(/[\x60*_{}\[\]()+.!~]/g, character => ({
      "\x60": "&#96;", "*": "&#42;", "_": "&#95;", "{": "&#123;",
      "}": "&#125;", "[": "&#91;", "]": "&#93;", "(": "&#40;",
      ")": "&#41;", "+": "&#43;", ".": "&#46;",
      "!": "&#33;", "~": "&#126;",
    })[character])
    .trim();
}

export function renderLiveSaasSummary(report) {
  if (!isRecord(report) || !Array.isArray(report.checks) ||
      !report.checks.every(isRecord) || !isRecord(report.summary) ||
      (report.diagnostics !== undefined &&
        (!Array.isArray(report.diagnostics) || !report.diagnostics.every(isRecord)))) {
    throw new TypeError("Invalid live SaaS smoke report.");
  }

  const summary = report.summary;
  const lines = [
    "# Developer OS — Live SaaS Observability",
    "",
    `**Result:** ${report.ok === true ? "PASS" : "FAIL"} · **Timestamp:** ${cell(report.timestamp)} · **Duration:** ${cell(report.durationMs)} ms`,
    "",
    "| Signal | Result |",
    "|---|---:|",
    `| Checks passed | ${cell(summary.passed)}/${cell(summary.total)} |`,
    `| Failed checks | ${cell(summary.failed)} |`,
    `| Recovered after retry | ${cell(summary.recovered)} |`,
    `| Frontend | ${cell(report.frontendBase)} |`,
    `| API | ${cell(report.apiBase)} |`,
  ];

  if (report.diagnostics?.length) {
    lines.push("", "## Diagnostics", "");
    for (const diagnostic of report.diagnostics) {
      lines.push(`- **${cell(diagnostic.severity).toUpperCase()} / ${cell(diagnostic.code)}:** ${cell(diagnostic.message)} Action: ${cell(diagnostic.action)}`);
    }
  }

  const dependencyDiagnostics = report.checks.flatMap(check => {
    const source = check.healthDiagnostics?.diagnostics;
    if (!isRecord(source)) return [];
    return Object.entries(source)
      .filter(([key, value]) =>
        (key === "cache" || key === "runner") &&
        typeof value === "string" &&
        SAFE_DEPENDENCY_DIAGNOSTIC.test(value))
      .map(([key, value]) => ({ check: check.name, key, value }));
  });

  if (dependencyDiagnostics.length) {
    lines.push("", "## Sanitized dependency diagnostics", "");
    for (const diagnostic of dependencyDiagnostics) {
      lines.push(`- **${cell(diagnostic.check)} / ${cell(diagnostic.key)}:** ${cell(diagnostic.value)}`);
    }
  }

  lines.push(
    "",
    "| Check | HTTP | Attempts | Duration | Status | Details |",
    "|---|---:|---:|---:|---|---|",
  );
  for (const check of report.checks) {
    const status = check.ok ? (check.recovered ? "RECOVERED" : "PASS") : "FAIL";
    const detail = check.ok ? "—" : cell(check.error || "failed");
    lines.push(`| ${cell(check.name)} | ${cell(check.status)} | ${cell(check.attempts)} | ${cell(check.durationMs)} ms | ${status} | ${detail} |`);
  }

  lines.push(
    "",
    "> Read-only external smoke monitoring only. This does not authenticate, mutate production data, execute user code, or replace full release acceptance.",
    "",
  );
  return lines.join("\n");
}

function main() {
  const reportPath = path.resolve(process.argv[2] || "live-saas-smoke.json");
  const report = JSON.parse(fs.readFileSync(reportPath, "utf8"));
  const summary = renderLiveSaasSummary(report);
  if (process.env.GITHUB_STEP_SUMMARY) {
    fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, summary);
  } else {
    process.stdout.write(summary);
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main();
}
