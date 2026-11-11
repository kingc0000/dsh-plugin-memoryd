import test from "node:test";
import assert from "node:assert/strict";
import { memoryGuidance, hasGuidance, PLUGIN_SOURCE } from "../dist/guidance.js";
import { fmtScope, fmtSearch, clamp } from "../dist/tools.js";

test("memoryGuidance returns zh by default and for unknown languages", () => {
  assert.ok(memoryGuidance("zh").includes("memoryd"));
  assert.ok(memoryGuidance("whatever").includes("memoryd"));
  assert.equal(memoryGuidance("zh"), memoryGuidance("fr"));
});

test("memoryGuidance returns en content for en", () => {
  const en = memoryGuidance("en");
  assert.ok(en.includes("memory_search"));
  assert.ok(en.includes("memory_promote"));
});

test("hasGuidance detects already-injected guidance in session events", () => {
  const session = {
    events: [
      {
        type: "user/message",
        data: {
          content: "guidance",
          source: { kind: PLUGIN_SOURCE, form: "instructions" },
        },
      },
    ],
  };
  assert.equal(hasGuidance(session), true);
  assert.equal(hasGuidance({ events: [] }), false);
});

test("hasGuidance detects guidance in pending messages", () => {
  const pending = [{ source: { kind: "plugin", plugin: PLUGIN_SOURCE, form: "instructions" } }];
  assert.equal(hasGuidance({ events: [] }, pending), true);
  assert.equal(hasGuidance({ events: [] }, [{ source: { kind: "other" } }]), false);
});

test("fmtScope labels scope with project ref and origin", () => {
  assert.equal(
    fmtScope({ id: 1, scope: "project", scope_ref: "etuangou", content: "x" }),
    "[project:etuangou]",
  );
  assert.equal(
    fmtScope({
      id: 2,
      scope: "user",
      content: "x",
      origin: { device_id: 1, agent_id: "root" },
    }),
    "[user] 来源:设备1/root",
  );
});

test("fmtSearch renders items and empty state", () => {
  const result = {
    data: {
      results: [
        { id: 1, scope: "user", content: "偏好", updated_at: "2026-11-10 22:00:00" },
      ],
    },
  };
  const text = fmtSearch(result, 8);
  assert.ok(text.includes("偏好"));
  assert.ok(text.includes("2026-11-10"));
  assert.equal(fmtSearch({ data: { results: [] } }, 8), "未检索到相关记忆。");
});

test("clamp bounds and falls back on non-numeric input", () => {
  assert.equal(clamp(99, 1, 50, 8), 50);
  assert.equal(clamp(0, 1, 50, 8), 1);
  assert.equal(clamp("abc", 1, 50, 8), 8);
  assert.equal(clamp(12.6, 1, 50, 8), 13);
});
