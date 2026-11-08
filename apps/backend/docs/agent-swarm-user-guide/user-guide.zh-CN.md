# Agent Swarm — 用户指南

> **接口面：** `/openapi/v1/agentswarm` 下的 HTTP API（版本 `v1`，只做增量演进）。
> **代码权威：** 社区后端 [`agentswarm/router.py`](../../src/agentclaw/community/adapters/http/agentswarm/router.py) + [`schemas.py`](../../src/agentclaw/community/adapters/http/agentswarm/schemas.py)。
> **配置清单权威：** [`bot-config-manifest/manifest-schema.zh-CN.md`](../bot-config-manifest/manifest-schema.zh-CN.md)（schema v1 语法）与 [`examples.zh-CN.md`](../bot-config-manifest/examples.zh-CN.md)。
> **语言说明：** 本文件是 [`user-guide.md`](./user-guide.md)（英文）的配套翻译；英文版本为唯一事实来源（source of truth），两版如有出入以英文版为准。
> **最近一次对照代码核验：** 2026-10-21。

本指南演示如何使用 Agent Swarm API：提交一次运行、观察其装配与执行、读取最终结果；并解释每个 Agent 条目里的 `config_manifest` 字段 —— 一份完整的 Bot 配置清单文档 —— 以及该文档语法所属的 with-manifest 创建接口面。

> **术语说明**：本翻译沿用仓库自身中文文档的既有词汇 —— 类别/条目/命名源/凭证/收敛/覆盖/启动脚本等取自 [`bot-config-manifest/manifest-schema.zh-CN.md`](../bot-config-manifest/manifest-schema.zh-CN.md)；引擎/集群/响应封套等接口面术语取自 Avernet 仓库 `docs/bot-create-api-guide.zh-CN.md`。

---

## 1. 一次 Agent Swarm 运行是什么

一次**运行（run）**用你预先声明的一组 **Agent** 去完成一个 `goal`：

- `kind: "llm"` —— 进程内补全人格。快（秒级），不起容器。
- `kind: "bot"` —— 真实装配出来的 Bot 人格：平台现场创建一个 Bot（可由你的 `config_manifest` 决定其配置），该 Agent 即以这个 Bot 运行；运行结束时平台会将其删除。

编排器对目标做分解，按轮次生成/执行 Agent（并行波次或前后依赖，由目标文本决定），最后产出 `final_answer`。运行终结于 §6 的某个终态；在那之前的一切都可以通过轮询或 SSE 实时流观察。

首次调用前必须知道的规则：

| 主题 | 规则 |
| --- | --- |
| 鉴权 | 每个路由都要求解析出**已认证的终端用户** —— 匿名调用得到 `401`。只有 `/health` 和 `/ready` 对探针开放。 |
| 租户 | 租户是**显式的每请求参数**：提交时是 body 里的 `tenant` 字段，其余路由都是 `?tenant=` 查询参数。留空在校验处被拒（`422`）。跨租户访问在数据层被拒，且与"不存在的运行"不可区分（两种情况都是 `404`）。 |
| 版本策略 | 接口面为 `/openapi/v1/agentswarm`，在 `v1` 内只做增量兼容。改名/删除必须换新的 `/v2/` 前缀。 |
| 幂等 | 相同 `idempotency_key`（配相同提交内容）会**并入同一次运行**，而不是开启第二次。 |

## 2. Base URL 与凭证

所有示例使用 `BASE` 变量：

```bash
# 本地 singlebox 环境
BASE=http://127.0.0.1:8000/openapi/v1/agentswarm

# …或经由你所在部署的网关主机
BASE=http://<gateway-host>/openapi/v1/agentswarm
```

鉴权方式因部署形态而异：网关部署从请求主体解析终端用户（例如浏览器会话 Cookie；若你的鉴权提供方校验 referer，还需带匹配的 `Referer`/`Origin`）；本地 singlebox 形态则解析匿名或开发主体。无论哪种形态，未认证调用在除 `/health`、`/ready` 外的每个路由都会收到 `401`。

## 3. 五分钟快速上手

提交一个 `llm` Agent，然后轮询到终态：

