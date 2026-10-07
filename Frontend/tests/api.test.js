import test from "node:test";
import assert from "node:assert/strict";

function safeExternalUrl(value) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

test("safeExternalUrl accepts http and https URLs", () => {
  assert.equal(safeExternalUrl("https://example.com/path"), "https://example.com/path");
  assert.equal(safeExternalUrl("http://localhost:5173"), "http://localhost:5173/");
});

test("safeExternalUrl rejects dangerous or malformed URLs", () => {
  assert.equal(safeExternalUrl("javascript:alert(1)"), null);
  assert.equal(safeExternalUrl("data:text/html,test"), null);
  assert.equal(safeExternalUrl("not-a-url"), null);
  assert.equal(safeExternalUrl(""), null);
});


test("API errors include HTTP status for operator diagnostics", async () => {
  const source = await import("../src/services/api.js");
  assert.equal(source.formatApiError({ error: { message: "AI unavailable" } }), "AI unavailable");
});


test("API client defaults to same-origin instead of a hard-coded Render backend", async () => {
  const source = await import("../src/services/api.js");
  assert.equal(source.API_URL, "/api");
  assert.equal(source.API_URL.includes("onrender.com"), false);
});
