import test from "node:test";
import assert from "node:assert/strict";
import { normalizeProfileLink, safeProfileLink } from "../src/utils/profile.js";

test("allows valid absolute HTTP and HTTPS profile links", () => {
  assert.equal(normalizeProfileLink("https://github.com/example"), "https://github.com/example");
  assert.equal(normalizeProfileLink("http://example.com/profile"), "http://example.com/profile");
});

test("normalizes surrounding whitespace and empty optional links", () => {
  assert.equal(normalizeProfileLink("  https://example.com  "), "https://example.com/");
  assert.equal(normalizeProfileLink(""), "");
  assert.equal(normalizeProfileLink(null), "");
});

test("rejects executable schemes, unsupported protocols, and embedded credentials", () => {
  for (const value of [
    "javascript:alert(1)",
    "data:text/html,hello",
    "ftp://example.com",
    "https://user:pass@example.com",
    "not a URL",
  ]) {
    assert.throws(() => normalizeProfileLink(value));
  }
});

test("safe preview links omit malformed or unsafe values", () => {
  assert.equal(safeProfileLink("javascript:alert(1)"), "");
  assert.equal(safeProfileLink("https://example.com"), "https://example.com/");
});
