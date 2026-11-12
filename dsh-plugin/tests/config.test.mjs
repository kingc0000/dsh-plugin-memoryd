import test from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import { DEFAULT_CONFIG, resolveConfig } from "../dist/config.js";

test("resolveConfig returns defaults for empty input", () => {
  const cfg = resolveConfig({}, {});
  assert.equal(cfg.token, "");
  assert.equal(cfg.apiKey, "");
  assert.equal(cfg.deviceName, os.hostname());
  assert.match(cfg.tokenFile, /\.memoryd[\\/]token$/);
  assert.match(cfg.fingerprintFile, /\.memoryd[\\/]fingerprint$/);
  assert.equal(cfg.registrationPollMs, 5000);
  assert.equal(cfg.autoRegister, true);
});

test("resolveConfig reads env MEMORYD_API_KEY as fallback", () => {
  const cfg = resolveConfig({}, { MEMORYD_API_KEY: "tok-123" });
  assert.equal(cfg.token, "tok-123");
});

test("resolveConfig prefers explicit token over env", () => {
  const cfg = resolveConfig({ token: "explicit" }, { MEMORYD_TOKEN: "env" });
  assert.equal(cfg.token, "explicit");
});

test("resolveConfig: token beats deprecated apiKey alias", () => {
  const cfg = resolveConfig({ token: "new", apiKey: "old" }, {});
  assert.equal(cfg.token, "new");
});

test("resolveConfig: deprecated apiKey alias still works", () => {
  const cfg = resolveConfig({ apiKey: "legacy" }, {});
  assert.equal(cfg.token, "legacy");
  assert.equal(cfg.apiKey, "legacy");
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
