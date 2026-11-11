<div align="center">

# dsh-plugin-memoryd

**Self-hosted, cross-device long-term memory for LLM agents.**

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![docker](https://github.com/kingc0000/dsh-plugin-memoryd/actions/workflows/docker.yml/badge.svg)](https://github.com/kingc0000/dsh-plugin-memoryd/actions/workflows/docker.yml)
[![Docker](https://img.shields.io/badge/ghcr.io-kingc0000%2Fdsh-plugin-memoryd-2496ED?logo=docker&logoColor=white)](https://github.com/kingc0000/dsh-plugin-memoryd/pkgs/container/dsh-plugin-memoryd)
[![Docker Hub](https://img.shields.io/badge/docker.io-mejeor%2Fmemoryd-2496ED?logo=docker&logoColor=white)](https://hub.docker.com/r/mejeor/memoryd)

FastAPI · SQLite (FTS5 + jieba BM25) · Bearer device tokens · server-side LLM extraction & dedup · web admin console · [DeepSeek Harness plugin](dsh-plugin/)

[中文文档](README_ZH.md) · [Quick start](#quick-start) · [API](#api-reference) · [DSH plugin](dsh-plugin/README.md)

</div>

---

## Why

LLM agents forget. Browser-based "memory" tools keep everything client-side, so knowledge dies with the session, and every device re-learns the same facts. memoryd flips that: **memory is a small self-hosted service**, and every device — your DSH instance on this box, a teammate's on another server, a CLI tool on a laptop — shares one store with clear isolation rules.

| Problem | memoryd's answer |
|---|---|
| Knowledge dies with the session | Durable, versioned memories in SQLite |
| Each device re-learns the same facts | One shared store, cross-device reads |
| Memory fills with duplicates | Server-side LLM adjudication: ADD / UPDATE / NOOP |
| Private data leaks to other devices | Device-private scopes; writes permission-checked |
| Secrets end up in memory | Secret red line — shared scopes reject `sk-…`/keys/`password=` outright |
| Thin clients, fat duplication | Extraction & dedup live **server-side**, so every client stays thin |

## Architecture

```
DSH plugin / CLI / any client        (Bearer device token)
        │
        │  HTTP /v1/*                        ┌────────────────────────────┐
        ▼                                    │  memoryd (FastAPI)         │
┌───────────────────────────────────────┐    │                           │
│ Devices (token = identity)            │    │  /v1/memories   CRUD      │
│  • dsh-web on server A  ── writes ────►│────┼─►/v1/ingest    LLM       │
│  • teammate's DSH on B   ────────────►│    │   extract → adjudicate    │
│  • laptop CLI           ── reads ───►│    │  /v1/insights  aggregate  │
│                                       │    │  /v1/proposals approvals  │
└───────────────────────────────────────┘    │  /v1/devices   token mgmt │
                                             │  /ui           admin web  │
                                             └───────────┬───────────────┘
                                                         ▼
                                        SQLite (WAL + FTS5, jieba BM25)
```

**Key design choices**

- **Server-side adjudication.** Clients submit raw conversation text; the server extracts durable facts with an LLM and adjudicates each candidate against existing memories — paraphrased duplicates merge into a new **version** of the same memory instead of re-adding noise. All clients share one pipeline; none implement dedup themselves.
- **Read-open, write-restricted.** Any active device can read everything (cross-device knowledge is the point); writes follow a per-scope permission matrix, and unauthorized writes degrade to the caller's device zone plus a proposal for admin approval. No silent privilege escalation, ever.
- **Secret red line.** Content matching `sk-…`, private keys, or `password=` is rejected for shared scopes at the door. The device zone allows it — some facts (deploy tokens for this box) are legitimately private.
- **Insight with a floor.** A daily aggregation job condenses user-scope memories into one digest entry — but skips entirely under 5 sources, because a small library's "summary" is just duplication wearing a hat.
- **Boring storage.** One SQLite file, WAL mode, FTS5 with jieba segmentation for first-class Chinese search. Back up by copying the file. No server cluster, no vector DB.

## Scope model

Five scopes, one permission matrix, enforced server-side on every write:

| Scope | Read | Write | Use for |
|---|---|---|---|
| `user` | all devices | all devices (secret scan) | cross-device facts & preferences |
| `project:slug` | all devices | project members (`owner`/`member`) | project knowledge |
| `device` | all devices | this device + admin | paths, hosts, production details |
| `agent` | all devices | all devices | reusable task-type experience |
| `session` | all devices | session parties (origin recorded) | per-session scratchpad |

Promotion (`device` → `user`/`project`) and project membership are **admin-approved proposals**, not automatic.

## Quick start

### Docker

```bash
git clone https://github.com/kingc0000/dsh-plugin-memoryd.git && cd dsh-plugin-memoryd
export LLM_API_KEY=sk-...          # any OpenAI-compatible key
docker compose up -d               # → http://127.0.0.1:3233
```

The container needs an OpenAI-compatible chat endpoint for extraction/adjudication/aggregation. Point it at your gateway in `docker-compose.yml`:

```yaml
environment:
  MEMORYD_LLM_BASE_URL: "https://your-gateway/v1"
  MEMORYD_LLM_MODEL: "gpt-4o-mini"
  LLM_API_KEY: "${LLM_API_KEY:?set LLM_API_KEY}"
```

### Manual (systemd)

```bash
pip install -r requirements.txt
mkdir -p /var/lib/memoryd /etc/memoryd
cat > /etc/memoryd/config.env <<'EOF'
LLM_BASE_URL=https://api.example.com/v1
LLM_MODEL=gpt-4o-mini
LLM_API_KEY=sk-...
EOF
python scripts/init_db.py
uvicorn app.main:app --host 127.0.0.1 --port 3233
```

TLS reverse-proxy templates ship in `nginx-memoryd.conf.example` (Let's Encrypt) and `nginx-memoryd-selfsigned.conf.example`. **Do not expose the bare port** — put TLS in front.

### First admin, first device

```bash
# 1. create the admin device (token is shown exactly once)
python scripts/register_device.py --name admin --role admin
#    → token: Xk9-EXAMPLE... (save it; the server stores only a hash)

# 2. open the admin console
open http://127.0.0.1:3233/ui/    # paste the token to log in
#    tabs: 记忆 CRUD · 检索 · 审批 (proposals) · 设备与 Token · 审计

# 3. register each client device the same way (role=device)
#    hand the token to that device's plugin config
```

### DeepSeek Harness plugin

Full docs in [`dsh-plugin/`](dsh-plugin/README.md). The plugin registers four tools (`memory_search` / `memory_write` / `memory_promote` / `memory_status`), injects usage guidance at session start, and automatically submits completed conversation turns to `/v1/ingest`.

```yaml
# DSH profile cordis.patch.yml
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
            apiKey: <device-token>
            language: zh
```

Any other client works the same way — Bearer token + plain HTTP:

```bash
# submit a conversation batch for extraction & dedup
curl -s http://127.0.0.1:3233/v1/ingest \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"dialogue":["用户: 所有服务只能用 2000-2099 端口","助手: 已按此配置 iptables 放行"]}'
# → {"batch_id":"b-123", ...}; poll GET /v1/ingest/b-123 or watch /v1/audit

# search everything readable
curl -s http://127.0.0.1:3233/v1/memories:search \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"query":"端口策略","limit":5}'
```

## API reference

Auth: `Authorization: Bearer <device-token>` on everything except health checks and `/ui` static assets.

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| GET | `/v1/health` · `/v1/health/ready` | — | Liveness / stats |
| GET | `/v1/auth/whoami` | device | Token introspection |
| | **memories** | | |
| GET | `/v1/memories` | device | List — filters: `scope`, `kind`, `limit`, `offset`, `include_insight` (paged) |
| POST | `/v1/memories` | device | Create (scope adjudicated server-side; unauthorized scopes degrade to `device` + proposal) |
| POST | `/v1/memories:search` | device | BM25 search — `query` (1–500), `scopes?`, `limit` (default 8, max 50), `since?`, `include_insight?` (default false) |
| GET | `/v1/memories/{id}` | device | Fetch one |
| PATCH | `/v1/memories/{id}` | writer | Update — creates version+1, supersedes old row |
| DELETE | `/v1/memories/{id}` | writer | Soft delete |
| POST | `/v1/memories/{id}:promote` | writer | Promote scope (audited, version chain) |
| | **ingest & insight** | | |
| POST | `/v1/ingest` | device | Dialogue batch → LLM extraction + adjudication. `dialogue` (1–200 strings), `agent_id?` (default `root`), `session_id?`, `async_mode` (default `true`) |
| GET | `/v1/ingest/{batch_id}` | device | Batch status |
| POST | `/v1/insights/run` | admin | Run daily aggregation now (skips under 5 sources) |
| GET | `/v1/insights` | admin | List insights |
| | **governance** | | |
| GET/POST | `/v1/proposals` | device | List (admin: all; device: own) / submit `scope_upgrade` or `project_join` |
| POST | `/v1/proposals/{id}:approve` · `:reject` | admin | Decide — approve executes immediately (promote memory / add member) |
| GET/POST | `/v1/projects` | admin | Projects |
| GET/POST/DELETE | `/v1/memberships` | admin | Project membership (`owner`/`member`/`readonly`) |
| | **devices & tokens** | | |
| GET/POST | `/v1/devices` | admin | List / register — response includes the one-time token |
| POST | `/v1/devices/{id}/token` | admin | Rotate token (old one invalid immediately) |
| DELETE | `/v1/devices/{id}/token` | admin | Revoke |
| POST | `/v1/devices/{id}/activate` · `/deactivate` | admin | Enable / disable |
| | **misc** | | |
| POST | `/v1/sessions/{id}/post` · GET `/list` · DELETE | device | Session blackboard |
| GET | `/v1/events` | device | SSE change stream (15 s heartbeat) |
| GET | `/v1/audit` | admin | Audit log |
| GET | `/ui/` | — | Web admin console (token login) |

Interactive OpenAPI docs at `/docs` once running. Full request/response formats for every endpoint: [docs/API.md](docs/API.md).

## Configuration

Precedence: `MEMORYD_*` environment variables → `/etc/memoryd/config.env` → built-in defaults.

| Key | Default | Description |
|---|---|---|
| `DB_PATH` | `/var/lib/memoryd/memoryd.db` | SQLite path (WAL) |
| `LISTEN_HOST` · `LISTEN_PORT` | `127.0.0.1` · `3233` | Bind address |
| `LLM_BASE_URL` | placeholder | OpenAI-compatible `/v1/chat/completions` |
| `LLM_API_KEY` | empty | Also read from env `LLM_API_KEY` |
| `LLM_MODEL` | `gpt-4o-mini` | Any chat model |
| `AGENT_SCOPE_SHARED` | `true` | `agent`-zone experience shared across devices |
| `SECRET_REDLINE` | `true` | Reject secrets on shared-scope writes |
| `SEARCH_TOP_K` | `100` | BM25 recall cap |

## Security model

- Tokens are random 40-char secrets, stored **hashed with salt**; plaintext is returned exactly once at register/rotate; rotation revokes instantly.
- Every write is permission-checked against the scope matrix; violations return 403. Ingest degrades instead of failing: unauthorized target scope → device zone + admin proposal.
- Content is secret-scanned **before** going to the LLM.
- Default bind is `127.0.0.1`. For remote access, terminate TLS in front (templates included); each remote device is its own token, individually revocable.
- All privileged actions land in the audit log with device id, action, and timestamp.

## Operations

| Task | How |
|---|---|
| Backup | `scripts/backup.sh` (file copy + retention; cron example inside) — or just copy the `.db` while running (WAL tolerates it) |
| Restore | Stop service → replace `/var/lib/memoryd/memoryd.db` → start |
| Logs | `journalctl -u memoryd -f` (or `docker compose logs -f`) |
| Insight schedule | Daily 04:30 background thread; manual: `POST /v1/insights/run` |
| Health check | `GET /v1/health/ready` (used by the Docker `HEALTHCHECK`) |

## Development

```bash
# server
pip install -r requirements.txt
python scripts/init_db.py
uvicorn app.main:app --reload

# DSH plugin (TypeScript, strict)
cd dsh-plugin
npm install && npm run build && npm test   # 30 tests
```

## FAQ

**Does it work without an LLM key?** Search/list/CRUD/sessions all work. Ingest and insights need the LLM: batches are still accepted and recorded, but extraction fails (error recorded on the batch) — nothing is stored unextracted.

**Is Chinese supported?** First-class: FTS5 search runs jieba segmentation, and the default plugin guidance is bilingual.

**Why SQLite and not Postgres/vectors?** Memory for an agent fleet is thousands of rows, not millions. One WAL file you can `scp` beats a database cluster to babysit; BM25 over segmented text beats embedding drift for keyword recall.

**Can multiple servers share one store?** Not yet — single-node by design. Replicate the file or wait for a future sync layer.

## License

Apache-2.0
