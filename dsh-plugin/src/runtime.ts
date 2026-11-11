/** 自动捕获运行时：完成的对话轮次攒够 autoMemoryInterval 后批量提交 memoryd /v1/ingest。 */
import type { MemorydClient } from "./client.js";
import type { MemorydPluginConfig } from "./config.js";

export interface LoggerLike {
  info?: (message: string) => void;
  warn?: (message: string) => void;
}

interface MessageLike {
  content?: string | Array<{ type?: string; text?: string }>;
  source?: { kind?: string };
}

export interface SessionEvent {
  type: string;
  data?: unknown;
}

export interface DshSession {
  id: string;
  header?: { origin?: string };
}

export interface CapturedMessage {
  role: "user" | "assistant";
  text: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function messageText(message: MessageLike): string {
  if (typeof message.content === "string") return message.content.trim();
  if (!Array.isArray(message.content)) return "";
  return message.content
    .filter((part) => isRecord(part) && part.type === "text" && typeof part.text === "string")
    .map((part) => (part as { text: string }).text.trim())
    .filter(Boolean)
    .join("\n\n")
    .trim();
}

function eventMessage(event: SessionEvent): MessageLike | null {
  if (event.type === "user/message") return isRecord(event.data) ? (event.data as MessageLike) : null;
  if (event.type === "assistant/message" && isRecord(event.data)) {
    const data = event.data as { message?: unknown };
    return isRecord(data.message) ? (data.message as MessageLike) : null;
  }
  return null;
}

/** 会话事件 → 待捕获消息；插件自身注入的消息返回 null（防自我复读）。 */
export function captureMessage(event: SessionEvent): CapturedMessage | null {
  const message = eventMessage(event);
  if (!message || message.source?.kind === "plugin") return null;
  if (event.type === "user/message" && message.source?.kind !== "user") return null;
  const role = event.type === "assistant/message" ? "assistant" : "user";
  const text = messageText(message);
  if (!text) return null;
  return { role, text };
}

interface SessionState {
  activeMessages: CapturedMessage[];
  pendingTurns: number;
  ingesting: boolean;
  lastDialogue: string[] | null;
}

export class MemorydRuntime {
  readonly client: MemorydClient;
  private readonly configSource: () => MemorydPluginConfig;
  private readonly logger?: LoggerLike;
  private readonly sessions = new Map<string, SessionState>();

  constructor(client: MemorydClient, config: (() => MemorydPluginConfig) | MemorydPluginConfig, logger?: LoggerLike) {
    this.client = client;
    this.configSource = typeof config === "function" ? config : () => config;
    this.logger = logger;
  }

  start(): void {
    const cfg = this.configSource();
    if (!cfg.autoMemoryEnabled) return;
    this.logger?.info?.("memoryd runtime started (auto memory enabled)");
  }

  private state(sessionId: string): SessionState {
    let state = this.sessions.get(sessionId);
    if (!state) {
      const fresh: SessionState = {
        activeMessages: [],
        pendingTurns: 0,
        ingesting: false,
        lastDialogue: null,
      };
      this.sessions.set(sessionId, fresh);
      return fresh;
    }
    return state;
  }

  capture(session: DshSession, event: SessionEvent): void {
    const cfg = this.configSource();
    if (!cfg.autoMemoryEnabled) return;
    const state = this.state(session.id);
    if (event.type === "turn/start") {
      state.activeMessages = [];
      return;
    }
    const message = captureMessage(event);
    if (message) state.activeMessages.push(message);
    if (event.type !== "turn/end") return;
    const hasUser = state.activeMessages.some((m) => m.role === "user");
    const hasAssistant = state.activeMessages.some((m) => m.role === "assistant");
    if (hasUser && hasAssistant) {
      state.pendingTurns += 1;
      state.lastDialogue = state.activeMessages.map((m) => `${m.role === "user" ? "用户" : "助手"}: ${m.text}`);
    }
    if (state.pendingTurns >= cfg.autoMemoryInterval) this.flush(session.id, state);
  }

  private flush(sessionId: string, state: SessionState): void {
    const dialogue = state.lastDialogue;
    state.pendingTurns = 0;
    state.lastDialogue = null;
    if (!dialogue || !dialogue.length) return;
    state.ingesting = true;
    void this.client
      .ingest(dialogue, { agentId: "root", sessionId })
      .then((result) => {
        if (!result.ok) {
          this.logger?.warn?.(`memoryd ingest failed: ${result.error || "unknown error"}`);
        } else {
          this.logger?.info?.(`memoryd ingest accepted: ${result.data?.batch_id || ""}`);
        }
      })
      .catch(() => {})
      .finally(() => {
        state.ingesting = false;
      });
  }

  dispose(session: Pick<DshSession, "id">): void {
    const state = this.sessions.get(session.id);
    if (state && state.lastDialogue) {
      this.flush(session.id, state);
    }
    this.sessions.delete(session.id);
  }

  disposeAll(): void {
    for (const [id] of this.sessions) {
      this.dispose({ id });
    }
  }
}
