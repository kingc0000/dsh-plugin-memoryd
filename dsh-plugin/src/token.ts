/** 设备 token 解析与自助注册：token 配置/环境变量 > tokenFile > 自动注册（持久化指纹 + 轮询审批）。 */
import fs from "node:fs";
import Path from "node:path";
import { randomBytes } from "node:crypto";
import type { MemorydPluginConfig } from "./config.js";

export interface TokenStoreLogger {
  info?: (m: string) => void;
  warn?: (m: string) => void;
}

export type TokenSource = "config" | "file" | "registered" | "none";

export class TokenStore {
  private token = "";
  private stopped = false;
  private timer?: ReturnType<typeof setTimeout>;
  private started = false;

  constructor(
    private readonly getConfig: () => MemorydPluginConfig,
    private readonly fetchImpl: typeof fetch = fetch,
    private readonly logger?: TokenStoreLogger,
  ) {}

  /** 当前可用 token（显式 apiKey 优先于自动注册获得的 token）。 */
  resolve(): string {
    const cfg = this.getConfig();
    return cfg.token || this.token;
  }

  /** 生命周期入口：尝试加载已有 token，否则自动注册。 */
  start(): void {
    if (this.started) return;
    this.started = true;
    const cfg = this.getConfig();
    this.token = this.readTokenFile();
    if (this.resolve()) {
      this.logger?.info?.(
        `memoryd token ready (${cfg.token ? "config" : "file: " + cfg.tokenFile})`,
      );
      return;
    }
    if (!cfg.autoRegister) {
      this.logger?.warn?.(
        "memoryd: no token configured and autoRegister is false — memory tools will be unauthenticated until you set token",
      );
      return;
    }
    void this.runRegistration();
  }

  dispose(): void {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
    this.timer = undefined;
    this.started = false;
  }

  private readTokenFile(): string {
    try {
      return fs.readFileSync(this.getConfig().tokenFile, "utf8").trim();
    } catch {
      return "";
    }
  }

  private writeTokenFile(token: string): void {
    const cfg = this.getConfig();
    fs.mkdirSync(Path.dirname(cfg.tokenFile), { recursive: true });
    fs.writeFileSync(cfg.tokenFile, token + "\n", { mode: 0o600 });
    try {
      fs.chmodSync(cfg.tokenFile, 0o600);
    } catch {
      /* chmod 失败不影响使用 */
    }
  }

  /** 指纹持久化：同一台设备重复注册幂等。 */
  fingerprint(): string {
    const cfg = this.getConfig();
    try {
      const existing = fs.readFileSync(cfg.fingerprintFile, "utf8").trim();
      if (existing.length >= 16) return existing;
    } catch {
      /* 首次生成 */
    }
    const fp = randomBytes(32).toString("hex");
    fs.mkdirSync(Path.dirname(cfg.fingerprintFile), { recursive: true });
    fs.writeFileSync(cfg.fingerprintFile, fp + "\n", { mode: 0o600 });
    try {
      fs.chmodSync(cfg.fingerprintFile, 0o600);
    } catch {
      /* 忽略 */
    }
    return fp;
  }

  private async runRegistration(): Promise<void> {
    const cfg = this.getConfig();
    const fp = this.fingerprint();
    const name = cfg.deviceName || "dsh-agent";
    try {
      const res = await this.fetchImpl(cfg.endpoint.replace(/\/+$/, "") + "/v1/auth/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, fingerprint: fp, note: "dsh-plugin auto-register" }),
      });
      if (res.status === 202) {
        this.logger?.info?.(
          `memoryd: registration request submitted (fingerprint ${fp.slice(0, 12)}…) — waiting for an admin to approve`,
        );
      } else if (res.status === 429) {
        this.logger?.warn?.("memoryd: registration rate-limited, will retry");
        this.schedulePoll(cfg.registrationPollMs);
        return;
      } else {
        const detail = await res.text().catch(() => "");
        this.logger?.warn?.(`memoryd: registration submit failed (HTTP ${res.status}) ${detail.slice(0, 200)}`);
        this.schedulePoll(Math.max(cfg.registrationPollMs * 4, 30000));
        return;
      }
    } catch (error) {
      this.logger?.warn?.(`memoryd: registration submit error: ${(error as Error).message}`);
      this.schedulePoll(Math.max(cfg.registrationPollMs * 4, 30000));
      return;
    }
    this.schedulePoll(cfg.registrationPollMs);
  }

  private schedulePoll(delayMs: number): void {
    if (this.stopped) return;
    this.timer = setTimeout(() => void this.pollClaim(), delayMs);
  }

  private async pollClaim(): Promise<void> {
    if (this.stopped) return;
    const cfg = this.getConfig();
    const fp = this.fingerprint();
    let res: Response;
    try {
      res = await this.fetchImpl(cfg.endpoint.replace(/\/+$/, "") + "/v1/auth/register/claim", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fingerprint: fp }),
      });
    } catch (error) {
      this.logger?.warn?.(`memoryd: claim poll error: ${(error as Error).message}`);
      this.schedulePoll(cfg.registrationPollMs);
      return;
    }
    if (res.status === 200) {
      const data = (await res.json().catch(() => ({}))) as { status?: string; token?: string };
      if (data.status === "issued" && data.token) {
        this.token = data.token;
        this.writeTokenFile(data.token);
        this.logger?.info?.("memoryd: token claimed via registration — memory tools are ready");
        return;
      }
      // pending → 继续
      this.schedulePoll(cfg.registrationPollMs);
      return;
    }
    if (res.status === 202) {
      this.schedulePoll(cfg.registrationPollMs);
      return;
    }
    if (res.status === 403) {
      this.logger?.warn?.("memoryd: registration was rejected by an admin — not retrying");
      return;
    }
    if (res.status === 410) {
      this.logger?.warn?.(
        "memoryd: token already claimed (tokenFile may exist elsewhere) — restart or rotate device token on the server",
      );
      return;
    }
    this.schedulePoll(cfg.registrationPollMs);
  }
}