```bash
# 1. 提交 —— 返回 {"run_id": "..."}
curl -sS -X POST "$BASE/runs" -H 'Content-Type: application/json' -d '{
  "tenant": "teamclaw",
  "goal": "Say hello world",
  "agents": [
    {"name": "greeter_bot", "role": "hello world producer", "kind": "llm",
     "instructions": "Respond with exactly the single word: hello world"}
  ],
  "options": {"model_name": "DeepSeek-V4.1-Flash"}
}'
# → {"run_id":"a1b2c3…"}

# 2. 轮询，直到状态进入 §6 的终态集合
curl -sS "$BASE/runs/$RUN_ID?tenant=teamclaw"
# → {"run_id":"a1b2c3…","status":"COMPLETED","termination_reason":"finish",
#    "final_answer":"hello world","error":null,"started_at":…,"ended_at":…}
```

想阻塞等待？加 `?wait=true`，同一次调用会等到运行终态后才返回完整摘要（`llm` 运行通常数秒内完成）。

## 4. 接口索引

| # | 方法 | 路径 | 用途 |
| --- | --- | --- | --- |
| 1 | POST | `/runs` | 提交运行（异步；`?wait=true` 同步） |
| 2 | POST | `/runs/{run_id}/cancel` | 请求取消 |
| 3 | GET | `/runs/{run_id}` | 运行摘要 |
| 4 | GET | `/runs` | 运行列表（分页、可过滤） |
| 5 | GET | `/runs/{run_id}/detail` | 完整明细：Agent、轮次、护栏、消息 |
| 6 | GET | `/runs/{run_id}/events` | SSE 实时事件流 |
| 7 | GET | `/runs/{run_id}/report` | HTML 报告页 |
| 8 | GET | `/health` | 存活探针（免鉴权） |
| 9 | GET | `/ready` | 就绪探针（免鉴权） |

## 5. 接口详解

### 5.1 提交运行 — `POST /runs`

请求体（`SubmitRunDTO`，严格模式 —— 未知字段即 `422`）：

| 字段 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `tenant` | string | ✅ | 1–64 字符 |
| `goal` | string | ✅ | ≤ 8,000 字符；编排器将分解的目标 |
| `agents` | array | ✅ | 1–64 个条目（见下表） |
| `provider` | enum | — | `default` \| `stub` \| `avernet` |
| `model` | string | — | 运行级模型覆盖；**优先级高于** `options.model_name` |
| `idempotency_key` | string | — | ≤ 190 字符；同键并入同一运行 |
| `options` | object | — | 运行参数覆盖（见下表） |

每个 `agents[]` 条目（`AgentSpecDTO`，严格模式）：

| 字段 | 类型 | 必填 | 约束 / 默认值 |
| --- | --- | --- | --- |
| `name` | string | ✅ | ≤ 128 字符 |
| `role` | string | ✅ | ≤ 256 字符 |
| `instructions` | string | — | ≤ 20,000 字符；人格文本 |
| `kind` | enum | — | `llm`（默认）\| `bot` |
| `model` | string | — | 该 Agent 专属模型 |
| `engine` | string | — | Bot 类 Agent 的服务引擎 |
| `bot_type` | string | — | 默认 `personal` |
| `identity_file` | string | — | 默认 `SOUL.md` |
| `metadata` | object | — | ≤ 32 个键值；键 ≤ 200 字符，值 ≤ 2,000 字符 |
| `config_manifest` | string | — | **≤ 200,000 字符。** 一份完整的 Bot 配置清单 YAML 文档 —— 见 §8 |

`options`（`RunOptionsDTO`，严格模式）—— 未指定的取值回退到部署配置
`agentswarm.orchestration.*`；部署方始终保留最终裁量权，分三种语义：

- **回退（fallback）** —— `model_name` 为未自带模型的 Agent 指定模型，
  省略时使用部署默认。
- **只能收紧（tighten-only）** —— `max_width`、`max_attempts`、
  `slice_max_seconds`、`per_attempt_timeout_seconds` 会被部署配置值
  钳制：请求值只能更小，不能超过部署上限。
- **可放宽至切片余量（raise-to-slice-room）** —— `setup_timeout_seconds`
  为单个 Agent 的供给阶段（生成 + 授权等待 + READY）重新定预算：
  部署的 `setup_timeout` 是回退值，显式请求值在**部署留下的切片余量**
  （`slice_max_seconds − per_attempt_timeout`）之内直接生效。在人工
  授权窗口停等的时间不计入该预算。

`max_turns` 与 `max_total_agents` 会随运行持久化，但不会覆盖部署方的
编排预算。

