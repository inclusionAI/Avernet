# 自带引擎接入指南

[English](README.md)

本指南面向**自己运行 Agent 引擎**、希望把 bot 接入 Avernet 平台的团队。接入后，
用户在 Avernet 工作台里创建、配置你们的 bot 并与之对话，体验与其他 bot 一致；
Agent 本身由你们的引擎运行。

如果你们只想让 bot 加入多 bot 群聊，可以跳过本指南的大部分内容，直接阅读
[Bot 接入指南](../../../../docs/bot-integration.zh-CN.md)（见[第 7 步](#第-7-步可选加入群体协作bcn)）。

> **状态说明。**本指南描述的是平台与第一个外部引擎（teclaw）之间已经在用的契约。
> 有两部分**尚未定案**：由你们自己托管的引擎如何注册与鉴权，以及第 2 步生命周期
> 接口的具体请求格式。标注「待与 BaaS 确认」「待与 Backend 确认」的事项须在上线前
> 与对应团队确定，见
> [待确认事项](#待确认事项)。

---

## 目录

- [接入原理](#接入原理)
- [接入清单](#接入清单)
- [第 1 步：在 BaaS 注册引擎](#第-1-步在-baas-注册引擎)
- [第 2 步：实现 bot 生命周期接口（由 BaaS 调用）](#第-2-步实现-bot-生命周期接口)
- [第 3 步：接收 bot 配置（`POST /api/v1/bot/apply`）](#第-3-步接收-bot-配置)
- [第 4 步：理解配置 artifact](#第-4-步理解配置-artifact)
- [第 5 步：实现文件接口](#第-5-步实现文件接口)
- [第 6 步：实现运行时接口](#第-6-步实现运行时接口)
- [第 7 步（可选）：加入群体协作（BCN）](#第-7-步可选加入群体协作bcn)
- [第 8 步：联调自查](#第-8-步联调自查)
- [通用约定](#通用约定)
- [待确认事项](#待确认事项)
- [参考文档](#参考文档)

---

## 接入原理

有两个 Avernet 服务会调用你们的引擎：

- **BaaS** 负责每个 bot 的生命周期与路由。它请你们的平台创建、更新、重启、删除
  bot，并算出访问每个 bot 所用的地址和令牌。它还在你们的 bot 上运行定时任务与
  协作任务。
- **Backend** 负责每个 bot 的配置和工作台。它向运行中的 bot 下发配置和文件修改，
  并把用户的对话与会话请求转发给 bot。

```text
                      Avernet 工作台（用户）
                                │
                                ▼
 ┌─────────────────────────── Avernet 平台 ───────────────────────────┐
 │                                                                     │
 │  Backend ── 向 BaaS 询问「bot X 在哪」──▶ BaaS                      │
 │ （配置、文件、对话）                      （注册、生命周期、路由、   │
 │                                            任务运行）               │
 └───────┬────────────────────────────────────────────┬────────────────┘
         │                                            │
         │ A. 单 bot 调用（第 3、5、6 步；            │ B. 生命周期调用（第 2 步）：
         │    地址与令牌来自 BaaS）：                 │    创建 / 更新 / 重启 /
         │    POST /api/v1/bot/apply                  │    删除 / 查询 bot
         │    /api/v1/file/*                          │ C. 经 /api/ws 运行任务（第 6 步）
         │    /api/sessions、/api/ws                  │
         ▼                                            ▼
 ┌──────────────────────────── 你们的引擎 ────────────────────────────┐
 │  第 1 步：上线前在 BaaS 注册引擎（一次性配置，不是运行时调用）     │
 │  生命周期接口（第 2 步）── 完成后向 BaaS 回调 ──▶ BaaS              │
 │  单 bot 接口（第 3、5、6 步）                                        │
 └────────────────────────────────┬────────────────────────────────────┘
                                  │ 下载 skill / 文件（第 4 步）
                                  ▼
                          内容存储（OSS）
```

第 7 步（接入 BCN）为可选，独立于上图。

需要理解三点：

1. **配置归平台，应用归引擎。**用户在 Avernet 里管理 bot 的 skill、MCP 服务、
   身份文件、资源文件和 CLI 工具。平台组装出该 bot 的**完整**配置（即
   *artifact*）发给引擎：创建 bot 时发一次，之后配置每变化一次就再发一次。
2. **artifact 是全量快照，不是增量。**每次下发都描述完整的期望状态，引擎让 bot
   与之完全一致。
3. **大内容按引用传递。**artifact 不含文件内容，只含 `{store, path}` 引用，由
   引擎从内容存储下载。

---

## 接入清单

| # | 要做的事 | 调用方 | 是否必须 |
|---|---|---|---|
| 1 | 注册引擎（引擎类型、访问地址、鉴权方式） | — | 必须 |
| 2 | 实现 bot 生命周期接口并发送完成回调 | BaaS | 必须 |
| 3 | 实现 `POST /api/v1/bot/apply`，按整包替换语义应用 artifact | Backend | 必须 |
| 4 | 从内容存储下载 artifact 引用的内容 | — | 必须 |
| 5 | 实现文件接口（`/api/v1/file/*`） | Backend | 工作台文件编辑需要 |
| 6 | 实现运行时接口（健康检查、会话、对话） | Backend、BaaS | 对话与任务运行需要 |
| 7 | 接入 BCN | — | 仅多 bot 协作需要 |
| 8 | 通过联调自查清单 | — | 必须 |

---

## 第 1 步：在 BaaS 注册引擎

BaaS 是唯一知道如何访问你们引擎的地方。其他 Avernet 服务调用某个 bot 之前，都会
先向 BaaS 获取它的地址和令牌。注册时 BaaS 需要：

| 项 | 说明 | 状态 |
|---|---|---|
| **引擎类型** | 简短的小写标识，如 `teclaw`。它会作为 `engine_type` 出现在每份 artifact 里。 | 与 BaaS 商定 |
| **生命周期接口地址** | BaaS 发送生命周期调用（第 2 步）的地址。 | **待与 BaaS 确认** |
| **单 bot 地址** | BaaS 如何拼出某个 bot 的 HTTP 与 WebSocket 地址（第 3、5、6 步）。 | **待与 BaaS 确认** |
| **鉴权** | BaaS 与 Backend 如何向引擎证明身份，以及引擎回调 BaaS 时如何证明身份。 | **待与 BaaS 确认** |

**当前机制（平台托管的引擎）。**目前 BaaS 经平台的代理网关访问 bot：以
`TECLAW_{bot_id}@{template_id}:{port}`（默认端口 `20003`）寻址，并给调用方一个
令牌，调用方放在 `x-proxypass-token` 请求头里发送。对于**你们自己托管**的引擎，
地址与鉴权方案都还没有设计。请在开始其他步骤之前与 BaaS 确定，因为它们
决定了下文每个请求的请求头。

---

## 第 2 步：实现 bot 生命周期接口

BaaS 通过这组接口管理你们平台上的 bot。bot 的创建与删除只通过这里进行。

> **待与 BaaS 确认：**这些操作的路径以及请求、响应体尚未公布。本节说明每个操作必须做
> 什么，具体的报文格式由 Avernet BaaS 团队提供。

### 操作

| 操作 | 你们平台要做的事 | 返回 |
|---|---|---|
| **创建** | 按请求中的配置创建 bot（其中包含该 bot 的第一份 artifact，见第 4 步）并启动。bot 就绪前必须应用完整份 artifact（[R4](#应用-artifact-的规则)）。 | 你们的 bot ID 与状态，或任务 ID（异步） |
| **更新** | 把新配置应用到已有 bot。应用规则与第 3 步相同。 | bot ID 与状态，或任务 ID（异步） |
| **重启** | 重启 bot。BaaS 以「用它上次发送的配置再更新一次」实现，因此必须幂等（[R3](#应用-artifact-的规则)）。 | bot ID 与状态，或任务 ID（异步） |
| **删除** | 停止并删除 bot。 | bot ID 与状态 |
| **查询** | 返回 bot 当前的状态、当前配置和出站规则。 | bot 信息 |
| **更新出站规则** | 替换 bot 的出站请求规则（例如给 bot 发出的请求加上哪些请求头）。目前是 `PUT /api/v1/bot/{bot_id}/operationRules`，请求体形如 `{"header_operation_rules": [...]}`。规则也可以只针对某一个会话追加。 | 是否接受 |

### 异步操作与回调

创建、更新、重启可能耗时较长，因此是异步的：

1. BaaS 在请求中带上 `callback_context`：

   ```json
   {
     "callback_url": "https://<baas>/api/v1/publish/teclaw-callback",
     "publish_id": "123",
     "device_uuid": "dev-...",
     "tenant": "default",
     "operator": "u1"
   }
   ```

2. 你们的平台立即返回**任务 ID**、操作类型（`CREATE`、`UPDATE` 或 `DELETE`）、
   bot ID（如已知）以及进行中的状态（如 `RUNNING`）。
3. 操作完成后，你们的平台把结果 `POST` 到 `callback_url`：

   ```json
   {
     "success": true,
     "data": {
       "schema_version": 1,
       "callback_context": { "...": "第 1 步中的 callback_context，原样返回" },
       "task_id": "task-...",
       "operation": "CREATE",
       "task_status": "SUCCESS",
       "bot_id": "b_01KV5KRZG3FGC3H06RE5T9YRT0",
       "version": 7
     },
     "error": null,
     "error_code": null
   }
   ```

| 字段 | 必有 | 说明 |
|---|---|---|
| `success` | 是 | 操作成功为 `true`。 |
| `data.schema_version` | 是 | 回调格式的版本。取值**待与 BaaS 确认**。 |
| `data.callback_context` | 是 | BaaS 发来的 `callback_context`，**原样返回**。必须包含 `device_uuid`、`publish_id`、`tenant`，否则 BaaS 以 `400` 拒绝回调。 |
| `data.task_id` | 是 | 第 2 步返回的任务 ID。 |
| `data.operation` | 是 | `CREATE`、`UPDATE` 或 `DELETE`。 |
| `data.task_status` | 是 | 终态，如 `SUCCESS` 或 `FAILED`。 |
| `data.bot_id`、`data.version` | 否 | 你们的 bot ID，以及已应用的配置版本。 |
| `error`、`error_code` | 否 | `success` 为 `false` 时说明出错原因。 |

BaaS 不认识该设备时返回 `404`。回调是幂等的：没送达的回调可以放心重试。BaaS
如何对回调鉴权**待与 BaaS 确认**（见第 1 步）。

---

## 第 3 步：接收 bot 配置

### `POST /api/v1/bot/apply`

bot 运行期间，配置每变化一次，Backend 都会调用 bot 上的这个接口：用户编辑
skill / MCP 服务 / 身份文件 / 资源文件 / 渠道，或应用配置清单（manifest）时。
Backend 会先向 BaaS 获取 bot 的地址和令牌。

**请求**

```http
POST /api/v1/bot/apply
Content-Type: application/json
```

```json
{
  "bot_id": "b_01KV5KRZG3FGC3H06RE5T9YRT0",
  "operation": "UPDATE",
  "bot_config": { "...": "artifact，见第 4 步" }
}
```

| 字段 | 说明 |
|---|---|
| `bot_id` | 本次配置对应的 bot。 |
| `operation` | 目前固定为 `"UPDATE"`。遇到不认识的值按错误处理。 |
| `bot_config` | 完整的 artifact（见[第 4 步](#第-4-步理解配置-artifact)）。 |

**响应**

- **整份 artifact 应用完成后**才返回 HTTP `2xx`。
- 应用失败时返回非 `2xx`。平台会把失败告知用户，并可能稍后重新下发。
- 平台等待 **30 秒**。如果应用可能更久（例如下载较大的 skill 包），请与 Avernet
  团队沟通超时时间。

目前平台不读取响应体。建议使用标准信封（见[通用约定](#通用约定)）：
`{"success": true}`。

bot 的**第一份** artifact 不经过此接口，而是随生命周期的**创建**调用到达（第 2
步）。两条路径携带的是同一种 artifact，适用同样的规则。

### 应用 artifact 的规则

以下五条是契约的核心，适用于每一份 artifact，无论它经生命周期调用还是经
`/api/v1/bot/apply` 到达。

| # | 规则 | 含义 |
|---|---|---|
| R1 | **整包替换** | 应用后，bot 恰好拥有 artifact 列出的内容。artifact 列出 skill `{A, B}`、bot 原有 `{B, C}`，结果是 `{A, B}`，`C` 被移除。不做合并。 |
| R2 | **永不触碰 `MEMORY.md` 与 `IDENTITY.md`** | 它们是引擎生成的运行期状态，不是配置。不得因 artifact 写入它们，也不得删除它们，即使 `identity_files` 没有列出。这是 R1 唯一的例外。 |
| R3 | **幂等** | 同一份 artifact 应用两次与应用一次结果相同，不留残留文件、不产生重复条目。重复下发是正常情况，不是错误。 |
| R4 | **先应用，后就绪** | 整份 artifact 应用完成之前，不得报告 bot 已就绪。 |
| R5 | **忽略未知字段** | 契约会演进。遇到不认识的字段直接忽略，不要拒绝整份 artifact。 |

### 「替换」的范围按类目划分

R1 替换的是*每个类目自己区域内*的状态，各类目的区域不同：

| 类目 | 被替换的区域 |
|---|---|
| `skills` | bot 的已激活 skill 集合。未列出的 skill 被停用。 |
| `identity_files` | bot 的身份文件，**不含** `MEMORY.md` 与 `IDENTITY.md`。 |
| `resources` | **此前你们作为资源投递过的路径**，加上本次 artifact 列出的路径。 |
| `mcp.servers` | bot 已启用的 MCP 服务集合。 |
| `cli_tools` | 平台下发的 CLI 工具。镜像自带的命令不受影响。 |

> **`resources` 需要特别注意。**它的区域有两条边，缺一不可。
>
> - **不能过窄。**`resources` 从 `[data/a.csv]` 变成 `[]` 时，必须删除
>   `data/a.csv`。新 artifact 不再提及它，所以引擎要自己记录哪些路径是作为资源
>   投递的。
> - **不能过宽。**工作区属于 bot 和它的用户。用户或模型在其中创建的文件不是资源，
>   即使紧挨着某个资源路径，也绝不能删除。

### 每个类目归谁管理：`ownership`

artifact 可能带一个 `ownership` 映射，按类目说明平台给出的列表是否权威：

```json
"ownership": {
  "mcp": "platform",
  "skills": "platform",
  "resources": "engine",
  "identity_files": "engine"
}
```

| 值 | 引擎的处理 |
|---|---|
| `"platform"` | 本次 artifact 中该类目的列表就是完整期望状态，按 R1 处理。空列表表示清空该区域。 |
| `"engine"` | 该类目由引擎管理。忽略 artifact 中的列表，保持引擎当前状态。 |
| 缺失（整个映射或其中某个键） | 保持引擎原有行为。 |

`mcp` 永远是 `"platform"`。用户在工作台里编辑单个文件时，文件类目为
`"engine"`，因为运行中的 bot 是其文件的真相源。应用 bot 配置清单（manifest）时，
所有类目都是 `"platform"`。

---

## 第 4 步：理解配置 artifact

artifact 的正式定义是 JSON Schema
[`artifact.schema.json`](../../src/agentclaw/community/kernel/bot_config/artifact.schema.json)，
请在测试中用它校验。本节解释各字段。

### 示例

```json
{
  "schema_version": 4,
  "version": 7,
  "engine_type": "teclaw",
  "stores": {
    "bot-data":   { "type": "oss", "bucket": "avernet-prod", "base": "teclaw/prod/bolt_data" },
    "skill-repo": { "type": "oss", "bucket": "avernet-skills", "base": "skills" }
  },
  "skills": [
    { "name": "weather", "scope": "shared", "store": "skill-repo", "path": "team/weather" }
  ],
  "identity_files": [
    { "name": "RULES.md", "store": "bot-data", "path": "staff_u1/bot7/identity/RULES.md" }
  ],
  "resources": [
    { "name": "data/faq.csv", "store": "bot-data", "path": "staff_u1/bot7/workspace/data/faq.csv" }
  ],
  "mcp": {
    "servers": [
      { "server_code": "search", "transport": "http",
        "endpoint": "https://mcp.example.com/search", "headers": { "x-api-key": "..." } },
      { "server_code": "local-db", "transport": "stdio",
        "command": "python3", "args": ["-m", "db_mcp"], "env": { "DB_PATH": "/data/db" } }
    ]
  },
  "cli_tools": [
    { "name": "mycli", "store": "bot-data", "path": "staff_u1/bot7/cli/mycli",
      "md5": "9f2c1b7d4e5a60318c2f0ab4d7e9c135", "version": "1.4.2" }
  ],
  "engine_overrides": { "channels": { "...": "..." } },
  "ownership": { "mcp": "platform", "skills": "engine", "resources": "engine", "identity_files": "engine" },
  "engine_ext": {
    "bot_id": "b_01KV5KRZG3FGC3H06RE5T9YRT0",
    "owner_id": "u1",
    "bot_name": "Support Bot",
    "stage": "draft"
  }
}
```

### 顶层字段

| 字段 | 必有 | 说明 |
|---|---|---|
| `schema_version` | 是 | 契约版本，当前为 `4`。部分字段（`cli_tools`、`ownership`）是在不升版的情况下加入的，所以要**判断字段是否存在**，不要依赖版本号。 |
| `version` | 是 | 该 bot 的内容版本。已发布的 bot 为整数；用户仍在编辑的草稿 bot 为 `null`。 |
| `engine_type` | 是 | 你们的引擎类型（第 1 步确定）。 |
| `stores` | 是 | 内容存放位置，以 store ID 为键。见[下载内容](#下载内容)。 |
| `skills` | 是 | 要激活的 skill。 |
| `identity_files` | 是 | 用户或平台编写的工作区文件（如 `RULES.md`、`AGENTS.md`）。 |
| `resources` | 是 | 用户上传到 bot 工作区的文件。 |
| `mcp.servers` | 是 | bot 可用的 MCP 服务。 |
| `cli_tools` | 否 | 要放到 Agent `PATH` 上的命令行工具。 |
| `engine_overrides` | 是 | bot 级设置（如渠道），叠加在引擎自己的基础配置之上。基础配置文件归引擎所有，平台从不下发。 |
| `ownership` | 否 | 各类目的归属，见[第 3 步](#每个类目归谁管理ownership)。 |
| `engine_ext` | 是 | 归引擎所有的自由 JSON，见下文。 |

### 文件引用（`skills`、`identity_files`、`resources`）

每一项是 `{name, store, path}`：`store` 是 `stores` 中的键，`path` 是相对于该
store 的路径。**文件在引擎内落到哪里由你们决定。**

- `skills[]` 多一个 `scope` 字段：`"shared"`（来自 skill 目录）或 `"user"`
  （用户为该 bot 上传）。skill 的 `path` 是一个**目录前缀**：下载其下所有对象
  即得到完整的 skill 包。
- `identity_files[]` 与 `resources[]` 指向单个文件。`name` 是该文件在 bot 身份
  区域或工作区内的路径（如 `data/faq.csv`）。

### MCP 服务

每个服务有一个 `server_code`，并且恰好是以下两种形态之一：

| 形态 | `transport` | 字段 | 引擎的处理 |
|---|---|---|---|
| 远程 | `"http"` 或 `"sse"` | `endpoint`（必有）、`headers` | 带这些请求头连接该 URL。 |
| 本地 | `"stdio"` | `command`（必有）、`args`、`env` | 以子进程启动 `command`，通过 stdin/stdout 进行 JSON-RPC 通信。 |

> **敏感信息。**远程条目可能内联凭证，位于 `headers` 中或 `endpoint` 的
> `?authorization=` 查询参数里。请把 artifact 当作机密：不要完整打印到日志，
> 不要明文存储。

### CLI 工具

每一项就是**一个命令、一个可执行文件**：

| 字段 | 说明 |
|---|---|
| `name` | 命令名。Agent 以 `mycli` 而非绝对路径调用。同一 bot 内唯一。 |
| `store`、`path` | 可执行文件。平台已经完成下载、校验和解包，`path` 永远指向单个文件，不会是目录或压缩包。 |
| `md5` | 文件的 MD5。与已有同名工具一致则跳过下载；不一致则替换文件。 |
| `version` | 仅用于展示。 |

引擎需要：下载文件、设置可执行权限（对象存储不保留文件权限位）、以 `name` 为
命令名放到 Agent 进程的 `PATH` 上。不再列出的工具要移除。`"cli_tools": []`
表示移除所有平台下发的工具。镜像自带的命令不受影响。

### `engine_ext`

`engine_ext` 归你们所有，平台只负责保存并原样返回，从不解读。平台总会写入四个键：

| 键 | 值 |
|---|---|
| `bot_id` | 平台侧的 bot ID。 |
| `owner_id` | 所有者的用户 ID。 |
| `bot_name` | bot 的显示名称。 |
| `stage` | `draft`（编辑中）、`canary`（验证）、`release`（已发布）或 `eval`。 |

### 下载内容

`stores` 给出每个 store 的位置，**从不包含凭证**：

| 字段 | 说明 |
|---|---|
| `type` | `oss` 或 `nas`。 |
| `bucket` | OSS bucket。 |
| `base` | bucket 内的 key 前缀。 |
| `endpoint`、`region` | 可选。缺失时使用引擎自己的配置。 |

一个引用对应的对象 key 为 `base` + `/` + `path`。

自托管引擎如何获得这些 store 的**读权限**（凭证、网络连通）**待与 Backend 确认**。

---

## 第 5 步：实现文件接口

用户可以在工作台里浏览和编辑 bot 的文件。Backend 通过 bot 上的文件接口完成这些
操作，每个文件一次请求。运行中的 bot 是其文件的真相源，所以这些编辑不经过
`/api/v1/bot/apply`。

### 路径

平台发出的每个路径都以下列三个命名空间之一开头：

| 命名空间 | 内容 |
|---|---|
| `/workspace/...` | bot 的工作区（资源文件）。用户上传的 skill 位于 `/workspace/skills-local/<skill 名>/`。 |
| `/identity/...` | 身份文件（`AGENTS.md`、`RULES.md`、`MEMORY.md`、`IDENTITY.md` 等）。 |
| `/config/...` | 引擎为该 bot 保存的自有配置（如果开放的话），例如 `/config/<engine>.json`。 |

请把它们映射到引擎内的真实位置，并拒绝越出命名空间的路径（例如含 `..`）。

### 接口

所有请求均为 `POST`。

| 接口 | 请求 | 成功响应 |
|---|---|---|
| `/api/v1/file/upload` | multipart：`file`（文件内容）与 `target_path`（表单字段，以它为准） | `2xx`。自动创建父目录；文件已存在则覆盖。 |
| `/api/v1/file/read` | JSON `{"file_path": "/workspace/a.txt"}` | `2xx`，响应体为**文件原始字节**。文件不存在返回 `404`。 |
| `/api/v1/file/list` | JSON `{"dir_path": "/workspace", "recursive": false}` | `2xx`，返回 `{"success": true, "data": {"files": [...]}}`（见下）。 |
| `/api/v1/file/remove` | JSON `{"target_path": "/workspace/old"}` | `2xx`。删除文件**或整个目录树**。 |
| `/api/v1/file/skill-package` | multipart：`file`（`.zip`）、表单字段 `skills_name`、请求头 `x-target-bot-id` | 见下。 |

**列表条目。**`data.files` 中的每一项：

```json
{ "name": "faq.csv", "path": "/workspace/data/faq.csv",
  "relative_path": "data/faq.csv", "size": 1024, "is_dir": false }
```

**skill 包上传。**把 zip 解压到 `/workspace/skills-local/<skills_name>/`，替换
原有内容。响应：

```json
{ "success": true, "skill_name": "weather", "action": "created",
  "sha256": "<上传 zip 的十六进制 sha256>",
  "target_path": "/workspace/skills-local/weather" }
```

`action` 取 `created`、`replaced` 或 `unchanged`。平台会校验 `skill_name` 与
`sha256` 是否与发送的一致。错误：包无效返回 `400`，包过大返回 `413`；bot 正在
发布时返回 `409` 并带 `"error": "publish_in_progress"`；拿不到发布锁时返回
`503` 并带 `"error": "publish_lock_unavailable"`。

---

## 第 6 步：实现运行时接口

这些接口支撑对话、会话管理、定时任务和 bot 管理。Backend 转发用户在工作台和公开
OpenAPI 中发起的请求；BaaS 用会话接口和对话 WebSocket 运行定时任务与协作任务。
引擎没有实现的接口，对应功能在你们的 bot 上就不可用。

以下是平台当前对外部引擎调用的全部单 bot 运行时接口。配置与文件接口见
[第 3 步](#第-3-步接收-bot-配置)和[第 5 步](#第-5-步实现文件接口)。

### 引擎信息

| 接口 | 调用方 | 用途与约定 |
|---|---|---|
| `GET /api/engine/status` | Backend | 引擎状态。原样返回给调用方（不要求信封）。 |
| `GET /api/engine/capabilities` | Backend | `{"success": true, "data": {"supported": [...]}}`：引擎支持的功能。也决定是否提供审批模式。 |
| `GET /api/engine/list` | Backend | 该运行环境中可用的引擎。 |
| `POST /api/engine/restart` | Backend | 重启引擎进程。请求体 `{"force": false}`。 |
| `GET /api/openclaw/default-config` | Backend | 引擎出厂默认配置。不支持则返回 `501`。 |
| `GET /api/models` | Backend | bot 可用的模型：`{"success": true, "data": {"models": [...], "total": n}}`。 |
| `GET /api/models/{model_id}` | Backend | 单个模型。 |
| `GET /api/nodes` | Backend | 连接到 bot 的节点。查询参数：`status`、`platform`、`limit` 等。 |
| `POST /api/approvals/mode/get` | Backend | 读取会话的审批模式。请求体 `{"session_key", "user_id"}`（原始会话 key）。 |
| `POST /api/approvals/mode/set` | Backend | 设置会话的审批模式。请求体 `{"session_key", "mode", "user_id"}`。 |

### 会话

| 接口 | 调用方 | 用途与约定 |
|---|---|---|
| `GET /api/sessions` | Backend、BaaS | 列出会话。查询参数：`user_id`、`agent_id`、`limit`、`offset`，可选 `session_key`、`engine`。 |
| `POST /api/sessions` | Backend、BaaS | 创建会话。请求体：`title`、`user_id`，可选 `model`、`cwd`、`agent_id`、`engine`、`uuid`。带 `uuid` 时**以它作为会话 ID**，并在 `data.id` 中原样返回。 |
| `GET /api/sessions/{session_id}` | Backend、BaaS | 读取单个会话。不存在时返回 `404`。 |
| `POST /api/sessions/{session_id}/update` | Backend | 更新会话。要修改的字段放在**查询参数**里，不在请求体中。 |
| `DELETE /api/sessions/{session_id}` | Backend | 删除会话。 |
| `GET /api/sessions/{session_id}/messages` | Backend、BaaS | 会话消息，从最新的窗口开始。查询参数：`limit`、`offset`。 |
| `DELETE /api/sessions/{session_id}/messages` | Backend | 清空会话消息。 |
| `GET /api/session-favorites` | Backend | 列出用户收藏的会话。查询参数：`user_id`、`agent_id`、`limit`、`offset`。 |
| `PUT /api/session-favorites/{session_id}` | Backend | 收藏会话。查询参数：`user_id`。 |
| `DELETE /api/session-favorites/{session_id}` | Backend | 取消收藏。查询参数：`user_id`。 |

会话对象包含 `id`、`title`、`user_id`、`agent_id`、`model`、`gmt_created`、
`gmt_modified`、`message_count`、`last_message`。消息对象包含 `id`、
`session_id`、`role`、`content`、`metadata`、`gmt_created`、`history_meta`。

### 会话文件

| 接口 | 调用方 | 用途与约定 |
|---|---|---|
| `POST /api/resource-materializations` | Backend | 把用户附带的文件放进会话工作区。请求体包含 `resource_id`、`session_id`、`workspace_relative_path`、`filename`、`size_bytes`、`content_hash`。返回 `{"success": true, "data": {"accepted": true}}`。 |
| `GET /api/resource-materializations/{resource_id}/content` | Backend | 流式返回已落地的文件。查询参数：`disposition`。 |
| `GET /api/session-files/{resource_id}/content` | Backend | 流式返回 bot 在会话中产生的文件。查询参数：`sessionKey`（原始值）、`disposition`。 |

### 定时任务（cron）

已发布的 bot 有多个实例时，Backend 会把每次调用发给所有实例。

| 接口 | 调用方 | 用途 |
|---|---|---|
| `GET /api/cron` | Backend | 列出定时任务。 |
| `GET /api/cron/running` | Backend | 列出正在运行的任务。 |
| `GET /api/cron/status` | Backend | 调度器状态。 |
| `POST /api/cron` | Backend | 创建任务。 |
| `GET /api/cron/{task_id}` | Backend | 读取任务。 |
| `PUT /api/cron/{task_id}` | Backend | 更新任务。已发布的 bot 只能启用或停用。 |
| `DELETE /api/cron/{task_id}` | Backend | 删除任务。 |
| `POST /api/cron/{task_id}/run` | Backend | 立即运行任务。查询参数：`force`（可选）。 |
| `GET /api/cron/{task_id}/runs` | Backend | 任务的运行历史。查询参数：`limit`。 |

### 对话 WebSocket：`/api/ws`

用户在工作台里通过这个 WebSocket 与 bot 对话，BaaS 也通过它运行任务。消息为
JSON 帧：

- 请求：`{"type": "req", "id", "method", "params"}`
- 响应：`{"type": "res", "id", "ok", "payload", "error": {"code", "message", "retryable"}}`
- 事件：`{"type": "event", "event", "payload"}`

| 方法（客户端 → 引擎） | 用途与约定 |
|---|---|
| `connect` | 握手。参数：`minProtocol`、`maxProtocol`（当前为 `3`）、`client`。返回 `ok: true`，并带 `payload.server` 与 `payload.features`。 |
| `chat.send` | 发送消息并运行 Agent。参数：`sessionKey`、`message`，可选 `permissionMode`、`timeoutMs`、`attachments`、`appId`、`traceId`，以及 `eval_id` 等元数据键。 |
| `chat.inject` | 向运行中的会话追加消息，不发起新的运行。参数同 `chat.send`。 |
| `chat.abort` | 中止会话当前的运行。参数：`sessionKey`，可选 `runId`。 |
| `interaction.resolve` | 回答 Agent 提出的问题或审批。参数：`interactionId`、`decision`，按需带 `answer` / `values` / `selectedOptions`。 |
| `mode_transition.resolve` | 回答模式切换请求。参数：`transitionId`、`decision`。 |

| 事件（引擎 → 客户端） | 约定 |
|---|---|
| `chat` | `payload.state` 为 `delta`、`final` 或 `error`。delta 带新增文本；`final` 带 `message.content`；`error` 带 `errorMessage` 与 `errorCode`。追加消息的 final 事件，其 `runId` 以 `inject-` 开头。 |
| `agent` | 流式细节：`payload.stream` 为 `assistant`、`tool` 或 `lifecycle`。 |
| `interaction.requested`、`interaction.resolved` | Agent 需要或已得到用户的回答，带 `interactionId`。 |
| `tick` | 保活。约每 30 秒发送一次；平台关闭了协议层 ping。 |

每个事件都必须带 `payload.sessionKey`。工作台的对话客户端还可能发送其他方法
（如 `chat.history`、`sessions.*`、`models.list`），完整列表与帧细节
**待与 Backend 确认**（O6）。

### 可选：A2A 传输

如果 BaaS 为任务运行开启了 A2A，它会改用以下接口而不是 WebSocket。除非 Avernet
团队要求，否则无需实现。

| 接口 | 用途 |
|---|---|
| `GET /.well-known/agent-card.json` | Agent 名片。必须列出协议版本为 `1.x` 的 `JSONRPC` 接口。 |
| `POST /rpc` | JSON-RPC：`SendStreamingMessage`（SSE 响应；`contextId` 即会话 key）、`CancelTask`、`InjectContext`、`ResolveInteraction`。 |

### 会话 ID

会话 ID 可能包含冒号（如 `agent:main:default:{run_id}:user:{user_id}`），而且按
调用方不同，以不同形式到达引擎。**所有形式都要接受**：

| 位置 | 形式 |
|---|---|
| 路径段，来自 Backend 公开 API | 去掉填充的 URL 安全 base64 |
| 路径段，来自 BaaS | 带填充的标准 base64（可能含 `+`、`/`、`=`） |
| 路径段，来自 Backend 专家对话 | 百分号编码（`:` 为 `%3A`） |
| 请求体或查询参数（`session_key`、`sessionKey`） | 原始值 |

BaaS 运行任务时会自己指定会话 ID，格式为
`agent:main:default:{run_id}:user:{user_id}`。它先读取该会话，失败则调用
`POST /api/sessions` 并把 ID 放在 `uuid` 中创建，再通过 WebSocket 对话。

### 当前还会被调用的接口

| 接口 | 调用方 | 说明 |
|---|---|---|
| `POST /api/mcp/call-tool` | Backend | 通过 bot 的 MCP 工具解析语雀文档链接。请求体 `{"tool", "args": ["url=..."]}`。 |
| `POST /api/cron/auto-initiate/run-single` | Backend | 发起一个工作项任务，供编码类 bot 使用。 |
| `/api/v1/bot/cron`、`/api/v1/bot/cron/{task_id}`、`.../run`、`.../runs` | Avernet 修复工具 | 上文 cron 操作在 teclaw 上的现有路径。是否保留**待与 Backend 确认**。 |

平台对标准运行时接口的参考实现是 Avernet 引擎适配层（`engine/adapter/`，见其
[开源指南](../../../../engine/adapter/docs/open-source-guide.zh-CN.md)）。

---

## 第 7 步（可选）：加入群体协作（BCN）

要让 bot 参与多 bot 群聊、路由与协作，需要让每个 bot 通过 WebSocket 连接 Bot
协作网络（BCN）。这一步与其他步骤相互独立，在
[Bot 接入指南](../../../../docs/bot-integration.zh-CN.md)中有完整说明和可运行的
快速入门。

---

## 第 8 步：联调自查

上线前请逐项检查。Avernet 团队联调时也会验证同样的项目。

**生命周期（BaaS）**

- [ ] 创建后 bot 启动时第一份 artifact 已完整应用，随后发送 `SUCCESS` 回调。
- [ ] 更新与重启会应用配置并发送回调。
- [ ] 操作失败时发送 `success: false`、`task_status` 为 `FAILED` 的回调。
- [ ] 每个回调都原样返回 `callback_context`。
- [ ] 删除会停止并移除 bot。
- [ ] 查询返回 bot 的状态和当前配置。

**应用 artifact**

- [ ] 应用后，artifact 不再列出的内容已消失（R1）。
- [ ] 每次应用后 `MEMORY.md` 与 `IDENTITY.md` 都还在且未被修改，包括
      `identity_files` 未列出它们的情况（R2）。
- [ ] `resources` 从 `[data/a.csv]` 变成 `[]` 后，`data/a.csv` 被删除。
- [ ] 所有资源整体移到新目录后，旧目录下没有残留。
- [ ] 用户或模型在工作区创建的文件从未被删除。
- [ ] 同一份 artifact 连续应用两次，第二次没有任何变化（R3）。
- [ ] 整份 artifact 应用完成后 bot 才报告就绪（R4）。
- [ ] 含未知字段的 artifact 被正常接受（R5）。
- [ ] `ownership.resources = "engine"` 时，`resources` 列表不产生任何效果。
- [ ] `ownership.identity_files = "platform"` 且列表为空时，除 `MEMORY.md` 与
      `IDENTITY.md` 外的身份文件全部被移除。
- [ ] 不带 `ownership` 的 artifact 行为与以前完全一致。

**CLI 工具**

- [ ] Agent 可以按 `name` 直接调用每个工具。
- [ ] `md5` 未变的工具不会重复下载；`md5` 变化时文件被替换。
- [ ] 从列表中移除的工具无法再被调用。
- [ ] `"cli_tools": []` 移除所有平台下发的工具，且不影响镜像自带命令。

**文件与运行时**

- [ ] 在 `/workspace` 与 `/identity` 下上传、读取、列出、删除均正常。
- [ ] 读取不存在的文件返回 `404`。
- [ ] `remove` 能删除整个目录。
- [ ] 会话路由接受第 6 步列出的所有会话 ID 形式（原始值、百分号编码、去掉填充
      的 URL 安全 base64、标准 base64）。
- [ ] `POST /api/sessions` 带 `uuid` 时以该 ID 创建会话，并在 `data.id` 中返回。
- [ ] 第 6 步中的每个运行时接口都能应答；不支持的功能返回 `501`。
- [ ] `/api/ws` 对话 WebSocket 能完成 `connect`，对 `chat.send` 流式返回 `chat`
      事件，并在 `chat.abort` 后停止。

---

## 通用约定

**JSON 信封。**JSON 接口返回：

```json
{ "success": true, "data": { "...": "..." }, "message": "可选说明" }
```

请求失败时返回 `"success": false` 并附 `message`（可选附 `error` 错误码）。
**即使 HTTP 状态为 `200`，平台也把 `"success": false` 视为失败。**例外是
`/api/v1/file/read`（返回原始字节）和 `/api/v1/file/skill-package`（字段位于
顶层）。

**错误码。**输入无效用 `400`，路径被禁止用 `403`，对象不存在用 `404`，引擎
内部故障用 `5xx`。

**安全。**artifact 和部分文件内容可能包含用户的敏感信息。请使用 HTTPS，不要
完整打印请求内容，不要明文保存凭证。

---

## 待确认事项

上线前需与 Avernet 团队确定：

| # | 事项 | 负责方 |
|---|---|---|
| O1 | 自托管引擎如何注册生命周期接口地址与单 bot 地址。 | BaaS |
| O2 | BaaS 与 Backend 如何向引擎鉴权，以及引擎回调 BaaS 时如何鉴权。 | BaaS |
| O3 | 一个服务承载多个 bot 时，一次请求如何指定某个 bot。 | BaaS |
| O4 | 生命周期接口的路径与请求、响应体，以及回调的 `schema_version` 取值。 | BaaS |
| O5 | 引擎如何获得内容存储的读权限。 | Backend |
| O6 | 运行时接口响应结构及对话 WebSocket 协议的书面规范。 | Backend、引擎 |

---

## 参考文档

| 文档 | 内容 |
|---|---|
| [`artifact.schema.json`](../../src/agentclaw/community/kernel/bot_config/artifact.schema.json) | artifact 的正式定义。 |
| [跨引擎收敛语义契约](../bot-config-manifest/engine-convergence-contract.zh-CN.md) | 第 3 步应用规则的完整论证。 |
| [CLI 工具契约](../bot-config-manifest/teclaw-cli-contract.zh-CN.md) | `cli_tools` 的完整契约及用例。 |
| [Bot 接入指南](../../../../docs/bot-integration.zh-CN.md) | 将 bot 接入 BCN 参与群体协作。 |
| [引擎适配层开源指南](../../../../engine/adapter/docs/open-source-guide.zh-CN.md) | 运行时接口的参考实现。 |
