/** 指令注入文案 + 已注入检查：会话开场告诉 LLM 记忆服务怎么用（中英双语）。 */

export const PLUGIN_SOURCE = "memoryd-memory";

/** 向 DSH 的 MessageSourceMap 注册本插件自己的 source kind（merge-extensible 设计）。 */
declare module "@deepseek-ai/dsh-llm" {
  interface MessageSourceMap {
    "memoryd-memory": {
      kind: "memoryd-memory";
      form: "instructions";
    };
  }
}

const GUIDANCE = {
  zh: [
    "您已接入 memoryd 长期记忆服务（跨设备共享区 + 设备私有隔离区）。",
    "使用规则：",
    "1. 回答依赖历史事实/偏好/决策/流程时，先调用 memory_search 检索。",
    "2. 结果标注 scope：user/project=跨设备共享记忆，device=本机私有记忆，agent=同类任务经验，session=本会话黑板。",
    "3. 需要主动记录新信息时用 memory_write（默认写本机私有区；写共享区需内容跨设备通用且不含密钥）。",
    "4. 把记忆提升到共享区用 memory_promote。",
    "5. 密钥/密码/API Key 严禁写入记忆；不确定归属的一律写本机私有区。",
  ].join("\n"),
  en: [
    "You are connected to the memoryd long-term memory service (shared + device-isolated zones).",
    "Rules: 1. Call memory_search before answering questions that depend on prior facts/preferences/decisions. 2. Results are labeled with scope: user/project=shared, device=this machine private, agent=task-type experience, session=blackboard. 3. Use memory_write to persist new facts (default: device-private; shared only when clearly generic and secret-free). 4. Promote to shared with memory_promote. 5. Never write secrets into memory; when unsure, write to the device zone.",
  ].join("\n"),
} as const;

export type GuidanceLanguage = keyof typeof GUIDANCE;

export function memoryGuidance(language: string): string {
  return GUIDANCE[language as GuidanceLanguage] || GUIDANCE.zh;
}

function isGuidance(value: unknown): boolean {
  const source =
    value && typeof value === "object" ? (value as { source?: unknown }).source : undefined;
  if (!source || typeof source !== "object") return false;
  const s = source as { kind?: string; plugin?: string; form?: string };
  return (
    (s.kind === PLUGIN_SOURCE || (s.kind === "plugin" && s.plugin === PLUGIN_SOURCE)) &&
    s.form === "instructions"
  );
}

/** 会话是否已注入过 guidance（防重复注入）。 */
export function hasGuidance(
  session: { events?: Array<{ type?: string; data?: unknown }> },
  pendingMessages: Array<unknown> = [],
): boolean {
  return (
    (session.events || []).some((event) => event.type === "user/message" && isGuidance(event.data)) ||
    pendingMessages.some(isGuidance)
  );
}