| 字段 | 范围 | 含义 |
| --- | --- | --- |
| `model_name` | ≤ 128 字符 | 未自带模型的 Agent 使用的模型 |
| `slice_max_seconds` | (0, 3600] | 单次运行的切片截止时间（只能收紧） |
| `per_attempt_timeout_seconds` | (0, 600] | 单次 Agent 尝试上限（只能收紧） |
| `setup_timeout_seconds` | (0, 900] | Agent 供给预算（可放宽至部署切片余量） |
| `max_turns` | [1, 50] | 仅持久化 —— 由部署预算管辖 |
| `max_total_agents` | [1, 64] | 仅持久化 —— 由部署预算管辖 |
| `max_width` | [1, 16] | 最大同时在飞的 Agent 数（只能收紧） |
| `max_attempts` | [1, 3] | 单 Agent 全新上下文重试上限（只能收紧） |

响应：

- **`200`** —— `{"run_id": "…"}`（默认的异步提交）。
- **`200`（带 `?wait=true`）** —— 完整运行摘要（§5.3 的结构），在运行终态或截止后返回。
- **`429`** —— 准入被拒（见 §7）。**`422`** —— 结构或校验错误。
  提交（无论接受或拒绝）都会落入只追加的审计表（`ac_agent_swarm_audit`）。

### 5.2 运行列表 — `GET /runs?tenant=…`

查询参数：`page`（≥ 1）、`page_size`（1–100，默认 20）、可选 `status`（一个 `RunStatus` 值）。返回
`{"items":[摘要…], "total":…, "page":…, "page_size":…}`。

### 5.3 单个运行 — `GET /runs/{run_id}?tenant=…`

返回运行摘要：

```json
{"run_id":"…", "status":"COMPLETED", "termination_reason":"finish",
 "final_answer":"hello brave new wide world", "error":null,
 "started_at":1760000000, "ended_at":1760000012}
```

不存在的运行（或他人租户的运行）：`404 {"detail":"run not found"}`。

### 5.4 完整明细 — `GET /runs/{run_id}/detail?tenant=…`

摘要之外的全部记录：`goal`、`provider`、声明用的 `agent_catalog`、每个生成出来的 Agent（bot 类含 `bot_id`/`bot_state`、其 `attempts`、完整 `attempt_ledger`、以及含编排器决策与 `token_usage` 的 `messages`）、轮次 `rounds`（阶段索引、同批 Agent）、以及 `guards` 护栏事件。

### 5.5 取消 — `POST /runs/{run_id}/cancel?tenant=…`

`200 {"run_id":"…","status":"RUNNING"}` —— `status` 是取消请求发出后该运行的即时状态（编排器观察到请求并结束运行，最终落为 `CANCELLED`）。不存在的运行：`404`。

### 5.6 实时事件流 — `GET /runs/{run_id}/events?tenant=…`

Server-Sent Events；每行 `data:` 都是
`{"event":…, "run_id":…, "data":…}`：

| 事件 | `data` 载荷 | 含义 |
| --- | --- | --- |
| `status` | `{"status":"RUNNING"}` | 流开始时发一次 |
| `agent` | `{agent_id, name, status, kind, spawn_round}` | 每个到达的 Agent 游标一条（新连接会从 0 回放） |
| `round` | `{round_id, stage_index, cohort_size}` | 轮次同批 Agent |
| `guard` | `{"kind":"width_exceeded"}` | 触发了一条护栏（见 §6） |
| `progress` | `{"status":"RUNNING"}` | 增量之间约 1 Hz 的心跳 |
| `terminal` | `{"status":"COMPLETED"}` | 终态 —— 流随之关闭；运行存活期间 `id: terminal` 帧还指示读取方每 5 秒重连 |

```bash
curl -sS -N "$BASE/runs/$RUN_ID/events?tenant=teamclaw"
event: status
data: {"event":"status","run_id":"…","data":{"status":"RUNNING"}}
…
event: terminal
id: terminal
data: {"event":"terminal","run_id":"…","data":{"status":"COMPLETED"}}
```

每条 agent/round/guard 事件在一条流内只发一次（按持久行游标排序）—— 新连接先回放运行至今的全部事件，再进入尾随模式。

### 5.7 报告与探针 — `/report`、`/health`、`/ready`

- `GET /runs/{run_id}/report?tenant=…` —— 一个小型 HTML 页面（运行 ID、目标、状态、终止原因、Agent 数），适合贴进工单；响应头带 `Cache-Control: no-store`。
- `GET /health` —— 免鉴权 `{"status":"ok","version":"v1"}`；`version` 是供机群固定版本的机器可读关键字。
- `GET /ready` —— 免鉴权 `{"ready":true}`。

