/** 插件入口：DSH cordis 插件（memoryd-memory）。 */
import { createUserMessage } from "@deepseek-ai/dsh-llm";
import { MemorydClient } from "./client.js";
import { resolveConfig, Config, type MemorydPluginConfig } from "./config.js";
import { hasGuidance, memoryGuidance, PLUGIN_SOURCE } from "./guidance.js";
import { MemorydRuntime } from "./runtime.js";
import { registerMemorydTools } from "./tools.js";
import { TokenStore } from "./token.js";

export const name = "memoryd-memory";
export const inject = ["agents", "sessions", "tools"];

interface PluginContext {
  logger?: { info?: (m: string) => void; warn?: (m: string) => void };
  provide: (name: string, value: unknown) => void;
  effect: (fn: () => (() => void) | void, name?: string) => void;
  on: (event: string, handler: (...args: never[]) => void) => void;
}

interface MemorydConfigInput {
  endpoint?: string | { get?: () => unknown };
  apiKey?: string | { get?: () => unknown };
  requestTimeoutMs?: number | { get?: () => unknown };
  searchLimit?: number | { get?: () => unknown };
  autoMemoryEnabled?: boolean | { get?: () => unknown };
  autoMemoryInterval?: number | { get?: () => unknown };
  rootAgentsOnly?: boolean | { get?: () => unknown };
  language?: string | { get?: () => unknown };
  [key: string]: unknown;
}

export function apply(ctx: PluginContext, input: MemorydConfigInput = {}): void {
  let config: MemorydPluginConfig = resolveConfig(input as Record<string, unknown>);
  const current = () => config;
  const tokenStore = new TokenStore(current, fetch, ctx.logger);
  const client = new MemorydClient(() => ({
    endpoint: current().endpoint,
    apiKey: tokenStore.resolve(),
    requestTimeoutMs: current().requestTimeoutMs,
  }));
  const runtime = new MemorydRuntime(client, current, ctx.logger);

  ctx.provide("memorydMemory", runtime);

  ctx.effect(() => registerMemorydTools(ctx as never, client, current), "memorydMemory.tools()");

  ctx.effect(() => {
    tokenStore.start();
    runtime.start();
    return () => {
      tokenStore.dispose();
      runtime.disposeAll();
    };
  }, "memorydMemory.lifecycle()");

  ctx.on("loader/volatile-update", () => {
    config = resolveConfig(input as Record<string, unknown>);
  });

  ctx.on("agent/created", ({ agent }: never) => {
    const cfg = current();
    const a = agent as {
      session: { id?: string; header?: { origin?: string }; events?: Array<{ type?: string; data?: unknown }> };
      status?: string;
      inbox: { nextStep?: unknown };
      inject?: (message: unknown) => void;
      ctx: { effect: (fn: () => (() => void) | void, name?: string) => void };
    };
    if (cfg.rootAgentsOnly && a.session.header?.origin === "subagent") return;
    a.ctx.effect(() => () => runtime.dispose({ id: a.session.id ?? "" }), "memorydMemory.disposeSession()");
    const pending = Array.isArray(a.inbox.nextStep) ? a.inbox.nextStep : [];
    if (a.status !== "idle" || hasGuidance(a.session, pending)) return;
    a.inject?.(
      createUserMessage({
        content: [{ type: "text", text: memoryGuidance(cfg.language) }],
        source: {
          kind: PLUGIN_SOURCE,
          form: "instructions",
        },
      }),
    );
  });

  ctx.on("session/event", (session: never, event: never) => {
    const cfg = current();
    const s = session as { id: string; header?: { origin?: string } };
    if (cfg.rootAgentsOnly && s.header?.origin === "subagent") return;
    runtime.capture(s, event as never);
  });
}

export { Config };
