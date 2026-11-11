/** 配置解析：默认值 + schemastery schema + 环境变量兜底。 */
import z from "@deepseek-ai/schemastery";

export const DEFAULT_CONFIG = Object.freeze({
  endpoint: "http://127.0.0.1:3233",
  apiKey: "",
  requestTimeoutMs: 10000,
  searchLimit: 8,
  autoMemoryEnabled: true,
  autoMemoryInterval: 5,
  rootAgentsOnly: false,
  language: "zh",
});

export const Config = z.object({
  endpoint: z.string().description("memoryd HTTP service URL").default(DEFAULT_CONFIG.endpoint).volatile(),
  apiKey: z
    .string()
    .description("Device token issued by memoryd admin; env MEMORYD_API_KEY is also supported")
    .default("")
    .volatile(),
  requestTimeoutMs: z.natural().min(1000).max(120000).default(DEFAULT_CONFIG.requestTimeoutMs).volatile(),
  searchLimit: z.natural().min(1).max(50).default(DEFAULT_CONFIG.searchLimit).volatile(),
  autoMemoryEnabled: z.boolean().default(DEFAULT_CONFIG.autoMemoryEnabled).volatile(),
  autoMemoryInterval: z.natural().min(1).max(1000).default(DEFAULT_CONFIG.autoMemoryInterval).volatile(),
  rootAgentsOnly: z.boolean().default(DEFAULT_CONFIG.rootAgentsOnly).volatile(),
  language: z.union(["en", "zh"]).default(DEFAULT_CONFIG.language).volatile(),
});

export interface MemorydPluginConfig {
  endpoint: string;
  apiKey: string;
  requestTimeoutMs: number;
  searchLimit: number;
  autoMemoryEnabled: boolean;
  autoMemoryInterval: number;
  rootAgentsOnly: boolean;
  language: "zh" | "en";
}

interface ConfigLike {
  get?: () => unknown;
}

function flattenInput(input: Record<string, unknown> = {}): Record<string, unknown> {
  const entries = Object.entries(input).map(([key, value]) => {
    const resolved =
      value !== null && typeof value === "object" && typeof (value as ConfigLike).get === "function"
        ? (value as ConfigLike).get!()
        : value;
    return [key, resolved];
  });
  return Object.fromEntries(entries);
}

export function resolveConfig(
  input: Record<string, unknown> = {},
  env: Record<string, string | undefined> = process.env,
): MemorydPluginConfig {
  const flat = flattenInput(input);
  const unknownKeys = Object.keys(flat).filter((key) => !(key in DEFAULT_CONFIG));
  if (unknownKeys.length) {
    throw new TypeError(`Unknown memoryd config option: ${unknownKeys.join(", ")}`);
  }
  const config = {
    ...DEFAULT_CONFIG,
    ...flat,
    apiKey: (flat.apiKey as string) || env.MEMORYD_API_KEY || "",
  };
  return config as MemorydPluginConfig;
}

export type { z };