## 6. 状态词汇表

**运行状态（`status`）** —— 第一个为非终态，其余为终态：

| 状态 | 含义 |
| --- | --- |
| `RUNNING` | 进行中 |
| `COMPLETED` | 完成；`final_answer` 即答案 |
| `FAILED` | 因错误终止 |
| `CANCELLED` | 取消请求已被观察到 |
| `TIMED_OUT` | 因截止时间终止 |
| `GUARD_KILLED` | 因护栏（如宽度/深度）终止 |
| `STUCK` | 长时间无进展，被关闭 |

**终止原因（`termination_reason`）**：`finish`、`no_decision`、`no_progress`、`turn_budget`、`max_agents`、`wall_clock`、`cancelled`、`stuck`、`error`、`guard`。

**Agent 状态**（明细与 SSE 中）：`QUEUED`、`RUNNING`、`COMPLETED`、`FAILED`、`KILLED`。

**Bot 状态**（bot 类 Agent，明细的 `agents[].bot_state`）：
`PROVISIONING`、`READY`、`AUTHORIZATION_REJECTED`、`AUTHORIZATION_EXPIRED`、`CREATE_FAILED`、`APPLY_FAILED` —— 即人格 Bot 自身的创建生命周期（见 §8.5）。

**护栏 kinds**：`serial_collapse`、`spurious_parallelism`、`dedupe_reject`、`depth_exceeded`、`width_exceeded`、`timeout`、`kill`、`stuck`、`prompt_injection`。示例：提交 10 个 Agent 并设 `options.max_width = 3`，编排器每次尝试更宽的波次被压回时，事件流里就会出现 `width_exceeded` 护栏。

## 7. 错误模型

| HTTP | 响应体（错误键） | 原因 |
| --- | --- | --- |
| 401 | — | 非探针路由上的匿名调用 |
| 404 | `{"detail":"run not found"}` | 运行 ID 不存在**或**跨租户访问（设计上就是同一个回答） |
| 400 | `{"error":"…"}` | 请求的模型/提供方缺少服务配置 |
| 422 | 校验错误明细 | 结构错误：未知字段、超限、租户留空 |
| 429 | `{"error":"tenant_run_limit_exceeded","tenant":…,"limit":…}` | 该租户并发运行过多 |
| 429 | `{"error":"global_run_limit_exceeded","limit":…}` | 全局并发运行达到上限 |
| 429 | `{"error":"queue_depth_exceeded","limit":…}` | 派发队列已满 |

## 8. Bot 类 Agent：从 `config_manifest` 装配

### 8.1 这个字段是什么

`agents[].config_manifest` 接受一份**完整的 Bot 配置清单**（schema v1）：
`manifest:` 块下的五个内容类别 `mcp`、`resources`、`skills`、`cli_tools`、`identity`，加上命名源 `sources:` 与命令式的 `script:` —— 与
`POST /openapi/v1/bots/with-manifest` 接受的是同一份文档（见 §8.5）。

提交路径上的行为：

- **有文档** —— 平台在提交时即校验（"预检"，与 with-manifest 接口面同一套校验、同一个 `422` 全量违规清单），并把人格 Bot 从该文档**逐字**装配出来：`role`/`instructions`/`identity_file` 照常作为规格字段传递，但 Bot 携带的是清单自身 `identity` 类别的内容。被接受的文档在提交时刻即持久存储（回执落在 `ac_bot_config_manifest`）。
- **留空（默认）** —— 平台依据 `identity_file` + `instructions` 合成一份仅含 identity 的文档。
- 人格 Bot 经共享后台队列装配 —— 请按**分钟**级预留时间，选择流式或轮询而非紧凑的客户端截止时间。在列表里人格 Bot 以其 Agent 的名字出现；运行终态时平台的收尾清理会将其软删除。

### 8.2 文档一览

```yaml
schema_version: 1

sources:                      # 命名源：一处声明，处处引用
  content:
    protocol: git             # git | oss
    url: https://code.example-corp.com/team/content.git
    ref: v1.2.0
    auth: corp-git-content    # 命名凭证 —— 文档内绝不内联机密

manifest:                     # 声明式类别 —— 所有引擎
  mcp: [ … ]                  # MCP 服务器（server_code + 可选 config）
  resources: [ … ]            # 工作区文件与目录树
  skills: [ … ]               # 本地技能包
  identity: [ … ]             # 人设/规则文件（SOUL.md、SAFETY.md、…）
  cli_tools: [ … ]            # 平台安装的命令行工具

script:                       # 命令式部分（与 manifest 平级，不在其内）
  body: |
    echo provisioned
```

