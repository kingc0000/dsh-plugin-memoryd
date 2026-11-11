<div align="center">

# memoryd

**自托管、跨设备的 LLM Agent 长期记忆服务。**

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![docker](https://github.com/kingc0000/memoryd/actions/workflows/docker.yml/badge.svg)](https://github.com/kingc0000/memoryd/actions/workflows/docker.yml)
[![Docker](https://img.shields.io/badge/ghcr.io-kingc0000%2Fmemoryd-2496ED?logo=docker&logoColor=white)](https://github.com/kingc0000/memoryd/pkgs/container/memoryd)
[![Docker Hub](https://img.shields.io/badge/docker.io-mejeor%2Fmemoryd-2496ED?logo=docker&logoColor=white)](https://hub.docker.com/r/mejeor/memoryd)

FastAPI · SQLite（FTS5 + jieba BM25）· Bearer 设备 token · 服务端 LLM 提取与去重 · Web 管理台 · [DeepSeek Harness 插件](dsh-plugin/README_ZH.md)

[English](README.md) · [快速开始](#快速开始) · [API](#api-参考) · [DSH 插件](dsh-plugin/README_ZH.md)

</div>

---

## 为什么需要它

LLM Agent 会忘事。浏览器侧的「记忆」工具把一切留在客户端——知识随会话一起死亡，每台设备都在重新学同样的事实。memoryd 反过来做：**记忆是一个自托管小服务**，所有设备——这台服务器上的 DSH、同事机器上的 DSH、笔记本上的 CLI——在同一个库里共享知识，且隔离规则清晰。

| 问题 | memoryd 的答案 |
|---|---|
| 知识随会话消失 | SQLite 持久化、版本化记忆 |
| 每台设备重复学习同样的事实 | 一个共享库，跨设备读取 |
| 记忆里堆满重复 | 服务端 LLM 裁决：ADD / UPDATE / NOOP |
| 私有数据漏到别的设备 | 设备私有 scope；写入按权限矩阵强制 |
| 密钥混进记忆 | 密钥红线——共享区直接拒绝 `sk-…`/私钥/`password=` |
| 客户端薄、重复逻辑厚 | 提取与去重都在**服务端**，所有客户端保持薄 |

## 架构

```
DSH 插件 / CLI / 任意客户端          （Bearer 设备 token）
        │
        │  HTTP /v1/*                        ┌────────────────────────────┐
        ▼                                    │  memoryd (FastAPI)         │
┌───────────────────────────────────────┐    │                            │
│ 设备（token 即身份）                   │    │  /v1/memories   增删改查   │
│  • 服务器 A 的 dsh-web  ── 写入 ────► │────┼─►/v1/ingest    LLM        │
│  • 同事 B 的 DSH       ────────────►│    │   提取 → 裁决              │
│  • 笔记本 CLI          ── 读取 ───►│    │  /v1/insights  日度聚合     │
│                                       │    │  /v1/proposals 审批        │
└───────────────────────────────────────┘    │  /v1/devices   token 管理  │
                                             │  /ui           Web 管理台   │
                                             └───────────┬────────────────┘
                                                         ▼
                                        SQLite（WAL + FTS5，jieba BM25）
```

**关键设计**

- **服务端裁决。** 客户端只提交原始对话文本；服务端用 LLM 提取持久事实，并把每条候选与已有记忆比对裁决——换措辞的重复会合并成同一记忆的**新版本**，而不是新增噪音。所有客户端共用一条管线，谁都不用自己实现去重。
- **读全开、写受限。** 任何活跃设备可读全部（跨设备共享知识本来就是目的）；写入按 scope 权限矩阵强制，无权写入自动降级到本机 device 区并生成 admin 审批申请。永远不存在悄悄越权。
- **密钥红线。** 内容含 `sk-…`/私钥/`password=` 时，共享区直接拒之门外；device 区允许——有些事实（本机的部署 token）本来就是私有的。
- **聚合有下限。** 每日聚合任务把 user 区记忆压缩成一条摘要——但源不足 5 条时直接跳过：小库的「摘要」只是戴着帽子的重复。
- **存储无聊即正确。** 一个 SQLite 文件、WAL 模式、FTS5 + jieba 中文分词检索。备份=复制文件。没有数据库集群要伺候。

## Scope 模型

五个 scope、一张权限矩阵，每次写入服务端强制：

| Scope | 读 | 写 | 适合放 |
|---|---|---|---|
| `user` | 所有设备 | 所有设备（密钥红线）| 跨设备事实与偏好 |
| `project:slug` | 所有设备 | 项目成员（`owner`/`member`）| 项目知识 |
| `device` | 所有设备 | 本机 + admin | 路径、主机、生产细节 |
| `agent` | 所有设备 | 所有设备 | 可复用的同类任务经验 |
| `session` | 所有设备 | 会话相关方（记来源）| 会话黑板 |

提升（`device` → `user`/`project`）和项目成员加入都是 **admin 审批的申请**，绝不自动生效。

## 快速开始

### Docker

```bash
git clone https://github.com/kingc0000/memoryd.git && cd memoryd
export LLM_API_KEY=sk-...          # 任意 OpenAI 兼容 key
docker compose up -d               # → http://127.0.0.1:3233
```

容器需要一个 OpenAI 兼容的 chat 端点（提取/裁决/聚合用）。在 `docker-compose.yml` 里指向你的网关：

```yaml
environment:
  MEMORYD_LLM_BASE_URL: "https://your-gateway/v1"
  MEMORYD_LLM_MODEL: "gpt-4o-mini"
  LLM_API_KEY: "${LLM_API_KEY:?请设置 LLM_API_KEY}"
```

### 手动部署（systemd）

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

TLS 反代模板随仓库附带：`nginx-memoryd.conf.example`（Let's Encrypt）与 `nginx-memoryd-selfsigned.conf.example`（自签）。**不要裸暴露端口**——前面加 TLS。

### 首个管理员、首台设备

```bash
# 1. 注册 admin 设备（token 只显示这一次）
python scripts/register_device.py --name admin --role admin
#    → token: Xk9-EXAMPLE...（保存好；服务端只存哈希）

# 2. 打开管理台
open http://127.0.0.1:3233/ui/    # 粘贴 token 登录
#    标签页：记忆 CRUD · 检索 · 审批 · 设备与 Token · 审计

# 3. 每台客户端设备同样方式注册（role=device）
#    把 token 填进该设备的插件配置
```

### DeepSeek Harness 插件

完整文档见 [`dsh-plugin/README_ZH.md`](dsh-plugin/README_ZH.md)。插件注册四个工具（`memory_search` / `memory_write` / `memory_promote` / `memory_status`）、会话开场注入使用规则，并自动把完成的对话轮次提交 `/v1/ingest`。

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
          name: "@agentscope-ai/memoryd-dsh-plugin"
          config:
            endpoint: http://127.0.0.1:3233
            apiKey: <设备token>
            language: zh
```

其他客户端一样接：Bearer token + 纯 HTTP：

```bash
# 提交对话批次（服务端提取 + 去重）
curl -s http://127.0.0.1:3233/v1/ingest \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"dialogue":["用户: 所有服务只能用 2000-2099 端口","助手: 已按此配置 iptables 放行"]}'
# → {"batch_id":"b-123",...}；轮询 GET /v1/ingest/b-123 或看 /v1/audit

# 检索全部可读记忆
curl -s http://127.0.0.1:3233/v1/memories:search \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"query":"端口策略","limit":5}'
```

## API 参考

鉴权：除健康检查与 `/ui` 静态资源外，一律 `Authorization: Bearer <device-token>`。

| 方法 | 端点 | 权限 | 说明 |
|---|---|---|---|
| GET | `/v1/health` · `/v1/health/ready` | 无 | 存活 / 统计 |
| GET | `/v1/auth/whoami` | 设备 | token 自省 |
| | **记忆** | | |
| GET | `/v1/memories` | 设备 | 列表——过滤：`scope`、`kind`、`limit`、`include_insight` |
| POST | `/v1/memories` | 设备 | 新增（scope 服务端裁决；无权目标自动降级 `device` 并生成审批申请）|
| POST | `/v1/memories:search` | 设备 | BM25 检索——`query`（1–500）、`scopes?`、`limit`（默认 8，最大 50）、`since?`、`include_insight?`（默认 false）|
| GET | `/v1/memories/{id}` | 设备 | 单条 |
| PATCH | `/v1/memories/{id}` | 写权 | 更新——产生 version+1，旧行 superseded |
| DELETE | `/v1/memories/{id}` | 写权 | 软删除 |
| POST | `/v1/memories/{id}:promote` | 写权 | 提升 scope（审计、版本链）|
| | **提取与聚合** | | |
| POST | `/v1/ingest` | 设备 | 对话批次 → LLM 提取+裁决。`dialogue`（1–200 条）、`agent_id?`（默认 `root`）、`session_id?`、`async_mode`（默认 `true`）|
| GET | `/v1/ingest/{batch_id}` | 设备 | 批次状态 |
| POST | `/v1/insights/run` | admin | 立即跑日度聚合（源 <5 条跳过）|
| GET | `/v1/insights` | admin | 聚合条目列表 |
| | **治理** | | |
| GET/POST | `/v1/proposals` | 设备 | 列表（admin 全部；设备仅自己）/ 提交 `scope_upgrade` 或 `project_join` 申请 |
| POST | `/v1/proposals/{id}:approve` · `:reject` | admin | 审批——approve 立即执行（升级记忆 / 加成员）|
| GET/POST | `/v1/projects` | admin | 项目 |
| GET/POST/DELETE | `/v1/memberships` | admin | 项目成员（`owner`/`member`/`readonly`）|
| | **设备与 token** | | |
| GET/POST | `/v1/devices` | admin | 列表 / 注册——响应含一次性 token |
| POST | `/v1/devices/{id}/token` | admin | 轮换（旧 token 立即失效）|
| DELETE | `/v1/devices/{id}/token` | admin | 吊销 |
| POST | `/v1/devices/{id}/activate` · `/deactivate` | admin | 停用 / 恢复 |
| | **其他** | | |
| POST | `/v1/sessions/{id}/post` · GET `/list` · DELETE | 设备 | 会话黑板 |
| GET | `/v1/events` | 设备 | SSE 变更流（15 秒心跳）|
| GET | `/v1/audit` | admin | 审计流水 |
| GET | `/ui/` | 无 | Web 管理台（token 登录）|

跑起来后 `/docs` 有交互式 OpenAPI 文档。每个端点的完整请求/响应格式见 [docs/API.md](docs/API.md)。

## 配置

优先级：`MEMORYD_*` 环境变量 → `/etc/memoryd/config.env` → 内置默认。

| 键 | 默认 | 说明 |
|---|---|---|
| `DB_PATH` | `/var/lib/memoryd/memoryd.db` | SQLite 路径（WAL）|
| `LISTEN_HOST` · `LISTEN_PORT` | `127.0.0.1` · `3233` | 监听地址 |
| `LLM_BASE_URL` | 占位 | OpenAI 兼容 `/v1/chat/completions` |
| `LLM_API_KEY` | 空 | 亦可读环境变量 `LLM_API_KEY` |
| `LLM_MODEL` | `gpt-4o-mini` | 任意 chat 模型 |
| `AGENT_SCOPE_SHARED` | `true` | `agent` 区经验跨设备共享 |
| `SECRET_REDLINE` | `true` | 共享区写入密钥红线 |
| `SEARCH_TOP_K` | `100` | BM25 召回上限 |

## 安全模型

- Token 为 40 字符随机串，**加盐哈希存储**；明文只在注册/轮换时返回一次；轮换后旧 token 立即失效。
- 每次写入按 scope 权限矩阵校验，越权 403。ingest 不报错而是降级：无权目标 scope → device 区 + admin 审批申请。
- 内容**先**密钥红线扫描**再**送 LLM。
- 默认只绑 `127.0.0.1`。远程访问请前置 TLS（模板附带）；每台远程设备独立 token，可单独吊销。
- 所有特权操作落审计日志（设备 id、动作、时间）。

## 运维

| 任务 | 方法 |
|---|---|
| 备份 | `scripts/backup.sh`（文件复制+保留策略；cron 示例在脚本头）——或运行中直接复制 `.db`（WAL 允许）|
| 恢复 | 停服务 → 覆盖 `/var/lib/memoryd/memoryd.db` → 起服务 |
| 日志 | `journalctl -u memoryd -f`（或 `docker compose logs -f`）|
| 聚合调度 | 每日 04:30 后台线程；手动：`POST /v1/insights/run` |
| 健康检查 | `GET /v1/health/ready`（Docker `HEALTHCHECK` 同款）|

## 开发

```bash
# 服务端
pip install -r requirements.txt
python scripts/init_db.py
uvicorn app.main:app --reload

# DSH 插件（TypeScript，strict）
cd dsh-plugin
npm install && npm run build && npm test   # 30 项测试
```

## FAQ

**没有 LLM key 能用吗？** 检索/列表/CRUD/会话黑板都能用。ingest 与 insights 依赖 LLM：批次仍会被受理并记录，但提取失败（错误记在批次上）——绝不把未提取的原文入库。

**中文支持如何？** 一等公民：FTS5 检索走 jieba 分词，插件默认指令注入也是双语。

**为什么用 SQLite 而不是 Postgres/向量库？** Agent 集群的记忆是几千行，不是几百万行。一个能 `scp` 的 WAL 文件胜过要伺候的数据库集群；关键词召回场景下，分词后的 BM25 也胜过会漂移的 embedding。

**多服务器能共享一个库吗？** 暂不能——单节点是设计取向。复制文件，或等未来的同步层。

## 许可证

Apache-2.0
