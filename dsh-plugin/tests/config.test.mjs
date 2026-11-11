import test from "node:test";
import assert from "node:assert/strict";
import { DEFAULT_CONFIG, resolveConfig } from "../dist/config.js";

test("resolveConfig returns defaults for empty input", () => {
  const cfg = resolveConfig({}, {});
  assert.deepEqual(cfg, { ...DEFAULT_CONFIG, apiKey: "" });
});

test("resolveConfig reads env MEMORYD_API_KEY as fallback", () => {
  const cfg = resolveConfig({}, { MEMORYD_API_KEY: "tok-123" });
  assert.equal(cfg.apiKey, "tok-123");
});

test("resolveConfig prefers explicit apiKey over env", () => {
  const cfg = resolveConfig({ apiKey: "explicit" }, { MEMORYD_API_KEY: "env" });
  assert.equal(cfg.apiKey, "explicit");
});

test("resolveConfig resolves getter-style (volatile) values", () => {
  const cfg = resolveConfig({ endpoint: { get: () => "http://example:9000" } }, {});
  assert.equal(cfg.endpoint, "http://example:9000");
});

test("resolveConfig fails fast on unknown keys", () => {
  assert.throws(() => resolveConfig({ nope: 1 }, {}), TypeError);
});

test("resolveConfig returns a frozen-shaped config object", () => {
  const cfg = resolveConfig({}, {});
  // 关键字段齐备且类型正确
  assert.equal(typeof cfg.endpoint, "string");
  assert.equal(typeof cfg.requestTimeoutMs, "number");
  assert.equal(typeof cfg.searchLimit, "number");
  assert.equal(typeof cfg.autoMemoryEnabled, "boolean");
  assert.ok(["zh", "en"].includes(cfg.language));
});
