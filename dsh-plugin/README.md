# memoryd plugin guide for DeepSeek Harness

[中文说明](./README_ZH.md)

This guide explains how to install, configure, and use `dsh-plugin-memoryd` with DeepSeek Harness (DSH): guidance injection, the four memory tools, automatic conversation capture, and the scope model (shared vs. device-isolated zones).

## 1. How the plugin works

The plugin gives a DSH session durable, cross-device memory backed by a self-hosted [memoryd](../README.md) service.

```text
New session
  └─ Inject memory-usage guidance (zh/en)
       └─ Agent calls memory_search before answering history-dependent questions
            └─ Results labeled with scope: user / project / device / agent / session

Completed conversation
  └─ After N complete turns → POST /v1/ingest (server-side LLM extraction + adjudication)
       └─ Duplicate/paraphrase facts are merged (UPDATE), not re-added
```

Three cooperating layers:

| Layer | What it does |
|---|---|
| Guidance injection | At session start, tells the agent when and how to use memory tools |
| Manual tools | `memory_search` / `memory_write` / `memory_promote` / `memory_status` — the agent calls them explicitly |
| Auto capture | A per-session state machine batches the most recent complete turn after every `autoMemoryInterval` (default 5) turns and submits it to `/v1/ingest` for server-side extraction |

Extraction and dedup live on the **server**, so every client (not just DSH) shares one adjudication pipeline. The plugin stays a thin client.

## 2. Requirements

- A running memoryd service (self-hosted; see the memoryd repo for deployment).
- DeepSeek Harness `0.1.7-rc.2`, `0.2.0-rc.2`, `0.2.1-alpha.2`, or `0.2.1-alpha.4`.
- Node.js `^22.19.0` or `>=24.0.0`, matching the current DSH engine range.
- A memoryd device token. Two ways to get one:
  - self-registration (recommended): `python3 scripts/register_device.py --name <device-name> --endpoint <service-url> --wait` — the agent submits a request with a persistent fingerprint, an admin approves it in the console, and the one-time token is claimed automatically;
  - or issued by the admin (`POST /v1/devices`).
  The token identifies this DSH instance as one device; memoryd isolates device-private memories by device id.

## 3. Install

Add the plugin to your DSH profile:

```yaml
# cordis.patch.yml
- insert:
    - id: memoryd-memory-scope
      name: "@deepseek-ai/cordis-plugin-group"
      group: true
      isolate:
        memorydMemory: true
      config:
        - id: memoryd-memory
          name: "dsh-plugin-memoryd"
          config:
            endpoint: http://127.0.0.1:3233
            # apiKey is optional: when omitted the plugin self-registers
            # (persistent fingerprint) and claims the one-time token automatically
            # once an admin approves; it persists to ~/.memoryd/token
            apiKey: <device-token>   # explicit token takes priority; or env MEMORYD_API_KEY
            language: zh
            searchLimit: 8
```

Restart DSH. Sessions should now see the memory guidance and the four tools.

## 4. Configuration

| Option | Default | Description |
|---|---|---|
| `endpoint` | `http://127.0.0.1:3233` | memoryd HTTP service URL |
| `apiKey` | `""` | Device token; env `MEMORYD_API_KEY` as fallback; if still unset, self-registration kicks in |
| `autoRegister` | `true` | Self-register (fingerprint + approval polling) when no token is configured |
| `deviceName` | hostname | Device name used for self-registration |
| `tokenFile` | `~/.memoryd/token` | Where the claimed token is persisted (mode 600) |
| `fingerprintFile` | `~/.memoryd/fingerprint` | Where the registration fingerprint is persisted |
| `registrationPollMs` | `5000` | Poll interval while awaiting approval (ms) |
| `requestTimeoutMs` | `10000` | Per-request timeout (ingest uses 60 s) |
| `searchLimit` | `8` | Default result count for `memory_search` |
| `autoMemoryEnabled` | `true` | Enable automatic conversation capture |
| `autoMemoryInterval` | `5` | Submit one completed turn after every N complete turns |
| `rootAgentsOnly` | `false` | Only capture root sessions, skip subagent sessions |
| `language` | `zh` | Guidance language, `zh` or `en` |

Unknown config keys fail fast with a `TypeError`.

## 5. Tools

| Tool | Purpose |
|---|---|
| `memory_search` | Keyword search across readable memories (BM25 + jieba segmentation). Results include scope labels and origins. |
| `memory_write` | Persist one memory. Defaults to the **device-private** zone; shared zones require generic, secret-free content. |
| `memory_promote` | Promote a private memory to a shared zone (audited, with reason). |
| `memory_status` | Connection and device identity check. |

## 6. The scope model

| Scope | Read | Write | Use for |
|---|---|---|---|
| `user` | all devices | all devices (secret scan) | Cross-device facts and preferences |
| `project:slug` | all devices | project members | Project knowledge |
| `device` | all devices | this device (+admin) | Paths, hosts, production details, anything unsure |
| `agent` | all devices | all devices | Reusable task-type experience |
| `session` | all devices | any (origin recorded) | Per-session scratchpad |

Secrets (`sk-…`, private keys, `password=`) are rejected when writing to shared zones; device zones allow them. When unsure, write to the device zone.

If the agent writes to a scope it lacks permission for, memoryd degrades the write to the device zone and files a proposal for admin approval (`POST /v1/proposals`) — no cross-scope privilege escalation happens automatically.

## 7. Privacy notes

- Only message text is captured (text parts only); plugin-injected messages are skipped to prevent self-repetition.
- Automatic capture submits the **most recent complete turn** per flush, not the whole history.
- `rootAgentsOnly: true` excludes subagent sessions entirely.
- Auto capture is fire-and-forget: memoryd downtime never blocks the conversation.

## 8. Troubleshooting

| Symptom | Check |
|---|---|
| `memoryd 不可用或未配置 token` | `apiKey` missing / wrong; call `memory_status` |
| Search returns nothing | Token revoked, or no matching memories; try broader keywords |
| Auto capture silent | `autoMemoryEnabled` off, or fewer than `autoMemoryInterval` complete turns (a turn needs both user and assistant messages) |

## License

Apache-2.0
