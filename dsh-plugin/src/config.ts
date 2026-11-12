/** 配置解析：默认值 + schemastery schema + 环境变量兜底。 */
import os from "node:os";
import Path from "node:path";
import z from "@deepseek-ai/schemastery";

export const DEFAULT_CONFIG = Object.freeze({
  endpoint: "http://127.0.0.1:3233",
  token: "",
  apiKey: "",
  requestTimeoutMs: 10000,
  searchLimit: 8,
  autoMemoryEnabled: true,
  autoMemoryInterval: 5,
  rootAgentsOnly: false,
  language: "zh",
  autoRegister: true,
  deviceName: "",
  tokenFile: "",
  fingerprintFile: "",
  registrationPollMs: 5000,
});

export const Config = z.object({
  endpoint: z.string().description("memoryd HTTP service URL").default(DEFAULT_CONFIG.endpoint).volatile(),
  token: z
    .string()
    .description("memoryd device token (Bearer credential); env MEMORYD_TOKEN is also supported. Omit to self-register")
    .default("")
    .volatile(),
  apiKey: z
    .string()
    .description("DEPRECATED alias of token, kept for backward compatibility")
    .default("")
    .volatile(),
  requestTimeoutMs: z.natural().min(1000).max(120000).default(DEFAULT_CONFIG.requestTimeoutMs).volatile(),
  searchLimit: z.natural().min(1).max(50).default(DEFAULT_CONFIG.searchLimit).volatile(),
  autoMemoryEnabled: z.boolean().default(DEFAULT_CONFIG.autoMemoryEnabled).volatile(),
  autoMemoryInterval: z.natural().min(1).max(1000).default(DEFAULT_CONFIG.autoMemoryInterval).volatile(),
  rootAgentsOnly: z.boolean().default(DEFAULT_CONFIG.rootAgentsOnly).volatile(),
  language: z.union(["en", "zh"]).default(DEFAULT_CONFIG.language).volatile(),
  autoRegister: z
    .boolean()
    .description("When no token is configured, self-register with a persistent fingerprint and poll for approval")
    .default(DEFAULT_CONFIG.autoRegister)
    .volatile(),
  deviceName: z
    .string()
    .description("Device name used in self-registration; defaults to this hostname")
    .default(DEFAULT_CONFIG.deviceName)
    .volatile(),
  tokenFile: z
    .string()
    .description("File to persist the claimed token (default ~/.memoryd/token)")
    .default(DEFAULT_CONFIG.tokenFile)
    .volatile(),
  fingerprintFile: z
    .string()
    .description("File to persist the registration fingerprint (default ~/.memoryd/fingerprint)")
    .default(DEFAULT_CONFIG.fingerprintFile)
    .volatile(),
  registrationPollMs: z
    .natural()
    .min(1000)
    .max(600000)
    .description("Poll interval while waiting for an admin to approve the registration")
    .default(DEFAULT_CONFIG.registrationPollMs)
    .volatile(),
});

export interface MemorydPluginConfig {
  endpoint: string;
  token: string;
  apiKey: string;
  requestTimeoutMs: number;
  searchLimit: number;
  autoMemoryEnabled: boolean;
  autoMemoryInterval: number;
  rootAgentsOnly: boolean;
  language: "zh" | "en";
  autoRegister: boolean;
  deviceName: string;
  tokenFile: string;
  fingerprintFile: string;
  registrationPollMs: number;
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
  const token =
    (flat.token as string) ||
    (flat.apiKey as string) ||
    env.MEMORYD_TOKEN ||
    env.MEMORYD_API_KEY ||
    "";
  const config = {
    ...DEFAULT_CONFIG,
    ...flat,
    token,
    apiKey: token, // 兼容旧读法
    deviceName: (flat.deviceName as string) || defaultDeviceName(),
    tokenFile: expandHome((flat.tokenFile as string) || defaultUnder(".memoryd", "token")),
    fingerprintFile: expandHome(
      (flat.fingerprintFile as string) || defaultUnder(".memoryd", "fingerprint"),
    ),
  };
  return config as MemorydPluginConfig;
}

function defaultDeviceName(): string {
  try {
    return os.hostname();
  } catch {
    return "dsh-agent";
  }
}

function defaultUnder(dir: string, file: string): string {
  return Path.join(os.homedir(), dir, file);
}

function expandHome(p: string): string {
  if (p === "~") return os.homedir();
  if (p.startsWith("~/")) return Path.join(os.homedir(), p.slice(2));
  return p;
}

export type { z };