**收敛策略是类别级覆盖** —— 语法里最容易踩坑的一段：

| 写法 | Apply 做什么 |
| --- | --- |
| 类别缺席 | 完全不碰该类别 |
| 类别非空 | 把该类别的**区域**收敛到恰好等于声明（区域内**移除**未声明的条目） |
| 类别存在但为 `[]` | 空集合也是声明 —— 移除区域内的全部条目 |
| 整份文档删除 | 什么都不删 —— 已落成的内容原样留在 Bot 上 |

每个类别是**全有或全无**的写入：只要任一条目无法物化，该类别完全不覆盖（其它类别不受牵连）。一次部分落地的 apply 报告为 `APPLY_FAILED`，绝不报告"带脚注的成功" —— 覆盖语义下，不完整的集合是破坏性的。

### 8.3 条目 —— 共享的取源机制

每个内容条目**恰好**以 `from` / `source` / `content` 三者之一指定来源（互斥），外加共享可选字段：

| 字段 | 规则 |
| --- | --- |
| `from: <name>` | 引用一个顶层 `sources` 命名源；凭证声明在源上而非条目上 |
| `source: {protocol: git …}` | 内联源对象：`git` 带 `url` + `ref`（另可 `mode`、源自身 `subpath`、`auth`）；`oss` 带 `bucket` + `key`（另可 `auth`）。裸 URL 字符串会被拒绝并给出替代写法 |
| `content: \|…` | 内联 UTF-8 文本，≤ 64 KiB。`auth`/`digest`/`on_fetch_failure` 对它非法（没有取源） |
| `subpath` | `git`：交付树内的路径；`oss`：**解包后归档内的成员** —— 不解包的条目上写它会被拒绝 |
| `digest: sha256:…` | **仅 oss** —— 拉取字节必须匹配，否则按取源失败处理。oss 上的 `skills` 与 `cli_tools` **必填**；git 上非法（commit SHA 即天然钉扎） |
| `auth: <name>` | 一个命名的租户级凭证；取源时在其存储前缀范围内出示 |
| `on_fetch_failure` | `keep_last`（默认 —— 复用平台上一份成功物化的副本）\| `fail`（该类别在写入前整体放弃） |
| `unpack` / `strip_components` | oss 归档（`zip` \| `tar.gz`）；git 上非法；oss 目录资源与技能包必填 |
| `apply_once` | v1 保留字 —— 出现在任何深度都被拒绝 |

各类别的合法键（其余一律按 `unknown_field` 拒绝）：

- **`mcp`** —— `server_code`（MCP Center 注册表 ID）+ 可选 `config`
  （`url`、非敏感的 `headers`、`endpoint_env` `PROD|PRE`、
  `transport_protocol` `SSE|STREAMABLE_HTTP`）。注册全新 MCP 身份不在配置清单契约之内。
- **`resources`** —— `path`（工作区相对路径；以 `/` 结尾即为目录条目，拥有该子树）。
- **`skills`** —— `name`。
- **`identity`** —— `type`（如 `SOUL.md`、`SAFETY.md`、`RULES.md`、…）；
  `MEMORY.md` 与 `IDENTITY.md` **写入即拒**（引擎运行时生成状态 —— apply 必须保持可收敛）。
- **`cli_tools`** —— `name` + 可选 `version`：一条目 = 一命令 = 一文件（用 `subpath` 在拉取的归档内选中它）。
- **`script.body`** —— 启动命令，受启动脚本上限约束（24 KiB）。逐构造的可用性由 with-manifest 接口面发布的同一能力解析器决定 —— 例如 teclaw 类引擎上的 `script` 以 `unsupported_script` 拒绝；没有任何物化器能落地的类别（当前：`engine_config`）在提交处即被拒，而不是存成死配置。

**`${BOT_*}` 变量替换**（白名单，取源与凭证前缀校验之前完成替换）：`${BOT_ENGINE_TYPE}`、`${BOT_ENV}`、`${BOT_TENANT}`、`${BOT_ARCH}`（当前值：`amd64`）。刻意不提供 `${BOT_ID}`。

