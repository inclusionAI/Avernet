# 进化 API 与客户端

> English version: [09-evolution-api.md](09-evolution-api.md)

> 状态：草稿（DRAFT）。[Bot 进化架构](design.zh-CN.md)中的服务。
> 公开访问层：所有进化端点共享的约定、全部公开端点的索引、生成的 SDK，
> 以及 `avn` CLI。

## 1. 目的与范围

Bot 进化平台通过**一个资源 API** 访问：一份位于公开前缀 `/openapi/v1` 下的
OpenAPI 文档，涵盖基因组端点和进化端点。调用方使用的其他一切都从这一个 API
派生：

- **生成的客户端 SDK**（Python 和 TypeScript），供确定性代码使用：
  流水线、CI 以及 UI 后端。
- **一个轻薄的 `avn` CLI**，构建在客户端 SDK 之上，供人类和 CI 脚本使用。

此前的设计文本将其表述为“从一个 API 派生其他一切”：如果某项能力不在 API
中，就没有任何 SDK 或 CLI 具备它，因此调用方之间不会出现偏差。

**本文档负责：**

| 本文档负责 | 含义 |
| --- | --- |
| 共享 API 约定 | Envelope、请求 id、JSON 规则、路径 id 与自定义方法、幂等键、ETag 与 `If-Match`、错误码、分页、按 id 跟踪的长时工作 |
| 端点索引 | 存在哪些公开端点，以及每个端点由哪份文档负责（[§13](#13-api)） |
| 生成的 SDK | 包、生成方式、手写的便利层（重试、等待、分页、错误类型） |
| `avn` CLI | 映射到 API 调用的命令树、机器可读输出、稳定的退出码、非交互规则、自描述 |

**本文档不负责：**

| 不由本文档负责 | 负责方 |
| --- | --- |
| 资源及其完整的请求/响应 schema | 各自的负责文档：[01-genome.zh-CN.md](01-genome.zh-CN.md)、[02-experience.zh-CN.md](02-experience.zh-CN.md)、[03-strategy.zh-CN.md](03-strategy.zh-CN.md)、[05-experiment-ledger.zh-CN.md](05-experiment-ledger.zh-CN.md)、[06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)、[07-verification.zh-CN.md](07-verification.zh-CN.md)、[08-promotion.zh-CN.md](08-promotion.zh-CN.md) |
| 进化策略作业工作器使用的内部作业协议（`/evolution/v1/...`） | [06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md) |
| 进化策略（插件）SDK `avernet-evolution-strategy`、其测试工具（harness）和一致性套件 | [03-strategy.zh-CN.md](03-strategy.zh-CN.md) |
| 谁可以批准或晋升、风险等级、门禁 | [08-promotion.zh-CN.md](08-promotion.zh-CN.md) |
| 认证与授权 | 本设计集不涉及；已推迟，将有独立的设计 |

**运行位置。** API 由同一前缀后的两个服务提供：基因组和晋升端点（包括候选
报告、评审队列以及批准/拒绝路径）由 **Backend** 提供（与 Manifest 和应用
流水线并列）；经验、进化策略注册表、进化运行、验证、实验记录、元进化以及
共享操作端点由提议的新模块 **`apps/evolution`** 提供（[design.zh-CN.md](design.zh-CN.md)
中的待定决策 D-1）。本文档将二者呈现为一个公开的 `/openapi/v1`
接口面。
本文档中的约定在每个服务中实现一次，位于各服务的 HTTP 适配层，从而使核心
逻辑保持与传输无关。
Backend 现有的 OpenAPI v1 适配器已经提供了本文档复用的 envelope 和
分页类型
（`apps/backend/src/agentclaw/community/adapters/http/openapi_v1/contracts.py`：
`Envelope`、`ErrorEnvelope`、`Page`、`PageParams`；
`responses.py`：`envelope`、`page`、`created`、`accepted`、`deleted`，以及
`@envelope_errors` 装饰器；文档见
`apps/backend/docs/openapi-v1/README.md`）。SDK 包和 `avn`
二进制是新的交付物（[work-items.zh-CN.md](work-items.zh-CN.md) 中的工作项 RSI-07）。

## 2. 领域模型

这里的类型是共享的线上传输约定，而不是进化资源本身（这些资源在各自的负责
文档中定义）。

| 类型 | 含义 | 负责方 | 生命周期 |
| --- | --- | --- | --- |
| `Envelope[T]` | 统一的响应包装：`code`、`message`、`data`、`request_id` | 本文档（复用 OpenAPI v1） | 每个响应 |
| `ErrorEnvelope` | 失败时的同一包装，`data` 始终为 `null` | 本文档（复用 OpenAPI v1） | 每个响应 |
| `Page[T]` / `PageParams` | 列表结果的一页（`total`、`items`）及其控制参数（`page`、`page_size`） | 本文档（复用 OpenAPI v1） | 每个请求 |
| `BotRef` | 一个 bot 的完整标识：`owner_id` 加 `bot_id`，因为单独的 `bot_id` 在不同用户之间并不唯一（[§2.7](#27-botrefbot-标识)） | 本文档；所有进化记录都使用它 | 在 bot 的整个生命周期内固定 |
| `IdempotencyRecord` | 平台对一个幂等键的记忆：`(owner_id, bot_id, key) → resource id`，外加请求的指纹 | 本文档；由各服务存储 | 在首个携带该键的请求时创建；在保留期内保留 |
| `Precondition` | 读取可变资源时返回的 `ETag`，以及在写入时将其回传的 `If-Match` 请求头 | 本文档 | 每个资源版本 |
| `Operation` | 由请求启动、按 id 查询的长时工作单元（`queued`、`running`、`succeeded`、`failed`、`cancelled`） | 概念在 [03-strategy.zh-CN.md](03-strategy.zh-CN.md) 中定义；公开资源及其查询端点由本文档负责（[§13.8](#138-操作--本文档)） | 由 `202` 响应创建；完成后即为终态 |
| `ExitCode` | `avn` CLI 稳定的进程退出码 | 本文档 | 每个 CLI 主版本内固定 |
| `CommandSchema` | 单个 `avn` 命令的机器可读描述（`--help --output json`） | 本文档 | 每个 CLI 版本 |

### 2.1 Envelope 与 ErrorEnvelope

每个 JSON 响应，无论成功还是失败，都是一个 envelope。这是现有的 OpenAPI v1
形态；进化端点原样采用它，从而一个客户端库即可同时处理基因组端点和进化
端点。`code` 为六位数字：HTTP 状态码（三位）后接业务子码（三位），例如
`200000`（OK）、`202000`（Accepted）、`404000`（未找到）。二进制内容（例如
`GET /bots/{bot_id}/genome/content/{digest}`）绕过 envelope；这是唯一的例外，
与当前 OpenAPI v1 一致。

```python
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")

@dataclass(frozen=True)
class Envelope(Generic[T]):
    code: int            # HTTP status (3 digits) + business subcode (3 digits), e.g. 202000
    message: str         # human-readable, always English, e.g. "Accepted"
    data: T | None       # the payload; null on errors (and on empty results where documented)
    request_id: str      # trace id of this HTTP request; mirrors the X-Trace-Id response header

@dataclass(frozen=True)
class ErrorEnvelope:
    code: int            # e.g. 409001; the subcode identifies the failure (§7)
    message: str         # fixed public message per code, never an internal exception text
    data: None           # always null
    request_id: str
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// A success envelope (GET /bots/bot_123/evolution/runs/run_7f3)
{
  "code": 200000,
  "message": "OK",
  "data": {"run_id": "run_7f3", "status": "running"},
  "request_id": "4f2c9a7e1b3d4e5f8a9b0c1d2e3f4a5b"
}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// An error envelope: the policy changed since the caller read it
{
  "code": 412000,
  "message": "Precondition failed: the resource changed since it was read",
  "data": null,
  "request_id": "9a1b2c3d4e5f60718293a4b5c6d7e8f9"
}
```

### 2.2 Page 与 PageParams

列表端点使用现有的 OpenAPI v1 分页：从 1 开始计数的 `page`，取值 1 到 100
（默认 20）的 `page_size`，以及包含总数和条目的 `Page` 载荷。

```python
@dataclass(frozen=True)
class PageParams:
    page: int = 1          # 1-based
    page_size: int = 20    # 1..100

@dataclass(frozen=True)
class Page(Generic[T]):
    total: int             # number of items matching the query (all pages)
    items: list[T]         # items on this page; present, possibly empty
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// data of GET /bots/bot_123/evolution/runs?status=completed&page=2&page_size=2
{
  "total": 5,
  "items": [
    {"run_id": "run_7c1", "status": "completed", "created_at": "2026-10-06T02:00:04Z"},
    {"run_id": "run_7d9", "status": "completed", "created_at": "2026-10-07T02:00:03Z"}
  ]
}
```

### 2.3 IdempotencyRecord

**幂等键**（idempotency key）是客户端为一个逻辑请求选定的字符串（例如
“为 bot_123 启动今晚的运行”）。客户端在该请求的每次重试中发送同一个键，
对不同的请求则使用不同的键。平台记住该键产生了哪个资源，因此重试会返回
那个资源，而不是再创建一个。规则见 [§5](#5-幂等键)。

```python
from datetime import datetime
from typing import Literal

# What a key can create or start (one value per row of the §5.3 table).
IdempotentResourceKind = Literal["run", "operation", "revision", "promotion", "review_decision", "feedback"]

@dataclass(frozen=True)
class IdempotencyRecord:
    bot: BotRef            # scope: the bot addressed by the request (owner + bot id, §2.7)
    key: str               # the client's Idempotency-Key header value
    fingerprint: str       # sha256 of the RFC 8785 canonical form of {method, path, body}
    resource_kind: IdempotentResourceKind
    resource_id: str       # id of what the first request created or started, e.g. "run_7f3"
    first_status: int      # HTTP status of the first response (201 or 202)
    created_at: datetime
    expires_at: datetime   # end of the retention window (open decision, §16)
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
  "key": "nightly-bot_123-2026-10-08",
  "fingerprint": "sha256:5d1e…",
  "resource_kind": "run",
  "resource_id": "run_7f3",
  "first_status": 202,
  "created_at": "2026-10-08T02:00:03Z",
  "expires_at": "2026-11-07T02:00:03Z"
}
```

### 2.4 Precondition（ETag 与 If-Match）

**ETag** 是服务器随资源一起返回的版本标签。想要修改该资源的客户端在
**`If-Match`** 请求头中回传该标签；如果资源在此期间已发生变化，服务器以
`412 Precondition Failed` 拒绝这次写入，而不是悄无声息地覆盖另一位写入者
的修改。

```python
@dataclass(frozen=True)
class Precondition:
    etag: str              # opaque, strong, quoted on the wire: "\"pol-7\""
    resource: str          # the path it belongs to
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// What the SDK keeps after GET /bots/bot_123/evolution/policy
{"etag": "\"pol-7\"", "resource": "/openapi/v1/bots/bot_123/evolution/policy"}
```

### 2.5 Operation（公开视图）

**操作**（operation）是可能比一个短 HTTP 请求持续更久的工作：一次评估、
一次实验记录导出。启动它会立即返回带有 `{operation_id}` 的
`202 Accepted`；随后调用方通过
`GET /bots/{bot_id}/evolution/operations/{operation}` 按 id 查询其状态，这是唯一的
公开操作资源（[§13.8](#138-操作--本文档)）。同一概念也在进化策略内部使用
（智能体会话、训练评估）；见
[03-strategy.zh-CN.md](03-strategy.zh-CN.md)。运行遵循相同的模式，但有其
自己的状态值（[06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)）。

```python
from typing import Literal

OperationStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]
# The public operation kinds: ad-hoc evaluations (07) and ledger exports (05).
OperationKind = Literal["evaluation", "ledger_export"]

@dataclass(frozen=True)
class OperationError:
    code: int              # same code space as ErrorEnvelope.code
    message: str

@dataclass(frozen=True)
class Operation:
    id: str                         # "op_19a"
    kind: OperationKind             # which kind of work; decides the shape of `result`
    status: OperationStatus
    created_at: datetime
    updated_at: datetime
    result: dict | None             # present only when status == "succeeded"; shape per kind (owning doc),
                                    # e.g. {"evaluation_id": ...} for an evaluation
    error: OperationError | None    # present only when status == "failed"
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "op_19a",
  "kind": "ledger_export",
  "status": "succeeded",
  "created_at": "2026-10-08T09:30:00Z",
  "updated_at": "2026-10-08T09:31:12Z",
  "result": {"digest": "sha256:e7a2…", "format": "jsonl", "entries": 412},
  "error": null
}
```

### 2.6 ExitCode 与 CommandSchema

```python
from enum import IntEnum

class ExitCode(IntEnum):
    OK = 0
    ERROR = 1             # proposed: any failure not listed below (unmapped 5xx, internal CLI error)
    USAGE = 2             # bad arguments, or a 400 validation error from the API
    NOT_FOUND = 3         # 404
    CONFLICT = 4          # 409 (incl. compare-and-swap, idempotency key reuse) and 412 / 428
    POLICY_DENIED = 5     # the request violates the bot's evolution policy or a platform rule
    BUDGET_EXCEEDED = 6   # a budget ceiling refuses the request
    TRANSIENT = 7         # network failure, 429, 502, 503, 504, or --wait timed out; safe to retry

@dataclass(frozen=True)
class CommandSchema:
    command: list[str]              # e.g. ["evolve", "run", "start"]
    summary: str
    api: list[str]                  # the API calls it makes, e.g. ["POST /bots/{bot_id}/evolution/runs"]
    arguments: list[dict]           # name, type, required, repeated, description
    flags: list[dict]               # name, type, default, description
    destructive: bool               # True if --yes is required
    output_schema: str              # JSON Schema id of the data it prints
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// avn evolve run start --help --output json
{
  "command": ["evolve", "run", "start"],
  "summary": "Start an evolution run for a bot (idempotent)",
  "api": ["POST /bots/{bot_id}/evolution/runs", "GET /bots/{bot_id}/evolution/runs/{run}"],
  "arguments": [],
  "flags": [
    {"name": "--bot", "type": "string", "required": true, "description": "Bot id"},
    {"name": "--binding", "type": "string", "required": true, "description": "Binding of the bot's evolution policy to run"},
    {"name": "--idempotency-key", "type": "string", "required": false, "description": "Reuse on retries; generated and printed when omitted"},
    {"name": "--wait", "type": "bool", "default": false, "description": "Repeat the status lookup until the run is terminal"},
    {"name": "--timeout", "type": "duration", "default": "2h", "description": "Give up waiting (the run continues) and exit 7"}
  ],
  "destructive": false,
  "output_schema": "avn/run@1"
}
```

### 2.7 BotRef（bot 标识）

`bot_id` 只在同一个所有者内唯一：两个用户可以各自拥有一个名为 `bot_123`
的 bot。OpenAPI v1 如今已经这样处理：它以 `/openapi/v1/bots/{bot_id}/…`
寻址 bot，并用 `entity_id` 查询参数指明 bot 的所有者，该参数默认为调用方
（`apps/backend/src/agentclaw/community/adapters/http/openapi_v1/__init__.py`；
`…/openapi_v1/engine_runtime/params.py` 中的 `resolve_owner_id`）。因此每条
进化记录都以一个 `BotRef` 存储这一对值，每个内部服务方法都接受 `BotRef`，
而从不接受单独的 bot id。运行、候选、操作、修订版和实验记录条目的 id 则不同：
它们本身就是全局唯一的。

```python
@dataclass(frozen=True)
class BotRef:
    owner_id: str          # the user (entity) who owns the bot; the `entity_id` of OpenAPI v1
    bot_id: str            # the bot's id within that owner, as in /bots/{bot_id}/…; not unique on its own
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// How a record names its bot (field "bot" of an IdempotencyRecord, Run, LedgerEntry, …)
{"owner_id": "user_owner_5", "bot_id": "bot_123"}
```

在线上传输时，这一对值按 OpenAPI v1 的方式拆分：`bot_id` 放在路径中，所有者
作为 `entity_id`（[§4.1](#41-路径资源与自定义方法））。

## 3. 调用方

在第一次迭代中，API 有三类调用方。它们使用相同的端点；每类调用方可以做
什么由负责文档决定（晋升和批准见 [08-promotion.zh-CN.md](08-promotion.zh-CN.md)），
而不是为每类调用方提供单独的 API。

| 调用方 | 示例 | 接入面 | 典型调用 |
| --- | --- | --- | --- |
| **确定性流水线 / CI** | 夜间作业、技能变更后的 CI、产品后端 | 客户端 SDK，或在脚本中使用 `avn` | 启动运行、查询其状态、读取候选报告、在所有者的自动晋升策略允许范围内批准 |
| **UI 后端** | 工作台的进化页面（过渡期为 AgentEvolve UI） | 客户端 SDK（TypeScript） | 人类在 UI 中所做的一切：编辑进化策略配置、评审队列、报告、晋升、浏览实验记录 |
| **人类操作员** | Bot 所有者、租户管理员、研究人员 | UI、`avn` | 一切操作，包括评审、晋升以及回到旧版本 |

进化策略作业工作器是平台服务，通过进化策略 SDK 使用内部作业协议，而不是
这个公开 API（[06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)、[03-strategy.zh-CN.md](03-strategy.zh-CN.md)）。

Bot 调用方（驱动自身进化的 bot，或作为作业运行器的 bot，以及它们会使用的
CLI 技能或 MCP 适配器）随 DR-3 一并推迟
（[decisions/0003-bot-principal-for-evolution-surface.zh-CN.md](decisions/0003-bot-principal-for-evolution-surface.zh-CN.md)）。

## 4. 请求与响应约定

### 4.1 路径、资源与自定义方法

- **前缀。** 每个公开路径都位于 `/openapi/v1` 下。本设计集中的路径都相对于
  它书写。
- **面向资源。** 集合使用复数名词
  （`/bots/{bot_id}/evolution/runs`）；成员即集合加上其 id。
  `GET` 读取，在集合上 `POST` 创建或启动，`PUT` 替换整个
  可变资源。
- **Bot 寻址。** bot 范围的路径在路径中携带 bot id
  （`/bots/{bot_id}/…`），在 **`entity_id` 查询参数**中携带 bot 的所有者，
  该参数默认为调用方，与 OpenAPI v1 如今的做法完全一致（[§2.7](#27-botrefbot-标识））。
  `bot_id` 在不同用户之间并不唯一，因此服务器总是先解析 `(entity_id 或调用方,
  bot_id)` 这一对值，再做其他任何事。本设计集中的大多数示例省略了
  `entity_id`，因为调用方就是所有者；协作者或代表他人 bot 行事的流水线则要
  加上它，例如
  `POST /bots/bot_123/evolution/runs?entity_id=user_owner_5`。该调用方是否
  可以操作这个 bot，由现有的 OpenAPI v1 访问检查决定，而不是由这一层决定。
- **自定义方法**用于不是单纯创建或替换的动作，在资源上使用冒号后缀：
  `:cancel`、`:approve`、`:reject`、`:export`。它们总是 `POST`。
- **包含冒号或斜杠的 id。** 修订版和候选的 id 是内容哈希，例如
  `sha256:a90b…`；进化策略 id 包含 `/`
  （`acme/correction-fixer`）。在路径段中，客户端对冒号（`sha256%3Ac41e…`）
  和斜杠（`acme%2Fcorrection-fixer`）进行百分号编码。
  服务器按最后一个路径段中的**最后一个**冒号拆分自定义方法，因此
  `/candidates/sha256%3Ac41e…:approve` 没有歧义。SDK
  和 `avn` 负责编码；调用方按响应中出现的形式传递 id。
- **每个 bot 的序号。** 修订版的 `seq`（向人类展示为 `r41`）不是标识
  （[01-genome.zh-CN.md](01-genome.zh-CN.md)）。API 接受修订版 id；`avn`
  另外接受 `r41` 形式，并在调用前通过基因组端点将其解析。

### 4.2 请求 id

每个响应都带有一个**请求 id**：即 envelope 中的 `request_id` 字段，
并在 `X-Trace-Id` 响应头中镜像（现有 OpenAPI v1 行为）。它标识一次 HTTP
请求，用于日志和支持。它不是幂等键：

| | 请求 id | 幂等键 |
| --- | --- | --- |
| 由谁选定 | 服务器 | 客户端 |
| 标识 | 一次 HTTP 尝试 | 一个逻辑请求，跨越其所有重试 |
| 重试时是否相同？ | 否，每次尝试都有自己的请求 id | 是，按定义如此 |
| 用途 | 追踪、日志、支持 | 避免把同一件事创建或启动两次 |

SDK 在每个响应和每个错误上都暴露请求 id；`avn` 在失败时打印它（stderr），
并将其包含在 JSON 输出中。

### 4.3 JSON

- 请求体和响应体都是 JSON（`application/json`），UTF-8 编码。
  本设计中的一切新内容都是 JSON；Bot Config Manifest 与现在一样保持 YAML
  （[01-genome.zh-CN.md](01-genome.zh-CN.md)）。
- 字段名采用 `snake_case`，在 `/openapi/v1` 内保持稳定。
- 时间戳是 UTC 的 RFC 3339 字符串（`2026-10-08T02:00:03Z`）；
  时长是带 `_s` 后缀的整数秒（`max_wall_clock_s`）。
- 需要哈希的内容（修订版、补丁、候选 id）使用 RFC 8785 规范化，且不包含
  浮点数（[01-genome.zh-CN.md](01-genome.zh-CN.md)）。响应不要求是
  规范形式，但客户端不得对已哈希的文档重新序列化并期望得到相同的哈希，
  除非对其进行规范化。
- 每个资源都有一个 JSON Schema；OpenAPI 文档引用它们。

### 4.4 v1 内的兼容性

- `/openapi/v1` 内的变更都是增量式的：新端点、新的可选请求字段、新的
  响应字段。
- 客户端忽略其不认识的响应字段。生成的 SDK 保留未知字段以供访问，而不是
  报错。
- 文档中标明为封闭的状态枚举（运行状态、操作状态、判定状态）在 v1 内不会
  新增取值；新增取值将是破坏性变更。其他枚举是开放的，客户端将未知取值视为
  不透明值。
- 删除或重命名字段，或收紧校验，需要端点的新版本。

## 5. 幂等键

任何创建资源或启动工作的 `POST` 都接受 **`Idempotency-Key`** 请求头。运行
提交是幂等的，并由平台保证：使用相同键重复 `POST` 会返回相同的运行 id，
不会启动任何新的东西。索引中标记为“key”的每个端点同样如此
（[§13](#13-api)）。

### 5.1 客户端规则

- 键是一个**由客户端选定的字符串**，长度为 1 到 255 个可打印 ASCII
  字符；推荐的字符集为 `[A-Za-z0-9._:/-]`。
- 它**在一个逻辑请求的每次重试中保持相同**，并且**对不同请求各不
  相同**。
- 它**不得**是在重试之间会变化的东西。在发送时取的时间戳，或每次尝试生成的
  随机值，都会使每次重试看起来像一个新请求，从而失去意义。

各类调用方的良好键：

| 调用方 | 键 | 示例 |
| --- | --- | --- |
| 流水线（一次性逻辑请求） | 一个 UUID，**在首次尝试之前创建一次**，并与作业状态一起保存 | `0b6f7c52-3d0e-4c1b-9a63-8f2a7d0e5c11` |
| 流水线（定时，每个周期一个） | 由使请求唯一的因素确定性地生成 | `nightly-bot_123-2026-10-08` |
| 平台触发器（按计划触发的绑定） | `<binding_id>/<scheduled_fire_time>` | `bind_01/2026-10-08T02:00:00Z` |
| 进化策略（内部，作业协议） | `<run_id>/<own step>` | `run_7f3/round-2/tune` |

触发器键中的计划触发时间是计划所指定的时间，而不是触发器实际运行的时间，
因此即使触发器延迟触发或触发两次，也仍只产生一次运行。

### 5.2 平台规则

- 平台存储 **`(owner_id, bot_id, key) → resource id`**，以及请求的指纹
  （对规范化 `{method,
  path, body}` 的 `sha256`）。对于 bot 范围的路径，作用域是被寻址的 bot：
  路径中的 `bot_id` 及其所有者（`entity_id`，省略时为调用方），因此两个
  所有者下 `bot_id` 相同的 bot 永远不会共享键。
- 该记录与其指向的资源**在同一事务中**写入，因此崩溃永远不会留下一个
  没有键的已创建运行，或一个没有运行的键。
- **相同键、相同请求**（指纹匹配）：平台返回原始结果：相同的状态码
  （`201` 或 `202`）和相同的资源 id，`data` 中为资源的**当前**状态（一个
  一小时前启动的运行会以 `running` 而非 `queued` 返回）。响应
  带有请求头 `Idempotent-Replayed: true`（提议）。不会再次创建、启动或
  计费任何东西。
- **相同键、不同请求**（指纹不同，包括端点不同）：返回 `409`，代码为
  `409001`。这能捕获重用键的客户端缺陷；平台从不猜测本意是哪个请求。
- **并发重复。** 两个同时到达、键相同的请求解析为同一个资源：第二个请求
  短暂等待第一个提交，然后重放它；如果第一个在短时限内未提交，则以
  `409` 及代码 `409002`（“使用此键的请求正在处理中”）应答。`409002`
  可以用同一个键重试。
- **保留期。** 记录至少保留到调用方可能重试的时长。该时间窗口是一项待定
  决策（[§16](#16-待定决策)）；过期后，该键可以被重用。
- **天然幂等的创建。** 有些创建按内容幂等，不需要键：候选 id 是其补丁的
  内容哈希，修订版 id 是 `{spec, policy}` 的哈希，而注册一个已存在且内容
  完全相同的进化策略版本会返回该版本。
  为保持一致，它们仍接受键。

### 5.3 幂等键的使用位置

| 端点 | 键创建或启动的内容 |
| --- | --- |
| `POST /bots/{bot_id}/evolution/runs` | 一次运行（`202`，运行 id） |
| `POST /bots/{bot_id}/evolution/evaluations` | 一个评估操作（`202`，操作 id） |
| `POST /bots/{bot_id}/evolution/ledger:export` | 一个导出操作（`202`，操作 id） |
| `POST /bots/{bot_id}/genome/revisions` | 一个修订版（同时按内容幂等） |
| `POST /bots/{bot_id}/genome/promotions` | 一次晋升（移动 `active`） |
| `POST /bots/{bot_id}/evolution/candidates/{candidate}:approve` 和 `:reject` | 一项评审决定 |
| `POST /bots/{bot_id}/evolution/runs/{run}:cancel` | 一次取消（同时按状态幂等） |
| `POST /bots/{bot_id}/evolution/operations/{operation}:cancel` | 一次操作取消（同时按状态幂等） |
| `POST /bots/{bot_id}/experience/feedback` | 一条反馈记录 |
| `POST /evolution/strategies` | 一个进化策略版本（同时按内容幂等） |

`PUT` 请求按定义是幂等的（相同的请求体产生相同的状态），使用 `If-Match`
而不是键。

## 6. 并发：ETag、If-Match 与比较并交换

两个修改同一资源的写入者不得悄无声息地相互覆盖。API 使用两种机制，由
资源的负责文档按资源选择：

**ETag 与 `If-Match`** 用于整个资源的替换（`PUT`）。目前的
`PUT /config-manifest` 两者都没有，这是基因组注册表要弥补的缺口之一
（[01-genome.zh-CN.md](01-genome.zh-CN.md)）。

- 对可变资源的 `GET` 返回一个 `ETag` 响应头（一个强的、不透明的、带引号的
  标签）。
- `PUT` 必须发送 `If-Match: <etag>`。如果资源在此之后发生了变化，服务器
  应答 `412`（`412000`）。如果缺少 `If-Match`，则应答
  `428 Precondition Required`（`428000`），因此客户端不会意外跳过这项
  检查。
- 公开接口面上的第一个可变资源是 bot 的进化策略配置
  （`GET|PUT /bots/{bot_id}/evolution/policy`）。

**请求体中的比较并交换**（compare-and-swap）用于引用（ref）移动。引用更新携带
`expected_revision`：客户端认为该引用当前指向的修订版。不匹配时返回 `409`
（`409010`）。这是基因组注册表为 `PUT /bots/{bot_id}/genome/refs/draft` 定义的
机制（[01-genome.zh-CN.md](01-genome.zh-CN.md)）；晋升在同一规则下移动
`active`（[08-promotion.zh-CN.md](08-promotion.zh-CN.md)）。

**不可变资源**（修订版、内容 blob、进化策略版本、实验记录条目）永不改变，
因此不需要写入前置条件。它们的
`ETag` 由其内容 id 派生，客户端可以发送
`If-None-Match` 以获得 `304 Not Modified`（提议），而不是响应体。

**客户端从不写入的资源**（运行、操作、评审条目）只通过自定义方法
（`:cancel`、`:approve`、`:reject`）改变，这些方法会对照资源的状态进行
检查：批准一个已被拒绝的候选返回 `409`（`409020`），批准一个已被批准的
候选则重放先前的决定。

## 7. 错误

### 7.1 形态与编码空间

每个有文档说明的失败都是一个 `ErrorEnvelope`（[§2.1](#21-envelope-与-errorenvelope)），
带有六位代码：HTTP 状态码加业务子码。消息按代码固定（Backend 的
`ENVELOPE_ERRORS` 映射已为 OpenAPI v1 强制执行这一点），因此可以安全地展示，
也可以稳定地匹配，但客户端应依据 `code` 分支，绝不依据 `message`。

每份负责文档为其自身的失败定义子码。本文档确定共享的子码，以及 SDK 和 CLI
映射到错误类型和退出码的**系列**：

| 代码 | 含义 | 是否重试？ | `avn` 退出码 |
| --- | --- | --- | --- |
| `400000` | 请求体或查询未通过校验（schema、枚举、范围） | 否：修正请求 | 2 |
| `404000` | 资源未找到（bot、运行、修订版、候选、进化策略、套件、条目） | 否 | 3 |
| `409001` | 幂等键被用于不同的请求 | 否：客户端缺陷 | 4 |
| `409002` | 使用此幂等键的请求仍在处理中 | 是，使用相同的键 | 7 |
| `409010` | 引用的比较并交换失败：`expected_revision` 已过时 | 重新读取后 | 4 |
| `409011` | 进化策略版本已以不同内容注册 | 否 | 4 |
| `409020` | 动作与资源状态冲突（例如批准已被拒绝的候选、取消已结束的运行） | 否 | 4 |
| `412000` | `If-Match` 与当前 `ETag` 不匹配 | 重新读取后 | 4 |
| `428000` | 需要 `If-Match` 但缺失 | 添加后 | 4 |
| `403xxx` / `422xxx`（策略系列） | 请求违反了 bot 的进化策略配置或平台规则：锁定基因、基因不在绑定的 `allowed_genes` 中、绑定检查失败、进化被紧急停止开关冻结、进化策略已禁用、候选在门禁下不可晋升 | 否 | 5 |
| `403100` 区段（预算系列） | 预算上限拒绝了请求（每 bot 或每租户的日或月上限、每日最大晋升次数） | 在上限重置之前不可 | 6 |
| `429000` | 被限流 | 是，在 `Retry-After` 之后 | 7 |
| `502000`、`503000`、`504000` | 上游或服务不可用 | 是，使用相同的键 | 7 |
| `500000` | 意外的服务器错误 | 仅在携带幂等键时 | 1 |

策略系列和预算系列中的具体子码由负责文档设定（预算、紧急停止开关和绑定
检查见 [06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)；锁定基因见
[01-genome.zh-CN.md](01-genome.zh-CN.md)；门禁见
[08-promotion.zh-CN.md](08-promotion.zh-CN.md)）。

**被拒绝的候选不是错误。** 验证拒绝一个候选，或一次运行以
`budget_exhausted` 结束，都是在资源状态中报告的正常结果，返回 `200`。

### 7.2 重试指引

- `GET` 总是可以安全重试。
- 带幂等键的 `POST` 在瞬时失败（`409002`、`429`、`5xx`、网络错误，或完全
  没有响应）后，使用**相同的键**重试是安全的。
- 不带键的 `POST` 从不由 SDK 自动重试。
- 带 `If-Match` 的 `PUT` 可以安全重试；重试后出现 `412` 意味着有其他人在
  这期间写入（或第一次尝试已成功）：重新读取并做决定。
- 采用带抖动的指数退避，并遵循 `Retry-After`。

## 8. 分页与过滤

- 列表使用 `page`（从 1 开始）和 `page_size`（1 到 100，默认 20），并
  返回 `Page {total, items}`，与当前 OpenAPI v1 一致
  （`contracts.py` 中的 `PageParams`）。
- 过滤器是以所过滤字段命名的查询参数
  （`status=`、`revision=`、`outcome=`、`run=`、`strategy=`）；时间范围
  使用 `since=` 和 `until=`（RFC 3339）。每份负责文档列出其过滤器。
- 除非负责文档另有说明，默认排序为按创建时间从新到旧；时间戳相同时排序
  稳定（按 id）。
- SDK 提供遍历所有页的迭代器；`avn` 提供 `--page`、
  `--page-size` 和 `--all`。
- 大型、仅追加的集合（片段（episode）、实验记录条目）可能需要游标分页而非
  页码，因为 `total` 开销大，且随着条目追加页会发生偏移。这是一项待定决策
  （[§16](#16-待定决策)）；在决定之前，沿用现有约定。

## 9. 按 id 跟踪的长时工作

工作运行期间不会保持任何请求处于打开状态。可能比一个短请求持续更久的工作
遵循同一模式：

1. **启动立即返回 id。** 启动请求记录该工作，并应答
   `202 Accepted`（`202000`）。对于操作，`data` 为
   `{operation_id}`，`Location` 响应头指向
   `/bots/{bot_id}/evolution/operations/{operation}`；对于运行，`data` 是该
   运行的当前状态（status 为 `queued`），`Location` 指向该运行。
2. **按 id 查询状态。** 调用方对该 URL 反复发起短 `GET`，直到状态为终态。
   每次查询都会立即返回。响应可能带有 `Retry-After`，作为下次查询的提示。
3. **启动是幂等的。** 使用相同的幂等键，重复启动会返回相同的 id
   （[§5](#5-幂等键)）。
4. **id 是唯一的句柄。** 没有回调或 webhook 通道；
   SDK 的 `wait` 辅助方法和 `avn ... --wait` 只是重复查询。
5. **工作能够在崩溃后存续。** 运行在工作器崩溃和重启后以同一运行 id 继续
   （[06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)）；
   操作由平台持久化，与调用方无关。

| 启动方式 | 返回 | 查询方式 | 状态 |
| --- | --- | --- | --- |
| `POST /bots/{bot_id}/evolution/runs` | 运行 id | `GET /bots/{bot_id}/evolution/runs/{run}` | `queued \| running \| completed \| failed \| cancelled \| budget_exhausted` |
| `POST /bots/{bot_id}/evolution/evaluations` | 操作 id | `GET /bots/{bot_id}/evolution/operations/{operation}`；其 `result` 携带 `evaluation_id`，通过 `GET /bots/{bot_id}/evolution/evaluations/{evaluation}` 读取 | `queued \| running \| succeeded \| failed \| cancelled` |
| `POST /bots/{bot_id}/evolution/ledger:export` | 操作 id | `GET /bots/{bot_id}/evolution/operations/{operation}` | `queued \| running \| succeeded \| failed \| cancelled` |

候选和判定在下一层遵循同样的“按 id”规则：候选的报告按候选 id 查询
（`GET /bots/{bot_id}/evolution/candidates/{candidate}`），不存在等待判定的
阻塞调用。

## 10. 生成的 SDK

| 包 | 语言 | 面向 | 内容 |
| --- | --- | --- | --- |
| `avernet-evolution` | Python | 流水线、CI、Python 后端 | 为整个公开进化与基因组接口面生成的客户端，外加下文的便利层 |
| `@avernet/evolution` | TypeScript | UI 后端、Node 流水线 | 同上，由同一份 OpenAPI 文档生成 |

进化策略 SDK（`avernet-evolution-strategy`）是面向进化策略作者的另一个包，
由 [03-strategy.zh-CN.md](03-strategy.zh-CN.md) 负责。

### 10.1 生成

- OpenAPI 文档是事实来源。SDK 模型和原始端点方法都由它**生成**；没有手写的
  请求代码。
- 提议：每个服务（Backend、`apps/evolution`）发布其负责的文档部分，由一个
  构建步骤将它们合并为 `/openapi/v1` 下的一份公开文档，两个 SDK 和 `avn`
  命令 schema 都由它生成（待定决策，[§16](#16-待定决策)）。
- SDK 版本跟随 API：增量式 API 变更对应 SDK 次版本发布，只有在 API 新版本
  时才发布主版本。

### 10.2 手写的便利层

在生成代码之上的一个小层把本文档中的约定实现一次，使每个调用方以相同方式
获得它们。它不添加 API 所不具备的行为。

| 关注点 | 行为 |
| --- | --- |
| Envelope | 解包 `data`；在结果和错误上保留 `request_id` |
| 错误 | 按代码系列抛出带类型的错误：`ValidationError`、`NotFound`、`Conflict`（含子类 `IdempotencyKeyReused`、`RefConflict`、`PreconditionFailed`）、`PolicyDenied`、`BudgetExceeded`、`Transient`、`ServerError` |
| 幂等 | 每个接受键的方法都有 `idempotency_key` 参数。若省略，SDK 会**每次方法调用生成一次** UUID，并在自身的重试中重用；它在结果上公开，以便调用方保存。在一次调用内部生成的键无法保护来自*新*进程的重试，因此可能重启的流水线应传入自己的键 |
| 重试 | 对瞬时失败自动带退避重试，仅限于 `GET`、带 `If-Match` 的 `PUT`，以及带键的 `POST` |
| ETag | 读取方法随资源返回 `ETag`；写入方法接受 `if_match`，并有一个辅助方法执行读-改-写，在 `412` 时重试 |
| 等待 | `wait(...)` 反复进行短状态查询，直到终态或超时；绝不使用长时间保持的请求 |
| 分页 | `iter_*` 方法遍历所有页 |
| Id | 对路径中包含冒号的 id 进行百分号编码 |

## 11. `avn` CLI

`avn` 是面向人类和 CI 的命令行客户端。提议：一个用 Rust 编写的新二进制，
遵循 `bcs-cli`（`apps/bcs/crates/tools/bcs-cli/`）的约定，并为日后加入其他
平台命令留有空间（待定决策 A-1，[§16](#16-待定决策)）。目前不存在通用的 Avernet CLI；
`bcs-cli` 仅限于 BCS 范围。

### 11.1 设计规则

- **轻薄。** 每个命令就是一到两次 SDK 调用。CLI 不包含 API 所不具备的逻辑，
  因此脚本、流水线和 UI 不会产生分歧。
- **机器优先的输出。** 当 stdout 不是 TTY 时，`--output json` 是默认值；在
  TTY 上 `--output table` 是默认值。JSON 输出就是原样的 API envelope
  （`code`、`message`、`data`、`request_id`），每个命令在 stdout 上输出一个
  JSON 文档，失败时也是如此。失败的单行人类可读摘要输出到 stderr。字段名
  与 API 一致，并且保持稳定。
- **稳定的退出码**（[§11.3](#113-退出码)）。
- **非交互。** 除非给出 `--interactive`，否则不进行提示。
  改变 bot 所运行内容或丢弃工作的命令属于**破坏性**命令，需要 `--yes`：
  `genome promote`、`evolve review approve`、
  `evolve review reject`、`evolve run cancel`、`evolve policy set`。没有
  `--yes` 时，它们以代码 2 退出，且不做任何更改。
- **在 API 支持时提供 `--dry-run`。** 试运行必须由 API 应答（一个仅校验的
  请求），而不是由 CLI 模拟；
  `genome patch apply --dry-run` 是第一个这样的命令。其他哪些端点提供仅校验
  模式由其负责文档决定。
- **自描述。** `avn <cmd> --help --output json` 打印该命令的
  `CommandSchema`（[§2.6](#26-exitcode-与-commandschema)），因此脚本或工具
  无需阅读文档即可发现参数。
- **默认幂等。** 启动或创建资源的命令接受
  `--idempotency-key`；省略时，`avn` 会生成一个，在自身的重试中使用它，并将其
  打印出来（在 JSON 输出中作为顶层 `idempotency_key` 字段，与 envelope 字段
  并列；见 [§14.2](#142-使用-avn-的-ci-脚本)），以便脚本使用同一个键重新
  运行该命令。
- **覆盖率门禁。** 与 singlebox 中的 `bcs-cli` 一样，每个叶子命令都由针对真实
  栈的端到端测试覆盖。

### 11.2 命令树与 API 调用的对应

路径相对于 `/openapi/v1`。

| 命令 | API 调用 | 说明 |
| --- | --- | --- |
| `avn genome log --bot B [--status S] [--parent R]` | `GET /bots/{bot_id}/genome/revisions` | |
| `avn genome show --bot B [--revision R \| --ref active]` | `GET /bots/{bot_id}/genome/refs`（用于解析引用或 `r41`）、`GET /bots/{bot_id}/genome/revisions/{rev}` | |
| `avn genome diff --bot B R1 --against R2` | `GET /bots/{bot_id}/genome/revisions/{rev}/diff?against=` | |
| `avn genome refs --bot B` | `GET /bots/{bot_id}/genome/refs` | |
| `avn genome draft set --bot B R --expected R0` | `PUT /bots/{bot_id}/genome/refs/draft` | 基于 `expected_revision` 的比较并交换 |
| `avn genome patch apply --bot B patch.json [--dry-run]` | `POST /bots/{bot_id}/genome/revisions` | 从 `{base, patch}` 记录一个修订版 |
| `avn genome content get --bot B DIGEST` / `content put --bot B FILE` | `GET /bots/{bot_id}/genome/content/{digest}` / `PUT /bots/{bot_id}/genome/content` | 二进制，get 时无 envelope |
| `avn genome promote --bot B --revision R --reason TEXT --yes` | `POST /bots/{bot_id}/genome/promotions` | 也用于回到旧版本：晋升一个更早的修订版 |
| `avn genome export --bot B --format git DIR` | `GET /bots/{bot_id}/genome/revisions`、`GET /bots/{bot_id}/genome/content/{digest}` | 写出本地 git 历史；对平台只读（[01-genome.zh-CN.md](01-genome.zh-CN.md)） |
| `avn experience episodes --bot B [--revision R] [--since T] [--outcome O]` | `GET /bots/{bot_id}/experience/episodes` | |
| `avn experience episode show --bot B EPISODE` | `GET /bots/{bot_id}/experience/episodes/{episode}` | |
| `avn experience feedback add --bot B FILE` / `feedback list --bot B` | `POST` / `GET /bots/{bot_id}/experience/feedback` | |
| `avn evolve strategies list` / `strategies show ID --version V` | `GET /evolution/strategies` / `GET /evolution/strategies/{id}/versions/{version}` | |
| `avn evolve capabilities` | `GET /evolution/capabilities` | |
| `avn evolve policy get --bot B` | `GET /bots/{bot_id}/evolution/policy` | 将 `ETag` 打印为 `etag` |
| `avn evolve policy set --bot B FILE --if-match ETAG --yes` | `PUT /bots/{bot_id}/evolution/policy` | `--if-match` 为必填 |
| `avn evolve run start --bot B --binding ID [--idempotency-key K] [--wait]` | `POST /bots/{bot_id}/evolution/runs`（使用 `--wait` 时另加 `GET .../runs/{run}`） | |
| `avn evolve run list --bot B [--status S]` | `GET /bots/{bot_id}/evolution/runs` | |
| `avn evolve run status --bot B RUN` | `GET /bots/{bot_id}/evolution/runs/{run}` | |
| `avn evolve run candidates --bot B RUN` | `GET /bots/{bot_id}/evolution/runs/{run}/candidates` | |
| `avn evolve run cancel --bot B RUN --yes` | `POST /bots/{bot_id}/evolution/runs/{run}:cancel` | |
| `avn evolve review list --bot B` | `GET /bots/{bot_id}/evolution/review-queue` | |
| `avn evolve review show --bot B CANDIDATE` | `GET /bots/{bot_id}/evolution/candidates/{candidate}` | diff、验证、门禁决定 |
| `avn evolve review approve\|reject --bot B CANDIDATE --reason TEXT --yes` | `POST /bots/{bot_id}/evolution/candidates/{candidate}:approve` / `:reject` | |
| `avn evolve suites list` / `suites show SUITE` | `GET /evolution/suites` / `GET /evolution/suites/{suite}` | |
| `avn evolve evaluate start --bot B --revision R --suite S [--wait]` / `evaluate status --bot B ID` | `POST /bots/{bot_id}/evolution/evaluations` / `GET /bots/{bot_id}/evolution/evaluations/{evaluation}` | 仅限操作员的临时评估 |
| `avn evolve ledger list --bot B [filters]` / `ledger show --bot B ENTRY` | `GET /bots/{bot_id}/evolution/ledger` / `GET /bots/{bot_id}/evolution/ledger/{entry}` | |
| `avn evolve ledger export --bot B [--wait]` | `POST /bots/{bot_id}/evolution/ledger:export`（另加状态查询） | |
| `avn strategy dev\|test\|publish` | 进化策略 SDK 测试工具；`publish` 调用 `POST /evolution/strategies` | 由 [03-strategy.zh-CN.md](03-strategy.zh-CN.md) 负责 |
| `avn job claim\|heartbeat\|input\|upload\|complete\|fail` | 内部作业协议 `/evolution/v1/...` | 用于调试平台作业工作器；由 [06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md) 负责 |

`--bot B` 接受 bot id。每个 bot 范围的命令还接受
`--entity-id OWNER`，作为 `entity_id` 查询参数发送；省略时，
所有者为调用方（[§4.1](#41-路径资源与自定义方法））。

此前设计中的 `avn evolve inbox` 和 `avn evolve observe` 命令
属于 bot 调用方，随其一并推迟（[§3](#3-调用方)）。

### 11.3 退出码

退出码是 CLI 契约的一部分，在一个主版本内不会改变。它们反映的是 **API 调用**，
而不是工作的结果：一次以 `failed` 结束的运行或一个被拒绝的候选，都是一次
成功的查询（退出码 0）；脚本读取 `.data.status`。

| 代码 | 含义 | 典型原因 |
| --- | --- | --- |
| 0 | OK | |
| 1 | 其他失败*（提议）* | `500`、未映射的错误、CLI 内部错误 |
| 2 | 用法错误 | 错误的标志或参数、缺少 `--yes`、`400` 校验错误 |
| 3 | 未找到 | `404` |
| 4 | 冲突 | `409`（`409002` 除外）、`412`、`428`：幂等键被重用、`expected_revision` 已过时、`ETag` 已过时、动作与状态冲突 |
| 5 | 策略拒绝 | 策略系列：锁定基因、绑定检查失败、进化被冻结、进化策略已禁用、不可晋升 |
| 6 | 超出预算 | 预算系列：上限拒绝了请求 |
| 7 | 瞬时 | 网络错误、`409002`、`429`、`502`/`503`/`504`，或 `--wait` 超时（工作仍在继续；重新执行查询） |

## 12. 服务接口

其他部分使用两个接口：SDK 实现的**客户端接口**（CLI 和 UI 后端调用它），
以及每个服务模块（Backend、`apps/evolution`）在其 HTTP 适配器中使用的
**约定接口**，使每个端点表现一致。

### 12.1 客户端接口（SDK 提供的内容）

每个 bot 范围的方法都以 `bot_id` 接受 bot id，并且还接受一个仅限关键字的
`entity_id: str | None = None`：bot 的所有者，作为 `entity_id` 查询参数
发送；`None` 表示调用方就是所有者，即 OpenAPI v1 的默认行为
（[§4.1](#41-路径资源与自定义方法））。为保持签名简短，下面的签名中省略了它。过滤参数
使用负责文档中的封闭取值集合（[01-genome.zh-CN.md](01-genome.zh-CN.md) 中的 `RevisionStatus`、
[02-experience.zh-CN.md](02-experience.zh-CN.md) 中的 `OutcomeStatus`、
[03-strategy.zh-CN.md](03-strategy.zh-CN.md) 中的 `Engine` 和 `ConformanceState`、
[06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md) 中的 `RunStatus`）；`None` 表示“不过滤”。

```python
from typing import AsyncIterator, Protocol

class GenomeClient(Protocol):
    async def list_revisions(self, bot_id: str, *, status: "RevisionStatus | None" = None,
                             parent: str | None = None, page: PageParams = PageParams()) -> Page["GenomeRevision"]: ...
    async def get_revision(self, bot_id: str, revision: str) -> "GenomeRevision": ...
    async def diff(self, bot_id: str, revision: str, *, against: str) -> dict: ...
    async def refs(self, bot_id: str) -> list["GenomeRef"]: ...
    async def set_draft(self, bot_id: str, revision: str, *, expected_revision: str) -> "GenomeRef":
        """Compare-and-swap; raises RefConflict (409010) if the draft moved."""
    async def record_revision(self, bot_id: str, *, base: str, patch: "GenomePatch",
                              idempotency_key: str | None = None) -> "GenomeRevision": ...
    async def promote(self, bot_id: str, revision: str, *, reason: str,
                      idempotency_key: str | None = None) -> "Promotion":
        """Moves `active` (also used to go back to an earlier revision). Owned by 08-promotion."""
    async def get_content(self, bot_id: str, digest: str) -> bytes: ...
    async def put_content(self, bot_id: str, data: bytes) -> str: ...  # digest

class ExperienceClient(Protocol):
    def iter_episodes(self, bot_id: str, *, revision: str | None = None, since: str | None = None,
                      outcome: "OutcomeStatus | None" = None) -> AsyncIterator["Episode"]: ...
    async def get_episode(self, bot_id: str, episode: str) -> "Episode": ...
    async def add_feedback(self, bot_id: str, feedback: "Feedback", *,
                           idempotency_key: str | None = None) -> "Feedback": ...

class StrategiesClient(Protocol):
    async def list(self, *, engine: "Engine | None" = None,
                   conformance: "ConformanceState | None" = None) -> Page["StrategyRegistration"]: ...
    async def get_version(self, strategy: str, version: str) -> "StrategyRegistration": ...
    async def capabilities(self) -> list["Capability"]: ...

class PolicyClient(Protocol):
    async def get(self, bot_id: str) -> tuple["EvolutionPolicy", str]:
        """Returns the policy and its ETag."""
    async def put(self, bot_id: str, policy: "EvolutionPolicy", *, if_match: str) -> tuple["EvolutionPolicy", str]:
        """Raises PreconditionFailed (412000) if the policy changed since it was read."""

class RunsClient(Protocol):
    async def start(self, bot_id: str, *, binding: str, params: dict | None = None,
                    budget: dict | None = None, idempotency_key: str | None = None) -> "Run":
        """202: returns the run as it is now (status queued, or its current status on a replay)."""
    async def get(self, bot_id: str, run: str) -> "Run": ...
    def iter(self, bot_id: str, *, status: "RunStatus | None" = None) -> AsyncIterator["Run"]: ...
    async def candidates(self, bot_id: str, run: str) -> list["Candidate"]: ...
    async def cancel(self, bot_id: str, run: str, *, idempotency_key: str | None = None) -> "Run": ...
    async def wait(self, bot_id: str, run: str, *, timeout_s: int, poll_s: int = 15) -> "Run":
        """Repeats get() until the status is terminal; raises Transient on timeout."""

class VerificationClient(Protocol):
    async def list_suites(self) -> Page["Suite"]: ...
    async def get_suite(self, suite: str) -> "Suite": ...
    async def start_evaluation(self, bot_id: str, *, revision: str, suite: str,
                               idempotency_key: str | None = None) -> Operation: ...
    async def get_evaluation(self, bot_id: str, evaluation: str) -> "Evaluation": ...

class ReviewClient(Protocol):
    async def queue(self, bot_id: str) -> Page["ReviewItem"]: ...
    async def report(self, bot_id: str, candidate: str) -> dict:
        """Diff + verification + gate decision for one candidate (08-promotion)."""
    async def approve(self, bot_id: str, candidate: str, *, reason: str,
                      idempotency_key: str | None = None) -> "ReviewItem": ...
    async def reject(self, bot_id: str, candidate: str, *, reason: str,
                     idempotency_key: str | None = None) -> "ReviewItem": ...

class OperationsClient(Protocol):
    async def get(self, bot_id: str, operation: str) -> Operation: ...
    async def cancel(self, bot_id: str, operation: str, *, idempotency_key: str | None = None) -> Operation: ...
    async def wait(self, bot_id: str, operation: str, *, timeout_s: int = 3600, poll_s: int = 15) -> Operation:
        """Repeats get() until the status is terminal; raises Transient on timeout."""

class LedgerClient(Protocol):
    def iter_entries(self, bot_id: str, **filters: str) -> AsyncIterator["LedgerEntry"]: ...
    async def get_entry(self, bot_id: str, entry: str) -> "LedgerEntry": ...
    async def start_export(self, bot_id: str, *, idempotency_key: str | None = None) -> Operation: ...

class EvolutionClient(Protocol):
    """Entry point of the generated SDK (`avernet_evolution.Client`)."""
    genome: GenomeClient
    experience: ExperienceClient
    strategies: StrategiesClient
    policy: PolicyClient
    runs: RunsClient
    verification: VerificationClient
    review: ReviewClient
    ledger: LedgerClient
    operations: OperationsClient
```

### 12.2 约定接口（各服务模块实现的内容）

```python
class IdempotencyStore(Protocol):
    """Implements §5. Writes happen in the caller's transaction."""

    async def lookup(self, bot: BotRef, key: str) -> IdempotencyRecord | None:
        """The record for (owner_id, bot_id, key), or None if the key is new or expired."""

    async def record(self, record: IdempotencyRecord) -> None:
        """Stores (owner_id, bot_id, key) -> resource id in the same transaction that creates the resource.
        Raises KeyInProgress if another request holds the key and has not committed."""

class IdempotentHandler(Protocol):
    async def handle(self, *, bot: BotRef, key: str | None, fingerprint: str,
                     create: "Callable[[], Awaitable[tuple[str, int]]]",
                     current: "Callable[[str], Awaitable[dict]]") -> tuple[int, dict, bool]:
        """Replays (same fingerprint), refuses (409001, different fingerprint),
        or runs `create` and records the key. Returns (status, data, replayed)."""

class Preconditions(Protocol):
    """Implements §6 for PUT on mutable resources."""

    def etag_of(self, resource_version: str) -> str: ...

    def check(self, *, if_match: str | None, current_etag: str) -> None:
        """Raises PreconditionRequired (428000) if missing, PreconditionFailed (412000) on mismatch."""
```

两者都是提议的共享辅助组件，位于每个服务的 HTTP 适配层；
其后的资源服务保持与传输无关。

## 13. API

所有公开路径都位于前缀 **`/openapi/v1`** 下，并相对于它书写。每个端点的
完整 schema、示例和错误都在其负责文档中；本节为它们建立索引，并在每组中
展示一次共享约定。图例：**key** = 接受 `Idempotency-Key`；**202** = 启动
按 id 查询的长时工作；**ETag** = 写入时必须携带 `If-Match`；
**CAS** = 请求体中的比较并交换。

### 13.1 基因组 — [01-genome.zh-CN.md](01-genome.zh-CN.md)

| 方法与路径 | 用途 | 约定 |
| --- | --- | --- |
| `GET /bots/{bot_id}/genome/revisions` | 列出修订版（`status=`、`parent=`） | Page |
| `POST /bots/{bot_id}/genome/revisions` | 从 `{base, patch}` 或 `{manifest}` 记录一个修订版 | key；按内容幂等 |
| `GET /bots/{bot_id}/genome/revisions/{rev}` | 单个修订版 | 不可变 ETag |
| `GET /bots/{bot_id}/genome/revisions/{rev}/diff?against=` | 比较两个修订版的差异 | |
| `GET /bots/{bot_id}/genome/refs` | 具名引用（`active`、`previous`、`canary`、`draft`……） | |
| `PUT /bots/{bot_id}/genome/refs/draft` | 移动所有者的 `draft` 引用 | CAS（`expected_revision`） |
| `GET /bots/{bot_id}/genome/content/{digest}` | 按摘要获取内容字节 | 二进制，无 envelope |
| `PUT /bots/{bot_id}/genome/content` | 上传内容，返回其摘要 | 按内容幂等 |

共享约定示例：对引用进行比较并交换。

```http
PUT /openapi/v1/bots/bot_123/genome/refs/draft
Content-Type: application/json
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"revision": "sha256:b73d…", "expected_revision": "sha256:a90b…"}   // r42 replaces r41, only if draft is still r41
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// 409: someone moved draft since this client read it
{
  "code": 409010,
  "message": "Ref changed: expected_revision is not the current revision",
  "data": null,
  "request_id": "0d4e5f6a7b8c9d0e1f2a3b4c5d6e7f80"
}
```

### 13.2 经验 — [02-experience.zh-CN.md](02-experience.zh-CN.md)

| 方法与路径 | 用途 | 约定 |
| --- | --- | --- |
| `GET /bots/{bot_id}/experience/episodes` | 列出片段（`revision=`、`since=`、`outcome=`） | Page |
| `GET /bots/{bot_id}/experience/episodes/{episode}` | 单个片段 | |
| `POST /bots/{bot_id}/experience/feedback` | 记录一次评分、纠正或结果 | key |
| `GET /bots/{bot_id}/experience/feedback` | 列出反馈 | Page |

共享约定示例：分页与过滤器。

```http
GET /openapi/v1/bots/bot_123/experience/episodes?revision=sha256%3Aa90b…&outcome=user_corrected&page=1&page_size=2
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "code": 200000,
  "message": "OK",
  "data": {
    "total": 17,
    "items": [
      {"episode_id": "ep_91", "revision_id": "sha256:a90b…", "started_at": "2026-10-07T09:12:00Z",
       "outcome": {"status": "user_corrected"}},
      {"episode_id": "ep_88", "revision_id": "sha256:a90b…", "started_at": "2026-10-06T16:40:21Z",
       "outcome": {"status": "user_corrected"}}
    ]
  },
  "request_id": "1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b"
}
```

### 13.3 进化策略注册表 — [03-strategy.zh-CN.md](03-strategy.zh-CN.md)

| 方法与路径 | 用途 | 约定 |
| --- | --- | --- |
| `GET /evolution/strategies` | 列出已注册的进化策略（`engine=`、`conformance=`） | Page |
| `POST /evolution/strategies` | 注册一个进化策略版本（及其智能体定义） | key；按内容幂等 |
| `GET /evolution/strategies/{id}/versions/{version}` | 单条注册记录及其一致性状态 | 不可变 ETag |
| `GET /evolution/capabilities` | 能力目录 | |

共享约定示例：以不同内容重新注册一个已存在的版本是冲突，而不是悄无声息的
覆盖。

```http
POST /openapi/v1/evolution/strategies
Content-Type: application/json
Idempotency-Key: publish-clawevolve-bot-evolution-2.0.0
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "clawevolve/bot-evolution",
  "version": "2.0.0",
  "runtime": {"kind": "job_worker", "image": "registry.example/clawevolve@sha256:…"},
  "needs": {"experience.sessions@1": {}, "evaluate.train@1": {}}
}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// 409: version 2.0.0 already exists with a different record; publish 2.0.1 instead
{
  "code": 409011,
  "message": "Strategy version already registered with different content",
  "data": null,
  "request_id": "2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c"
}
```

### 13.4 实验记录 — [05-experiment-ledger.zh-CN.md](05-experiment-ledger.zh-CN.md)

| 方法与路径 | 用途 | 约定 |
| --- | --- | --- |
| `GET /bots/{bot_id}/evolution/ledger` | 列出条目（过滤器如 `run=`、`strategy=`、`since=`） | Page |
| `GET /bots/{bot_id}/evolution/ledger/{entry}` | 单个条目 | 不可变 ETag |
| `POST /bots/{bot_id}/evolution/ledger:export` | 导出条目 | key；202 + 操作 id |

共享约定示例：一次长时导出。

```http
POST /openapi/v1/bots/bot_123/evolution/ledger:export
Content-Type: application/json
Idempotency-Key: ledger-export-bot_123-2026-10-08
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"format": "filesystem", "filter": {"since": "2026-09-01T00:00:00Z"}}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// 202; Location: /openapi/v1/bots/bot_123/evolution/operations/op_19a
{
  "code": 202000,
  "message": "Accepted",
  "data": {"operation_id": "op_19a"},
  "request_id": "3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d"
}
```

### 13.5 进化运行 — [06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)

| 方法与路径 | 用途 | 约定 |
| --- | --- | --- |
| `GET /bots/{bot_id}/evolution/policy` | bot 的进化策略配置（绑定列表） | 返回 ETag |
| `PUT /bots/{bot_id}/evolution/policy` | 替换进化策略配置（在此执行绑定检查） | ETag（必须携带 `If-Match`） |
| `POST /bots/{bot_id}/evolution/runs` | 启动某个绑定的一次运行 | key；202 + 运行 id |
| `GET /bots/{bot_id}/evolution/runs` | 列出运行（`status=`） | Page |
| `GET /bots/{bot_id}/evolution/runs/{run}` | 运行状态、轮次、已用预算 | |
| `POST /bots/{bot_id}/evolution/runs/{run}:cancel` | 取消一次运行 | key；按状态幂等 |
| `GET /bots/{bot_id}/evolution/runs/{run}/candidates` | 该运行提交的候选 | |

共享约定示例 1：幂等的运行提交。首次请求与使用相同键的重试得到相同的
运行 id。这里的调用方是一条代表它并不拥有的 bot 行事的流水线，因此它用
`entity_id` 指明所有者（[§4.1](#41-路径资源与自定义方法））。

```http
POST /openapi/v1/bots/bot_123/evolution/runs?entity_id=user_owner_5
Content-Type: application/json
Idempotency-Key: nightly-bot_123-2026-10-08
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"binding": "bind_01", "budget": {"max_usd": 10, "max_wall_clock_s": 3600}}   // body {binding, params?, budget?}, defined in 06-evolution-run.md
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// 202 on the first attempt; Location: /openapi/v1/bots/bot_123/evolution/runs/run_7f3?entity_id=user_owner_5
{
  "code": 202000,
  "message": "Accepted",
  "data": {"run_id": "run_7f3", "binding": "bind_01", "strategy": "clawevolve/bot-evolution@2.0.0",
           "status": "queued", "created_at": "2026-10-08T02:00:03Z"},
  "request_id": "4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e"
}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// 202 on a retry 40 minutes later with the same key; header Idempotent-Replayed: true.
// Same run id, current status; no second run is started or charged.
{
  "code": 202000,
  "message": "Accepted",
  "data": {"run_id": "run_7f3", "binding": "bind_01", "strategy": "clawevolve/bot-evolution@2.0.0",
           "status": "running", "created_at": "2026-10-08T02:00:03Z"},
  "request_id": "5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f"
}
```

共享约定示例 2：进化策略配置上的 `If-Match`。

```http
PUT /openapi/v1/bots/bot_123/evolution/policy
Content-Type: application/json
If-Match: "pol-7"
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "bindings": [
    {"id": "bind_01", "strategy": "clawevolve/bot-evolution@2.0.0", "trigger": {"schedule": "0 2 * * *"},
     "parent": "active", "allowed_genes": ["persona", "skills"], "verification_profile": "default@1",
     "budget": {"max_usd": 20, "max_wall_clock_s": 7200}, "params": {"window_days": 7, "max_rounds": 3}}
  ]
}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// 200 with the new ETag header: ETag: "pol-8"
{
  "code": 200000,
  "message": "OK",
  "data": {"bindings": [{"id": "bind_01", "strategy": "clawevolve/bot-evolution@2.0.0"}]},
  "request_id": "6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a"
}
```

过时的标签得到 `412000`（[§2.1](#21-envelope-与-errorenvelope)）；缺少
`If-Match` 得到 `428000`。（上面响应中的 `data` 为示例而做了缩减；完整的
进化策略配置形态由负责文档定义。）

**内部（不属于公开 API 或客户端 SDK）。** 位于 `/evolution/v1/...` 下的作业
协议供作业工作器类进化策略认领运行并调用其上下文，由
[06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md) 负责。它遵循相同的约定：
JSON、使用 `<run_id>/<step>` 形式的键实现幂等启动，以及以 `202` 和操作 id
应答的长时调用。

### 13.6 验证 — [07-verification.zh-CN.md](07-verification.zh-CN.md)

| 方法与路径 | 用途 | 约定 |
| --- | --- | --- |
| `GET /evolution/suites` | 列出套件 | Page |
| `GET /evolution/suites/{suite}` | 单个套件；用例内容受划分可见性约束 | |
| `POST /bots/{bot_id}/evolution/evaluations` | 仅限操作员：在某个套件上对修订版进行临时评估 | key；202 + 操作 id |
| `GET /bots/{bot_id}/evolution/evaluations/{evaluation}` | 评估结果（id 来自操作的 `result`） | |

共享约定示例：启动，然后按 id 查询操作。

```http
POST /openapi/v1/bots/bot_123/evolution/evaluations
Content-Type: application/json
Idempotency-Key: 0b6f7c52-3d0e-4c1b-9a63-8f2a7d0e5c11
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"revision": "sha256:b73d…", "suite": "support-refunds@3"}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// 202; Location: /openapi/v1/bots/bot_123/evolution/operations/op_19a
{
  "code": 202000,
  "message": "Accepted",
  "data": {"operation_id": "op_19a"},
  "request_id": "7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b"
}
```

```http
GET /openapi/v1/bots/bot_123/evolution/operations/op_19a
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// 200 while it runs; Retry-After: 30
{
  "code": 200000,
  "message": "OK",
  "data": {"id": "op_19a", "kind": "evaluation", "status": "running",
           "created_at": "2026-10-08T10:02:11Z", "updated_at": "2026-10-08T10:05:40Z",
           "result": null, "error": null},
  "request_id": "8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b3c"
}
```

当 `status` 为 `succeeded` 后，`result` 为 `{"evaluation_id": "ev_310"}`，并
通过 `GET /bots/{bot_id}/evolution/evaluations/ev_310` 读取该评估。

### 13.7 晋升 — [08-promotion.zh-CN.md](08-promotion.zh-CN.md)

| 方法与路径 | 用途 | 约定 |
| --- | --- | --- |
| `GET /bots/{bot_id}/evolution/candidates/{candidate}` | 候选报告：diff、验证、门禁决定 | |
| `GET /bots/{bot_id}/evolution/review-queue` | 等待人工评审的候选 | Page |
| `POST /bots/{bot_id}/evolution/candidates/{candidate}:approve` | 批准一个候选 | key；检查状态 |
| `POST /bots/{bot_id}/evolution/candidates/{candidate}:reject` | 拒绝一个候选 | key；检查状态 |
| `POST /bots/{bot_id}/genome/promotions` | 移动 `active`（包括回到旧版本） | key；对 `active` 进行 CAS |

共享约定示例：在包含冒号的 id 上调用自定义方法。

```http
POST /openapi/v1/bots/bot_123/evolution/candidates/sha256%3Ac41e…:approve
Content-Type: application/json
Idempotency-Key: review-bot_123-sha256:c41e…-approve
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"reason": "Fixes partial-refund misses; reviewed diff and validation report"}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// 200; approving again with the same key replays this answer
{
  "code": 200000,
  "message": "OK",
  "data": {"candidate_id": "sha256:c41e…", "run_id": "run_7f3", "decision": "approved",
           "risk_tier": "T2", "decided_at": "2026-10-08T11:20:00Z"},
  "request_id": "9a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d"
}
```

批准一个已被拒绝的候选返回 `409020`；批准一个门禁不允许的候选属于策略系列
（退出码 5）。谁可以批准哪个风险等级在 [08-promotion.zh-CN.md](08-promotion.zh-CN.md) 中定义。

### 13.8 操作 — 本文档

唯一的公开操作资源，由每个以带 `{operation_id}` 的 `202` 应答的端点共享
（[07-verification.zh-CN.md](07-verification.zh-CN.md) 中的临时评估、
[05-experiment-ledger.zh-CN.md](05-experiment-ledger.zh-CN.md) 中的实验记录导出）。由
`apps/evolution` 提供。

| 方法与路径 | 用途 | 约定 |
| --- | --- | --- |
| `GET /bots/{bot_id}/evolution/operations/{operation}` | 操作状态，以及完成后的结果或错误 | |
| `POST /bots/{bot_id}/evolution/operations/{operation}:cancel` | 取消一个操作 | key；按状态幂等 |

#### GET /bots/{bot_id}/evolution/operations/{operation}

按 id 查询操作：`Operation{id, kind, status, result?, error?,
created_at, updated_at}`（§2.5）。由流水线、UI 后端、SDK 的 `wait` 辅助方法
以及 `avn … --wait` 调用。每次查询都会立即返回；响应可能带有
`Retry-After`。

```http
GET /openapi/v1/bots/bot_123/evolution/operations/op_19a
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// 200 after the export finished
{
  "code": 200000,
  "message": "OK",
  "data": {"id": "op_19a", "kind": "ledger_export", "status": "succeeded",
           "created_at": "2026-10-08T09:30:00Z", "updated_at": "2026-10-08T09:31:12Z",
           "result": {"digest": "sha256:e7a2…", "format": "filesystem", "entries": 412},
           "error": null},
  "request_id": "b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6"
}
```

错误：`404000` 未知操作，或属于其他 bot 的操作。

#### POST /bots/{bot_id}/evolution/operations/{operation}:cancel

取消一个操作。按状态幂等：取消一个已取消的操作会原样返回它；取消一个已经
成功或失败的操作返回 `409020`。

```http
POST /openapi/v1/bots/bot_123/evolution/operations/op_19a:cancel
Idempotency-Key: cancel-op_19a
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// 200
{
  "code": 200000,
  "message": "OK",
  "data": {"id": "op_19a", "kind": "evaluation", "status": "cancelled",
           "created_at": "2026-10-08T10:02:11Z", "updated_at": "2026-10-08T10:07:02Z",
           "result": null, "error": null},
  "request_id": "c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7"
}
```

错误：`404000` 未知操作；`409020` 操作已经结束。

## 14. 示例

### 14.1 夜间流水线（Python SDK）

一个定时作业在 `user_owner_5` 拥有的 bot 上启动今晚的运行、等待其完成，
并批准所有者的进化策略配置允许流水线批准的候选。由于流水线不是所有者，
每次调用都传入 `entity_id`。键是确定性的，因此即使作业本身崩溃并重新运行，它也会
重新关联到同一次运行，而不是启动第二次运行。

```python
import asyncio
from avernet_evolution import Client, PolicyDenied, Transient

async def nightly(owner_id: str, bot_id: str, binding: str, day: str) -> None:
    c = Client.from_env()                                   # base URL and client settings from configuration
    # The pipeline acts on another user's bot, so every call names the owner (entity_id).
    run = await c.runs.start(bot_id, entity_id=owner_id, binding=binding,
                             idempotency_key=f"nightly-{owner_id}-{bot_id}-{day}")  # same key on every rerun of this job
    try:
        run = await c.runs.wait(bot_id, run.run_id, entity_id=owner_id, timeout_s=3 * 3600)  # short lookups by id
    except Transient:
        return                                              # still running; the next invocation re-attaches
    print(run.run_id, run.status, f"request_id={run.request_id}")
    if run.status != "completed":
        return                                              # failed / cancelled / budget_exhausted: nothing to approve

    for cand in await c.runs.candidates(bot_id, run.run_id, entity_id=owner_id):
        report = await c.review.report(bot_id, cand.candidate_id, entity_id=owner_id)
        if report["gate"]["decision"] != "needs_review":
            continue
        try:
            await c.review.approve(bot_id, cand.candidate_id, entity_id=owner_id, reason="nightly auto-policy",
                                   idempotency_key=f"nightly-{owner_id}-{bot_id}-{day}/approve/{cand.candidate_id}")
        except PolicyDenied as e:                           # the owner's policy does not let pipelines approve this tier
            print("left for human review:", cand.candidate_id, e.code, e.request_id)

asyncio.run(nightly("user_owner_5", "bot_123", "bind_01", "2026-10-08"))
```

### 14.2 使用 `avn` 的 CI 脚本

一个 CI 作业在技能变更后运行。它使用退出码而不是解析文本，并保存幂等键，
以便重试的 CI 步骤重用它。

```bash
set -u
KEY="ci-${CI_PIPELINE_ID}-bot_123"            # stable across retries of this pipeline
out=$(avn evolve run start --bot bot_123 --binding bind_01 \
        --idempotency-key "$KEY" --wait --timeout 2h --output json)
rc=$?
case $rc in
  0) status=$(echo "$out" | jq -r '.data.status')
     echo "run $(echo "$out" | jq -r '.data.run_id') ended: $status" ;;
  5) echo "policy refused the run (evolution frozen or binding invalid)"; exit 1 ;;
  6) echo "budget ceiling reached; skipping"; exit 0 ;;
  7) echo "transient; rerun this step (same key re-attaches)"; exit 1 ;;
  *) echo "$out" | jq -r '.request_id' >&2; exit 1 ;;
esac
```

成功时打印的 JSON：

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "code": 200000,
  "message": "OK",
  "data": {"run_id": "run_7f3", "status": "completed", "binding": "bind_01"},
  "request_id": "a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5",
  "idempotency_key": "ci-48211-bot_123"    // added by avn for commands that take a key
}
```

### 14.3 安全地编辑进化策略配置（Python SDK）

使用 `If-Match` 进行读-改-写；遇到 `412` 时重新读取并重新应用。

```python
from avernet_evolution import Client, PreconditionFailed

async def raise_budget(c: Client, bot_id: str, binding_id: str, max_usd: int) -> None:
    for _ in range(3):
        policy, etag = await c.policy.get(bot_id)        # caller is the owner: no entity_id
        for b in policy.bindings:
            if b.id == binding_id:
                b.budget.max_usd = max_usd
        try:
            await c.policy.put(bot_id, policy, if_match=etag)
            return
        except PreconditionFailed:
            continue                                         # someone else changed it; read again
    raise RuntimeError("policy kept changing; giving up")
```

### 14.4 UI 后端中的评审队列（TypeScript SDK）

```typescript
import { Client, PolicyDenied } from "@avernet/evolution";

const client = Client.fromEnv();

// entityId: the bot's owner, passed when it is not the signed-in user (omitted = caller)
export async function reviewQueue(botId: string, entityId?: string) {
  const items = [];
  for await (const item of client.review.iterQueue(botId, { entityId })) {        // walks all pages
    const report = await client.review.report(botId, item.candidateId, { entityId });
    items.push({ candidate: item.candidateId, tier: report.riskTier, gate: report.gate, diff: report.diff });
  }
  return items;
}

export async function approve(botId: string, candidateId: string, reason: string, key: string, entityId?: string) {
  try {
    return await client.review.approve(botId, candidateId, { reason, idempotencyKey: key, entityId });
  } catch (e) {
    if (e instanceof PolicyDenied) return { refused: true, code: e.code, requestId: e.requestId };
    throw e;
  }
}
```

UI 在评审者打开批准对话框时生成一次批准键，因此双击或网络重试只会记录
一项决定。

### 14.5 回到旧版本

回到旧版本就是晋升一个更早的修订版；没有单独的回滚调用。

```bash
avn genome promote --bot bot_123 --revision r41 \
  --reason "r42 raised refund escalations" --yes --output json
```

`avn` 通过 `GET /bots/{bot_id}/genome/refs` 和 `GET /bots/{bot_id}/genome/revisions`
将 `r41` 解析为其修订版 id，然后调用
`POST /bots/{bot_id}/genome/promotions`。对于服务型 bot，该修订版会通过现有的
发布流程作为下一个版本发布（[08-promotion.zh-CN.md](08-promotion.zh-CN.md)）。

## 15. 交互

| 其他部分 | 方向 | 流转内容 |
| --- | --- | --- |
| [01-genome.zh-CN.md](01-genome.zh-CN.md)（基因组注册表，Backend） | API → 基因组 | 修订版、引用、diff 和内容调用；引用上的 CAS；按内容幂等的修订版创建 |
| [02-experience.zh-CN.md](02-experience.zh-CN.md) | API → 经验 | 片段查询、反馈记录 |
| [03-strategy.zh-CN.md](03-strategy.zh-CN.md)（进化策略注册表） | API → 注册表 | 进化策略注册（`avn strategy publish`）、列表、能力目录；进化策略 SDK 是一个独立的包 |
| [05-experiment-ledger.zh-CN.md](05-experiment-ledger.zh-CN.md) | API → 实验记录 | 条目查询、以操作形式进行的导出 |
| [06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md) | API → 运行 | 进化策略配置（ETag）、运行提交（幂等键 → 运行 id）、按 id 查询状态、取消；预算、紧急停止开关和绑定检查产生策略和预算错误系列；内部作业协议使用相同的约定 |
| [07-verification.zh-CN.md](07-verification.zh-CN.md) | API → 验证 | 套件、以操作形式进行的临时评估 |
| [08-promotion.zh-CN.md](08-promotion.zh-CN.md) | API → 晋升 | 候选报告、评审队列、批准/拒绝、晋升（包括回到旧版本） |
| 流水线与 CI | 调用方 → API | 客户端 SDK 或 `avn`；确定性的幂等键；退出码 |
| UI 后端 | 调用方 → API | TypeScript SDK；编辑进化策略配置时使用 ETag；每个用户操作一个键 |
| 人类操作员 | 调用方 → API | `avn` 和 UI |
| Backend OpenAPI v1 适配器 | 被本层复用 | `Envelope`、`ErrorEnvelope`、`Page`、`PageParams`、envelope 构造器与错误映射 |

## 16. 待定决策

| ID | 问题 | 当前立场 |
| --- | --- | --- |
| A-1 | `avn` 是一个新二进制，还是某个现有 CLI 的子命令组？ | 推荐：用 Rust 编写一个新的 `avn` 二进制，遵循 `bcs-cli` 的约定，并为日后加入其他平台命令留有空间。目前不存在通用的 Avernet CLI |
| A-2 | 幂等记录保留多久？ | 提议：至少 30 天，长于任何调用方的重试周期（第二天重试的夜间作业仍必须能重新关联）。随 RSI-07 一并决定 |
| A-3 | 是否为大型仅追加集合（片段、实验记录条目）采用游标分页？ | 提议：为保持 v1 一致性保留页码；如果 `total` 或页偏移成为问题，再为这两个集合增加不透明的 `cursor` |
| A-4 | 结构化的错误详情（例如在绑定检查中哪项能力没有提供者）？ | OpenAPI v1 规定错误时 `data` 为 `null`，并使用固定消息。可选方案：保持现状，将详情编码到子码中；或仅对进化错误允许 `data.details` 对象。尚未决定 |
| A-5 | 是否为写入提供统一的仅校验模式（`dry_run`）？ | `genome patch apply --dry-run` 需要它；其他端点是否提供由其负责文档决定 |
| A-6 | 一份合并的 OpenAPI 文档，还是每个服务一份？ | 提议：每个服务发布其负责的部分；由一个构建步骤将它们合并为一份公开文档，SDK 和 CLI 都由它生成 |
| A-7 | `--wait` 是否应将运行的终态映射为退出码？ | 提议：否；退出码反映 API 调用，脚本读取 `.data.status`。如果 CI 用户提出需求再重新考虑 |
| A-8 | 事件投递（在 [work-items.zh-CN.md](work-items.zh-CN.md) 中列为 Q1 到 Q3：“CLI 二进制、默认的 bot 运行请求、事件投递”；A-1 即 Q1，A-8 即 Q3） | 当前设计没有回调或 webhook 通道：按 id 查询状态是唯一机制。推送通道将是一项补充，而不是替代 |

Q2（受管 bot 是否默认可以请求运行）属于已推迟的 bot 调用方
（[§3](#3-调用方)）。
