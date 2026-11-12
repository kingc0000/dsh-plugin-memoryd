# memoryd DeepSeek Harness 插件指南

[English guide](./README.md)

本指南说明如何安装、配置和使用 `dsh-plugin-memoryd`：指令注入、四个记忆工具、对话自动捕获，以及 scope 模型（跨设备共享区 + 设备私有隔离区）。

## 1. 插件工作机制

插件为 DSH 会话接入自托管 [memoryd](../README_ZH.md) 服务的长期跨设备记忆。

```text
新会话启动
  └─ 注入记忆使用规则（中文/英文）
       └─ LLM 回答依赖历史的问题前先调 memory_search
            └─ 结果带 scope 标注：user / project / device / agent / session

对话完成
  └─ 攒满 N 个完整轮次 → POST /v1/ingest（服务端 LLM 提取 + 裁决）
       └─ 重复/换措辞的事实会被合并（UPDATE），不会重复新增
```

三层协同：

| 层 | 职责 |
|---|---|
| 指令注入 | 会话启动时告诉 LLM 什么时候、怎么用记忆工具 |
| 手动工具 | `memory_search` / `memory_write` / `memory_promote` / `memory_status` —— LLM 主动调用 |
| 自动捕获 | 每会话状态机：每攒满 `autoMemoryInterval`（默认 5）个完整轮次，提交最近一轮对话给 /v1/ingest 做服务端提取 |

提取与去重在**服务端**——所有客户端（不限于 DSH）共用同一条裁决管线，插件保持薄客户端。

## 2. 环境要求

- 一个运行中的 memoryd 服务（自托管，部署见 memoryd 仓库）。
- DeepSeek Harness `0.1.7-rc.2`、`0.2.0-rc.2`、`0.2.1-alpha.2` 或 `0.2.1-alpha.4`。
- Node.js `^22.19.0` 或 `>=24.0.0`（与当前 DSH 引擎范围一致）。
- memoryd 设备 token。获取方式二选一：
  - 自助注册（推荐）：`python3 scripts/register_device.py --name <设备名> --endpoint <服务地址> --wait`，等 admin 在管理台批准后自动领取一次性 token；
  - 或由 admin 签发（`POST /v1/devices`）。
  token 把这个 DSH 实例标识为一台设备；memoryd 按设备 id 隔离各设备的私有记忆。

## 3. 安装

在你的 DSH profile 里加入插件：

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
            # token 可省略：不填时插件自动注册（生成持久化指纹并提交），
            # admin 在管理台批准后自动领取一次性 token（存 ~/.memoryd/token）
            token: <设备token>    # 显式配置则优先；或环境变量 MEMORYD_TOKEN
            # apiKey 为旧名兼容别名（效果同 token，已弃用）
            language: zh
            searchLimit: 8
```

重启 DSH。会话应能看到记忆使用规则和四个工具。

## 4. 配置项

| 配置 | 默认值 | 说明 |
|---|---|---|
| `endpoint` | `http://127.0.0.1:3233` | memoryd 服务地址 |
| `token` | `""` | memoryd 设备 token（Bearer 凭证）；未配置时回退环境变量 `MEMORYD_TOKEN`（`MEMORYD_API_KEY` 兼容），仍无则触发自动注册 |
| `apiKey` | `""` | `token` 的旧名兼容别名（已弃用，两者同值） |
| `autoRegister` | `true` | 无 token 时自动注册（指纹 + 轮询审批），批准后自动领取 |
| `deviceName` | 主机名 | 自动注册时的设备名 |
| `tokenFile` | `~/.memoryd/token` | 领取的 token 持久化文件（600 权限） |
| `fingerprintFile` | `~/.memoryd/fingerprint` | 注册指纹持久化文件 |
| `registrationPollMs` | `5000` | 等待审批的轮询间隔（毫秒） |
| `requestTimeoutMs` | `10000` | 请求超时（ingest 用 60 秒）|
| `searchLimit` | `8` | `memory_search` 默认返回条数 |
| `autoMemoryEnabled` | `true` | 自动捕获开关 |
| `autoMemoryInterval` | `5` | 每攒满 N 个完整轮次提交一次 |
| `rootAgentsOnly` | `false` | 只捕获主会话，跳过子代理会话 |
| `language` | `zh` | 指令注入语言，`zh` / `en` |

未知配置键直接抛 `TypeError`（fail fast，不静默忽略）。

## 5. 工具

| 工具 | 用途 |
|---|---|
| `memory_search` | 关键词检索可读记忆（BM25 + jieba 分词）。结果带 scope 标注和来源。 |
| `memory_write` | 写一条记忆。默认写**设备私有区**；写共享区要求内容跨设备通用且不含密钥。 |
| `memory_promote` | 把私有记忆提升到共享区（审计，需理由）。 |
| `memory_status` | 连接与设备身份检查。 |

## 6. scope 模型

| scope | 读 | 写 | 适合放什么 |
|---|---|---|---|
| `user` | 所有设备 | 所有设备（密钥红线扫描）| 跨设备事实与偏好 |
| `project:slug` | 所有设备 | 项目成员 | 项目知识 |
| `device` | 所有设备 | 本机（+admin）| 路径、主机、生产细节、拿不准的内容 |
| `agent` | 所有设备 | 所有设备 | 可复用的同类任务经验 |
| `session` | 所有设备 | 任意（记录来源）| 会话黑板 |

密钥（`sk-…`、私钥、`password=`）写入共享区直接拒绝；设备私有区允许。拿不准一律写 device 区。

LLM 想写无权限的目标 scope 时，memoryd 会自动降级写入 device 区并提交升级申请（`POST /v1/proposals`）等 admin 审批——**不会发生跨 scope 自动越权**。

## 7. 隐私说明

- 只捕获消息文本（text 部分）；插件自己注入的消息会被跳过（防自我复读）。
- 每次提交只带**最近一轮完整对话**，不是全部历史。
- `rootAgentsOnly: true` 可完全跳过子代理会话。
- 自动捕获是 fire-and-forget：memoryd 故障不阻塞对话主流程。

## 8. 故障排查

| 症状 | 检查 |
|---|---|
| `memoryd 不可用或未配置 token` | `apiKey` 缺失/错误；调 `memory_status` |
| 检索无结果 | token 被吊销，或确实没有相关记忆；换更宽的关键词 |
| 自动捕获无动静 | `autoMemoryEnabled` 关了，或完整轮次未攒满（一轮需要同时有用户和助手消息）|

## 许可证

Apache-2.0