### 8.4 限额与出处

| 限额 | 数值 |
| --- | --- |
| 配置清单文档整体 | 64 KiB（UTF-8） |
| 每个类别的条目数 | 50 |
| 单个内联 `content` 块 | 64 KiB |
| 源 URL | 2,048 字符 |
| `script.body` | 24 KiB |
| `config_manifest` 字段本身（swarm 提交 DTO） | 200,000 字符 |
| 取源期上限（每条目 / 每次 apply） | 由取源侧执行，而非提交侧 —— 单对象字节上限、解包树规模、逐跳超时都属于取源侧机制 |

> **核验出处**：本节对照 `docs/bot-config-manifest/manifest-schema.zh-CN.md`
> （§1–§5）、`adapters/http/agentswarm/router.py` 与
> `adapters/http/agentswarm/schemas.py`，核验于 2026-10。本压缩参考未覆盖的一切以语法权威文档为准。

### 8.5 姊妹接口面：用清单创建单个 Bot

swarm 的配置清单走的是单 Bot 创建接口面定义的同一套契约 —— 两边都值得了解，因为 `agents[].bot_state` 里的生命周期状态正是这里的词表：

**`POST /openapi/v1/bots/with-manifest`** —— 一次请求同时创建 Bot 及其配置。请求体：普通创建字段（`bot_name`、`bot_desc`、`engine`（如 `openclaw`）、`cluster_name`（如 `ACRA`）、`bot_type`（如 `personal`））加上 `config_manifest`。清单**在任何消耗发生之前**即被校验：非法文档得到逐条列出全部违规的 `422`，此时没有铸造任何 bot ID、没有存储任何东西。成功总是 `202` 响应封套：

```json
{"code": 202000, "message": "OK",
 "data": {"bot_id": "20260813_a7k2m9p1",
          "iframe_url": "https://auth.example.com/passport/consent?flow=f-1",
          "redirect_url": ""},
 "request_id": "…"}
```

把用户引导到任一非空的授权 URL（鉴权提供方返回其一，不可预测）。然后轮询：

**`GET /openapi/v1/bots/{bot_id}/with-manifest/status`** →
`{"state": …, "bot_id": …, "bot": …?, "apply": …?, "message": …}`，其中：

| 状态 | 含义 |
| --- | --- |
| `AWAITING_AUTHORIZATION` | 尚无任何东西 —— 引导用户打开授权 URL |
| `AUTHORIZATION_REJECTED` / `AUTHORIZATION_EXPIRED` | 终态 —— **未创建任何 Bot**；已存储的清单随创建一起删除 |
| `CREATING` | 已授权；Bot 记录已建立，容器装配中 |
| `CREATE_FAILED` | 终态 —— 没有可用的 Bot（与清单无关） |
| `APPLYING` | Bot 已就绪；容器后清单阶段执行中 |
| `READY` | 终态 —— Bot 就绪且整份清单全部落地 |
| `APPLY_FAILED` | 终态 —— **Bot 已存在且在运行**，但部分配置未落地。响应同时带 Bot 与逐条目的 apply 报告；修复清单后重新 apply 即可，无需重建 |

终态会同时返回 Bot 和逐条目的 apply 报告（类别、条目、动作
`created`/`updated`/`unchanged`/`skipped`/`failed`，以及被覆盖移除的
`removed` 条目）。与普通 `PUT …/config-manifest` 不同 —— 后者可以接收
暂时无人能落地的构造 —— 该创建接口面**直接拒绝**：接受它意味着用户
先授权、Bot 先建好，最后才发现配置不上。

### 8.6 一份完整的配置清单样例

完整样例（取自平台自身的契约示例
`api-contracts/genome/v1/examples/manifest-v1/01-complete/` —— 略去
`engine_config` 类别，v1 对其直接拒绝）：

