import test from "node:test";
import assert from "node:assert/strict";
import { captureMessage, MemorydRuntime } from "../dist/runtime.js";

function userEvent(text, source = { kind: "user" }) {
  return { type: "user/message", data: { content: text, source } };
}

function assistantEvent(text) {
  return {
    type: "assistant/message",
    data: { message: { content: text, source: { kind: "agent" } } },
  };
}

function turnStart() {
  return { type: "turn/start" };
}

function turnEnd() {
  return { type: "turn/end" };
}

test("captureMessage extracts user text", () => {
  const m = captureMessage(userEvent("你好"));
  assert.deepEqual(m, { role: "user", text: "你好" });
});

test("captureMessage extracts assistant text from nested message", () => {
  const m = captureMessage(assistantEvent("回复"));
  assert.deepEqual(m, { role: "assistant", text: "回复" });
});

test("captureMessage extracts text parts from array content", () => {
  const m = captureMessage({
    type: "user/message",
    data: {
      content: [
        { type: "text", text: "第一段" },
        { type: "image", url: "x" },
        { type: "text", text: "第二段" },
      ],
      source: { kind: "user" },
    },
  });
  assert.deepEqual(m, { role: "user", text: "第一段\n\n第二段" });
});

test("captureMessage skips plugin-injected messages (no self-repetition)", () => {
  assert.equal(captureMessage(userEvent("规则", { kind: "plugin" })), null);
});

test("captureMessage skips non-user-sourced user messages", () => {
  assert.equal(captureMessage(userEvent("x", { kind: "system" })), null);
});

test("captureMessage skips empty text", () => {
  assert.equal(captureMessage(userEvent("   ")), null);
});

function makeClient(calls) {
  return {
    ingest: async (dialogue, opts) => {
      calls.push({ dialogue, opts });
      return { ok: true, data: { accepted: true, batch_id: "b1" } };
    },
  };
}

function baseConfig(overrides = {}) {
  return {
    autoMemoryEnabled: true,
    autoMemoryInterval: 2,
    ...overrides,
  };
}

function completeTurn(runtime, sessionId, userText, assistantText) {
  runtime.capture({ id: sessionId }, turnStart());
  runtime.capture({ id: sessionId }, userEvent(userText));
  runtime.capture({ id: sessionId }, assistantEvent(assistantText));
  runtime.capture({ id: sessionId }, turnEnd());
}

test("runtime flushes after autoMemoryInterval complete turns", async () => {
  const calls = [];
  const runtime = new MemorydRuntime(makeClient(calls), baseConfig());
  completeTurn(runtime, "s1", "问题一", "回答一");
  assert.equal(calls.length, 0, "1 turn < interval 2, no flush");
  completeTurn(runtime, "s1", "问题二", "回答二");
  assert.equal(calls.length, 1, "2 turns = interval, flushed once");
  const { dialogue, opts } = calls[0];
  assert.ok(dialogue.some((line) => line.includes("用户: 问题二")));
  assert.ok(dialogue.some((line) => line.includes("助手: 回答二")));
  assert.equal(opts.agentId, "root");
  assert.equal(opts.sessionId, "s1");
});

test("runtime counts only turns with both user and assistant messages", async () => {
  const calls = [];
  const runtime = new MemorydRuntime(makeClient(calls), baseConfig());
  // 只有助手的轮次不算完整轮次
  runtime.capture({ id: "s1" }, turnStart());
  runtime.capture({ id: "s1" }, assistantEvent("只有助手"));
  runtime.capture({ id: "s1" }, turnEnd());
  assert.equal(calls.length, 0);
  // 只含一个用户消息的 turn/start 会清空缓冲，turn 不完整
  runtime.capture({ id: "s1" }, turnStart());
  runtime.capture({ id: "s1" }, userEvent("只有用户"));
  runtime.capture({ id: "s1" }, turnEnd());
  assert.equal(calls.length, 0);
});

test("runtime respects autoMemoryEnabled=false", async () => {
  const calls = [];
  const runtime = new MemorydRuntime(
    makeClient(calls),
    baseConfig({ autoMemoryEnabled: false, autoMemoryInterval: 1 }),
  );
  completeTurn(runtime, "s1", "q", "a");
  assert.equal(calls.length, 0);
});

test("runtime keeps sessions isolated", async () => {
  const calls = [];
  const runtime = new MemorydRuntime(makeClient(calls), baseConfig());
  completeTurn(runtime, "s1", "会话一问题A", "回答A");
  completeTurn(runtime, "s1", "会话一问题B", "回答B"); // s1 攒满 2 轮 → flush
  completeTurn(runtime, "s2", "会话二问题A", "回答A");
  completeTurn(runtime, "s2", "会话二问题B", "回答B"); // s2 攒满 2 轮 → flush
  assert.equal(calls.length, 2);
  assert.equal(calls[0].opts.sessionId, "s1");
  assert.equal(calls[1].opts.sessionId, "s2");
  assert.ok(calls[0].dialogue.some((l) => l.includes("会话一问题")));
  assert.ok(calls[1].dialogue.some((l) => l.includes("会话二问题")));
});

test("runtime flushes pending dialogue on dispose", async () => {
  const calls = [];
  const runtime = new MemorydRuntime(makeClient(calls), baseConfig());
  completeTurn(runtime, "s1", "最后问题", "最后回答"); // 1 < 2，未触发
  assert.equal(calls.length, 0);
  runtime.dispose({ id: "s1" });
  assert.equal(calls.length, 1, "dispose 补交未 flush 的对话");
});
