# API 详细文档

memoryd 的全部 HTTP 接口、请求/响应格式、错误码。[English overview](../README.md#api-reference) · [中文 README](../README_ZH.md#api-参考)

- **Base URL**：`http://127.0.0.1:3233`（默认；远程请前置 TLS 反代）
- **鉴权**：除健康检查与 `/ui` 静态资源外一律 `Authorization: Bearer <device-token>`
- **内容类型**：请求/响应均为 `application/json`（`/v1/events` 为 SSE）
- **时间格式**：`YYYY-MM-DD HH:MM:SS`（服务端本地时区）
- 交互式 OpenAPI：跑起来后访问 `/docs`

## 错误格式

所有错误统一 `{"detail": "<中文描述>"}`：

| 状态码 | 含义 |
|---|---|
| 400 | 请求不合法（参数越界、密钥红线、目标 scope 不存在等）|
| 401 | 缺少或无效的 Bearer token |
| 403 | 设备存在但无权限（写权、admin 操作、读限制）|
| 404 | 资源不存在（含软删除/版本链末端）|
| 409 | 冲突（设备名重复、重复审批、token 已吊销等）|

---

## 健康检查

### `GET /v1/health`

无鉴权。服务版本信息。

```json
{"ok": true, "service": "memoryd", "version": "0.2.0"}
```

### `GET /v1/health/ready`

无鉴权。数据库连通 + 统计（Docker `HEALTHCHECK` 同款）。

```json
{"ok": true, "devices": 3, "memories": 5}
```

`memories` = 活跃记忆数（未软删除且未被版本链取代）。

## 认证

### `GET /v1/auth/whoami`

任何有效 token。返回当前设备身份。

```json
{"device_id": 1, "name": "admin", "role": "admin"}
```

`role` ∈ `admin` | `device`。

---

## 记忆 CRUD

### 记忆对象

所有记忆端点返回的对象结构（`_row_to_dict` 输出）：

```json
{
  "id": 102,
  "scope": "user",
  "scope_ref": "",
  "content": "本机所有对外服务只能通过 2000-2099 端口段暴露",
  "kind": "fact",
  "tags": [],
  "origin": {"device_id": 1, "agent_id": "root", "session_id": null},
  "source_ref": null,
  "confidence": 0.6,
  "valid_until": null,
  "version": 1,
  "superseded_by": null,
  "created_at": "2026-11-10 21:13:00",
  "updated_at": "2026-11-10 21:13:00",
  "deleted_at": null
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int | 全局自增 id（版本链中每次更新产生新 id）|
| `scope` | string | `user` \| `project` \| `device` \| `agent` \| `session` |
| `scope_ref` | string | `project` 的 slug；`device` 为设备 id；其他为空 |
| `content` | string | 记忆正文（≤ 20000 字符）|
| `kind` | string | `fact` \| `preference` \| `procedure` \| `observation` \| `insight` |
| `tags` | string[] | 标签数组 |
| `origin` | object | `{device_id, agent_id, session_id}` 写入来源 |
| `source_ref` | string \| null | 来源引用（insight 存源 id 集合 JSON）|
| `confidence` | float | 0.0–1.0 |
| `valid_until` | string \| null | 失效时间 |
| `version` | int | 版本号（同一逻辑记忆的第几版）|
| `superseded_by` | int \| null | 被哪个新版本取代（非空=历史版本）|
| `created_at` / `updated_at` | string | 时间戳 |
| `deleted_at` | string \| null | 软删除时间 |

### `GET /v1/memories`

权限：所有活跃设备。列出可读记忆（读全开）。

| Query 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `scope` | string | — | 按单一 scope 过滤（如 `user`、`device`）|
| `kind` | string | — | 按 kind 过滤 |
| `limit` | int | 50 | 每页条数（1–500）|
| `offset` | int | 0 | 分页偏移 |
| `include_insight` | bool | false | 是否包含 `kind=insight` 聚合条目 |

```bash
curl -s "http://127.0.0.1:3233/v1/memories?scope=user&limit=10" \
  -H "Authorization: Bearer $TOKEN"
```

```json
{
  "results": [ { /* 记忆对象 */ }, ... ],
  "total": 5,
  "limit": 10,
  "offset": 0
}
```

### `POST /v1/memories`

权限：所有活跃设备。新增记忆。服务端强制 scope 写权限与密钥红线：

- 无权写目标 scope → **403** `{"detail": "无权写入 user/"}`（直接拒绝，不降级）
- 内容含密钥写共享区 → **400** `{"detail": "内容疑似含密钥/凭据，禁止写入共享区（user/project/agent/session）。如需保存，请显式使用 device 私有区。"}`

> 自动降级 + 生成审批申请的行为只在 **`/v1/ingest`**（LLM 自动判定的写入），手动 `POST /v1/memories` 由客户端负责选对 scope。

**请求体**

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `content` | string | ✓ | — | 正文（1–20000）|
| `scope` | string | | `device` | 目标 scope |
| `scope_ref` | string | | `""` | `project` 时为 slug |
| `kind` | string | | `fact` | kind |
| `tags` | string[] | | `[]` | 标签 |
| `source_ref` | string | | null | 来源引用 |
| `confidence` | float | | 0.5 | 0.0–1.0 |
| `agent_id` | string | | `root` | 来源 agent |
| `session_id` | string | | null | 来源会话 |

```bash
curl -s http://127.0.0.1:3233/v1/memories \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"content":"本服务只走 2000-2099 端口","scope":"user","kind":"fact"}'
```

```json
{
  "id": 211,
  "scope": "user",
  "scope_ref": "",
  "version": 1,
  "accepted": true,
  "note": ""
}
```

无权写目标 scope 403：

```json
{"detail": "无权写入 project/etuangou"}
```

含密钥红线 400：

```json
{"detail": "内容疑似含密钥/凭据，禁止写入共享区（user/project/agent/session）。如需保存，请显式使用 device 私有区。"}
```

### `GET /v1/memories/{id}`

权限：所有活跃设备（读全开）。取单条（跟随版本链到最新版）。

```json
{ /* 记忆对象 */ }
```

404：不存在或已软删除。403：无权读（`session` 区外的正常情况不会发生）。

### `PATCH /v1/memories/{id}`

权限：写权（按 scope 矩阵）。更新——产生 `version+1` 新行，旧行 `superseded_by` 指向新 id。

**请求体**（均可选，至少一项）

| 字段 | 类型 | 说明 |
|---|---|---|
| `content` | string | 新正文（1–20000）|
| `kind` | string | 新 kind |
| `tags` | string[] | 覆盖标签 |
| `confidence` | float | 0.0–1.0 |
| `valid_until` | string | 失效时间 |

```json
{ /* 更新后的新版本记忆对象，version=2 */ }
```

### `DELETE /v1/memories/{id}`

权限：写权。软删除（`deleted_at` 置时间戳）。

```json
{"ok": true, "deleted_id": 211}
```

### `POST /v1/memories/{id}:promote`

权限：写权。把私有记忆提升到共享 scope（版本链，审计）。

**请求体**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `to_scope` | string | ✓ | 目标 scope（`user` \| `project` \| …）|
| `to_scope_ref` | string | | `project` 时为 slug |
| `reason` | string | | 提升理由（写入审计）|

```json
{"ok": true, "memory_id": 213, "scope": "user", "scope_ref": ""}
```

---

## 检索

### `POST /v1/memories:search`

权限：所有活跃设备。BM25 全文检索（FTS5 + jieba 中文分词）。默认排除 `insight` 聚合条目（它们是源记忆的压缩重述，和源一起召回就是重复）。

**请求体**

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `query` | string | ✓ | — | 关键词（1–500 字符）|
| `scopes` | string[] | | null | scope 过滤（如 `["user","project:etuangou"]`）；null=全部可读 |
| `limit` | int | | 8 | 1–50 |
| `since` | string | | null | 只返回该时间之后的记忆 |
| `include_deleted` | bool | | false | 是否包含软删除 |
| `include_insight` | bool | | false | 是否包含聚合条目 |

```bash
curl -s http://127.0.0.1:3233/v1/memories:search \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"query":"端口策略","limit":5}'
```

```json
{
  "results": [ /* 记忆对象数组，按 BM25 相关度排序 */ ],
  "matched": 2,
  "query": "端口策略"
}
```

## 提取与聚合

### `POST /v1/ingest`

权限：所有活跃设备。提交对话批次，服务端 LLM 提取事实 + 与已有记忆裁决（ADD/UPDATE/NOOP）+ 写库。

**请求体**

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `dialogue` | string[] | ✓ | — | 按时间排序的消息文本（1–200 条）|
| `session_id` | string | | null | 会话 id |
| `agent_id` | string | | `root` | 来源 agent |
| `async_mode` | bool | | `true` | true=后台处理返回受理号；false=同步等结果 |

```bash
curl -s http://127.0.0.1:3233/v1/ingest \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"dialogue":["用户: 对外只走 2000-2099","助手: 已配置"],"async_mode":false}'
```

**响应**（`async_mode=false` 时直接返回处理结果；`true` 时 `extracted=0`、`written=[]`，用 `batch_id` 轮询）

```json
{
  "accepted": true,
  "batch_id": "b-8f3a2c1d",
  "extracted": 1,
  "written": [
    {"id": 214, "action": "ADD", "scope": "user"},
    {"id": 78, "action": "UPDATE", "scope": "user", "proposal": 17}
  ],
  "note": ""
}
```

- `action`：`ADD`（新事实）| `UPDATE`（已有记忆的新版本）| `NOOP`（纯重复，跳过）
- `proposal`：无权写目标 scope 时自动生成的审批申请 id（可选字段）

无 LLM key 时批次仍受理，后台任务 `status=error`、`error` 记录原因。

### `GET /v1/ingest/{batch_id}`

权限：提交该批次的设备。批次处理状态。

```json
{
  "status": "done",
  "extracted": 2,
  "written": [{"id": 214, "action": "ADD", "scope": "user"}],
  "error": null
}
```

`status` ∈ `running` | `done` | `error`。

### `POST /v1/insights/run`

权限：admin。立即运行日度聚合（每日 04:30 后台线程也会自动跑）。**源记忆 < 5 条时跳过**（小库聚合无价值）。

```json
{
  "status": "skipped",
  "reason": "源记忆仅 3 条（<5），小库聚合无价值，跳过"
}
```

成功时：

```json
{
  "status": "done",
  "memory_id": 215,
  "items": 7,
  "summary": "本周部署要点：端口段规则 2000-2099…（聚合摘要前 300 字符）"
}
```

其他 `status=skipped` 情形：近 24h 无新增用户级记忆、源集合与上次 insight 相同（无新内容）。

### `GET /v1/insights`

权限：admin。列出聚合条目。

```json
{"results": [ /* 记忆对象数组，kind=insight */ ]}
```

## 治理：审批与项目

### `GET /v1/proposals`

权限：admin 看全部；普通设备仅自己的申请。

| Query | 类型 | 默认 | 说明 |
|---|---|---|---|
| `status` | string | `pending` | `pending` \| `approved` \| `rejected` \| `all` |
| `limit` | int | 20 | 每页条数（1–500）|
| `offset` | int | 0 | 分页偏移 |

```json
{
  "results": [
    {
      "id": 17,
      "device_id": 3,
      "device_name": "test-bench",
      "type": "scope_upgrade",
      "memory_id": 78,
      "target_scope": "user",
      "reason": "内容跨设备通用",
      "status": "pending",
      "created_at": "2026-11-10 22:12:57",
      "decided_at": null,
      "decided_by": null,
      "memory_content": "memoryd-dsh-plugin 已上线…"
    }
  ],
  "count": 1,
  "total": 1,
  "limit": 20,
  "offset": 0
}
```

### `POST /v1/proposals`

权限：设备提交申请（scope 升级 / 加入项目）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | string | ✓ | `scope_upgrade` \| `project_join` |
| `memory_id` | int | | `scope_upgrade` 必填（要升级的记忆）|
| `target_scope` | string | ✓ | `user` \| `agent` \| `project:<slug>` |
| `reason` | string | | ≤ 2000 字符 |

```json
{"id": 18, "status": "pending", "type": "project_join", "target_scope": "project:etuangou"}
```

### `POST /v1/proposals/{id}:approve`

权限：admin。批准后**立即执行**：`scope_upgrade` → 复制记忆到目标 scope（版本+1 + 旧行 superseded）；`project_join` → 把设备加为项目成员（role=member）。

| 字段 | 类型 | 说明 |
|---|---|---|
| `note` | string | ≤ 500，决策备注 |

```json
{"ok": true, "action": "scope_upgrade", "new_memory_id": 216, "scope": "user"}
```

`project_join`：

```json
{"ok": true, "action": "project_join", "slug": "etuangou", "device_id": 3, "role": "member"}
```

重复审批 409：`{"detail": "申请已处理（approved）"}`。

### `POST /v1/proposals/{id}:reject`

```json
{"ok": true, "status": "rejected"}
```

### `GET /v1/projects` · `POST /v1/projects`

权限：admin。项目列表/创建。

创建请求：`{"slug": "etuangou", "name": "E团购"}`（slug 规则：`^[a-z0-9][a-z0-9\-]{0,63}$`）。

### `GET /v1/memberships` · `POST /v1/memberships` · `DELETE /v1/memberships/{slug}/{device_id}`

权限：admin。项目成员管理。

成员请求：`{"slug": "etuangou", "device_id": 3, "role": "member"}`（`owner` \| `member` \| `readonly`）。

## 设备与 Token

### `GET /v1/devices`

权限：admin。

| Query | 类型 | 默认 | 说明 |
|---|---|---|---|
| `limit` | int | 20 | 每页条数（1–500）|
| `offset` | int | 0 | 分页偏移 |

```json
{
  "results": [
    {"id": 1, "name": "admin", "role": "admin", "active": true,
     "last_seen_at": "2026-11-10 22:40:00", "created_at": "..."}
  ],
  "total": 3,
  "limit": 20,
  "offset": 0
}
```

### `POST /v1/devices`

权限：admin。注册新设备，**token 只显示这一次**。

**请求体**

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `name` | string | ✓ | — | 设备名（1–64，唯一，重复 409）|
| `role` | string | | `device` | `admin` \| `device` |

```json
{
  "device_id": 4,
  "name": "oracle-141",
  "role": "device",
  "token": "Xk9-EXAMPLE-2fjPq_Replace-ME-40chars-Ab1c2D",
  "note": "token 只显示这一次，服务端仅存哈希"
}
```

### `POST /v1/devices/{id}/token`

权限：admin。轮换 token——返回新 token，旧 token 立即失效。

```json
{"device_id": 4, "token": "Xk9-EXAMPLE-新token", "note": "旧 token 已失效"}
```

### `DELETE /v1/devices/{id}/token`

权限：admin。吊销（`token_hash='revoked'`）。已吊销设备调用任何需鉴权接口返回 401。

```json
{"ok": true, "detail": "设备 4 token 已吊销"}
```

### `POST /v1/devices/{id}/activate` · `/deactivate`

权限：admin。恢复/停用设备（停用后调用返回 401）。对已吊销的设备调 activate 返回 409：`{"detail": "token 已吊销，恢复请先轮换签发新 token"}`。

```json
{"ok": true, "detail": "设备 4 已恢复活跃"}
```

## 会话黑板

### `POST /v1/sessions/{session_id}/post`

权限：设备。向会话黑板写一条（结束归档）。

**请求体**

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `session_id` | string | ✓ | — | 1–128 字符 |
| `content` | string | ✓ | — | 1–20000 |
| `agent_id` | string | | `sub` | 来源 agent |

```json
{"ok": true, "id": 217, "session_id": "sess-abc"}
```

### `GET /v1/sessions/{session_id}/list`

```json
{"results": [ /* 记忆对象数组 */ ]}
```

### `DELETE /v1/sessions/{session_id}`

归档整个会话（软删除其黑板条目）。

```json
{"ok": true, "archived": true}
```

## 事件流（SSE）

### `GET /v1/events`

权限：设备。Server-Sent Events 长连接，推送**其他设备**的共享区变更（自己写的不推）。15 秒心跳保活。

```
data: {"type":"memory.write","scope":"user","scope_ref":"","memory_id":214,"device_id":3,"detail":"","time":"2026-11-10 22:50:00"}

: ping 1710000000
```

`type` 取值：`memory.write`（新增）| `memory.update`（版本更新）| `memory.promote`（scope 提升）| `insight`（聚合完成）。

## 审计

### `GET /v1/audit`

权限：admin。操作流水（倒序）。

| Query | 类型 | 默认 | 说明 |
|---|---|---|---|
| `limit` | int | 20 | 每页条数（1–500）|
| `offset` | int | 0 | 分页偏移 |

```json
{
  "results": [
    {
      "id": 412,
      "device_id": 1,
      "device_name": "admin",
      "action": "memory_promote",
      "scope": "user",
      "scope_ref": "",
      "detail": "id=78->216",
      "created_at": "2026-11-10 22:12:57"
    }
  ]
}
```

## 管理台

### `GET /ui/`

浏览器访问的 Web 控制台（`/ui/index.html`）。输入设备 token 登录（存 localStorage）。

| Tab | 功能 |
|---|---|
| 记忆 | 总览（scope 过滤+统计）、新增/编辑/删除/提升 scope |
| 检索 | BM25 关键词检索 |
| 审批 | 设备报送的 scope 升级/入项申请，通过/拒绝 |
| 设备与 Token | 注册/轮换/吊销/停用/恢复 |
| 审计 | 操作流水 |