```yaml
schema_version: 1

sources:
  content:
    protocol: git
    url: https://code.example-corp.com/team/content.git
    ref: v1.2.0
    auth: corp-git-content
  order-lookup:
    protocol: oss
    bucket: artifacts
    key: tools/skills/order-lookup-1.4.0.zip
    auth: oss-artifacts
  shopctl:
    protocol: oss
    bucket: artifacts
    key: tools/shopctl/2.3.0/shopctl-linux-amd64
    auth: oss-artifacts

manifest:
  identity:
    - type: SOUL.md
      from: content
      subpath: bots/support-agent/soul.md
    - type: RULES.md
      from: content
      subpath: kb/service-rules.md
    - type: SAFETY.md
      content: |
        Support safety boundaries: never promise refund amounts;
        escalate money-loss cases to a human.

  resources:
    - path: data/faq.csv
      from: content
      subpath: kb/faq.csv
      on_fetch_failure: keep_last
    - path: data/kb/
      from: content
      subpath: kb/
      on_fetch_failure: keep_last

  skills:
    - name: quality-check
      from: content
      subpath: skills/quality-check/
    - name: order-lookup
      from: order-lookup
      digest: "sha256:896e1cb125db379d13790e045213f16bb8c936b29ff0b0d6ca33b1ea037c359f"
      unpack: zip

  cli_tools:
    - name: shopctl
      from: shopctl
      digest: "sha256:d89a79825b1468374f2d0a5cdc5c856694730e6355624d0b2f925db5202a68a0"
      version: "2.3.0"

  mcp:
    - server_code: mcp.ant.homistudio.meetmcp

script:
  body: |
    #!/bin/bash
    set -euo pipefail
    curl -fsSL http://inner-ops.example.com/whitelist/today.json \
      -o "$HOME/workspace/data/whitelist.json"
```

## 9. 端到端演练

一次完整的双 Agent 运行 —— 一个 `llm`，外加一个**配置随请求携带**的
Bot Agent —— 从提交到报告：

```bash
BASE=http://<gateway-host>/openapi/v1/agentswarm

# 1. 提交；agents[1] 携带完整 config_manifest（§8.6）
curl -sS -X POST "$BASE/runs" -H 'Content-Type: application/json' -d '{
  "tenant": "teamclaw",
  "goal": "Greet the user politely and confirm the greeting was logged.",
  "agents": [
    {"name": "s1", "role": "greeting reviewer", "kind": "llm",
     "instructions": "Reply with exactly: greeting ok"},
    {"name": "s2", "role": "manifest-configured greeter", "kind": "bot",
     "instructions": "Greet the user politely.",
     "identity_file": "SOUL.md",
     "config_manifest": "schema_version: 1\nmanifest:\n  identity:\n    - type: SOUL.md\n      content: \"You are a meticulous greeter.\"\n  mcp: []\n  resources: []\n  skills: []\n  cli_tools: []\nscript:\n  body: \"echo provisioned\"\n"}
  ],
  "options": {"model_name": "DeepSeek-V4.1-Flash"}
}'
# → {"run_id":"a1b2c3…"}；记为 RUN_ID

# 2. 轮询 —— GET /runs/{run_id}?tenant=… 直到状态进入 §6 终态集合
curl -sS "$BASE/runs/$RUN_ID?tenant=teamclaw"

# 3. 观察 —— 每条 Agent 事件都会经 SSE 回放，含护栏宽度封顶
curl -sS -N "$BASE/runs/$RUN_ID/events?tenant=teamclaw"

# 4. 查看台账 —— 决策/消息/轮次，Bot Agent 的 bot_id 与 bot_state、编排器决策轮
curl -sS "$BASE/runs/$RUN_ID/detail?tenant=teamclaw"

# 5. 人类可读报告
curl -sS "$BASE/runs/$RUN_ID/report?tenant=teamclaw"
```

Bot Agent 的几点说明：其人格 Bot 经共享后台队列装配（按**分钟**而非秒计 —— 见 §8.1）；清单回执在提交应答的那一刻就已存在，装配慢不会使文档失效；运行结束时平台收尾会删除人格 Bot。

## 10. 相关文档

- 模块内部结构、数据表、配置项与运维手册：
  [`core/agentswarm/README.md`](../../src/agentclaw/community/core/agentswarm/README.md)
- 配置清单语法（权威）与完整示例：
  [`bot-config-manifest/manifest-schema.zh-CN.md`](../bot-config-manifest/manifest-schema.zh-CN.md)、
  [`examples.zh-CN.md`](../bot-config-manifest/examples.zh-CN.md)
- with-manifest 创建契约（§8.5 的姊妹接口面）：
  [`openapi_v1/bots/create_with_manifest.py`](../../src/agentclaw/community/adapters/http/openapi_v1/bots/create_with_manifest.py)
- Bot 创建指南（普通创建、引擎、集群、模板）：
  Avernet 仓库 `docs/bot-create-api-guide.zh-CN.md`。