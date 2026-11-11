/** memoryd HTTP 客户端：Bearer 设备 token 鉴权，全部调用超时可控（native fetch，零依赖）。 */

export interface MemorySearchOptions {
  limit?: number;
  scopes?: string[];
  signal?: AbortSignal;
  timeoutMs?: number;
}

export interface MemoryWriteOptions {
  scope?: string;
  scope_ref?: string;
  kind?: string;
  tags?: string[];
}

export interface IngestOptions {
  agentId?: string;
  sessionId?: string;
}

export interface MemoryItem {
  id: number;
  scope: string;
  scope_ref?: string;
  content: string;
  kind?: string;
  origin?: { device_id?: number; agent_id?: string };
  updated_at?: string;
}

export interface ClientResult<T = unknown> {
  ok: boolean;
  status?: number;
  data?: T;
  error?: string;
}

export interface MemorydClientConfig {
  endpoint: string;
  apiKey: string;
  requestTimeoutMs: number;
}

export type MemorydClientConfigSource = MemorydClientConfig | (() => MemorydClientConfig);

type Fetch = typeof fetch;

async function request<T>(
  config: MemorydClientConfigSource,
  path: string,
  init: {
    method?: string;
    body?: unknown;
    timeoutMs?: number;
    signal?: AbortSignal;
  },
  fetchImpl: Fetch,
): Promise<ClientResult<T>> {
  const cfg = typeof config === "function" ? config() : config;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), init.timeoutMs ?? cfg.requestTimeoutMs);
  const onOuterAbort = () => controller.abort();
  if (init.signal) init.signal.addEventListener("abort", onOuterAbort, { once: true });
  try {
    const res = await fetchImpl(cfg.endpoint.replace(/\/+$/, "") + path, {
      method: init.method ?? "GET",
      headers: {
        "Content-Type": "application/json",
        ...(cfg.apiKey ? { Authorization: `Bearer ${cfg.apiKey}` } : {}),
      },
      body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
      signal: controller.signal,
    });
    const text = await res.text();
    let data: (T & { detail?: string; error?: string }) | null = null;
    try {
      data = text ? (JSON.parse(text) as T & { detail?: string; error?: string }) : null;
    } catch {
      data = null;
    }
    if (!res.ok) {
      return {
        ok: false,
        status: res.status,
        error: (data && (data.detail || data.error)) || text || res.statusText,
      };
    }
    return { ok: true, status: res.status, data: data ?? undefined };
  } catch (error) {
    const name = (error as { name?: string })?.name === "AbortError" ? "timeout(max)" : "error";
    return { ok: false, error: `${name} ${String((error as Error)?.message || error)}`.slice(0, 300) };
  } finally {
    clearTimeout(timer);
    if (init.signal) init.signal.removeEventListener("abort", onOuterAbort);
  }
}

export class MemorydClient {
  private readonly config: MemorydClientConfigSource;
  private readonly fetchImpl: Fetch;

  constructor(config: MemorydClientConfigSource, fetchImpl: Fetch = fetch) {
    this.config = config;
    this.fetchImpl = fetchImpl;
  }

  whoami(signal?: AbortSignal): Promise<ClientResult<{ device_id: number; name: string; role: string }>> {
    return request(this.config, "/v1/auth/whoami", { signal }, this.fetchImpl);
  }

  search(query: string, opts: MemorySearchOptions = {}): Promise<ClientResult<{ results: MemoryItem[] }>> {
    return request<{ results: MemoryItem[] }>(
      this.config,
      "/v1/memories:search",
      {
        method: "POST",
        body: { query, limit: opts.limit, scopes: opts.scopes },
        signal: opts.signal,
        timeoutMs: opts.timeoutMs,
      },
      this.fetchImpl,
    );
  }

  write(content: string, extra: MemoryWriteOptions = {}): Promise<ClientResult<{
    id: number;
    scope: string;
    scope_ref?: string;
    version?: number;
    note?: string;
  }>> {
    return request(this.config, "/v1/memories", { method: "POST", body: { content, ...extra } }, this.fetchImpl);
  }

  promote(memoryId: number, toScope: string, reason: string): Promise<ClientResult<{ memory_id?: number }>> {
    return request(
      this.config,
      `/v1/memories/${memoryId}:promote`,
      { method: "POST", body: { to_scope: toScope, reason } },
      this.fetchImpl,
    );
  }

  ingest(dialogue: string[], opts: IngestOptions = {}): Promise<ClientResult<{
    accepted: boolean;
    batch_id?: string;
    note?: string;
  }>> {
    return request(
      this.config,
      "/v1/ingest",
      {
        method: "POST",
        body: {
          dialogue,
          agent_id: opts.agentId || "root",
          session_id: opts.sessionId || null,
          async_mode: true,
        },
        timeoutMs: 60000,
      },
      this.fetchImpl,
    );
  }
}
