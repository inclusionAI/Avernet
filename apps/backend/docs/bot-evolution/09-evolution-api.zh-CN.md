# 进化 API

> English version: [09-evolution-api.md](09-evolution-api.md)

> 状态：草稿（DRAFT）。[Bot 进化架构](design.zh-CN.md)中的服务。
> 公开访问层：所有进化端点共享的约定、全部公开端点的索引，以及发布这些
> 端点的 OpenAPI 文档。

## 1. 目的与范围

Bot 进化平台通过位于公开前缀 `/openapi/v1` 下的**一个资源 API** 访问，它涵盖
基因组端点和进化端点，并由 OpenAPI 文档描述。这个 API 就是全部公开接口面：
UI 后端、流水线和 CI 都通过 HTTP 调用它。

在出现具体用例之前，客户端 SDK 和 CLI 不在范围内。如果以后增加，它们由
OpenAPI 文档生成，且不添加 API 所不具备的能力，因此调用方之间不会出现偏差。

**本文档负责：**

| 本文档负责 | 含义 |
| --- | --- |
| 共享 API 约定 | Envelope、请求 id、JSON 规则、路径 id 与自定义方法、幂等键、ETag 与 `If-Match`、错误码、分页、按 id 跟踪的长时工作 |
| 端点索引 | 存在哪些公开端点，以及每个端点由哪份文档负责（[§10](#10-端点索引)） |
| OpenAPI 文档及其网关路由 | 每个提供端点的服务发布哪份文档、网关如何把每个路径路由到其服务、兼容性门禁，以及契约测试（[§11](#11-openapi-文档与网关路由)） |

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
共享操作端点由新模块 **`apps/evolution`** 提供（决策 D-1，
[ADR 0021](../../../../docs/adr/0021-evolution-control-plane-in-apps-evolution.zh-CN.md)）。本文档将二者呈现为一个公开的 `/openapi/v1`
接口面。
本文档中的约定在每个服务中实现一次，位于各服务的 HTTP 适配层，从而使核心
逻辑保持与传输无关。
Backend 现有的 OpenAPI v1 适配器已经提供了本文档复用的 envelope 和
分页类型
（`apps/backend/src/agentclaw/community/adapters/http/openapi_v1/contracts.py`：
`Envelope`、`ErrorEnvelope`、`Page`、`PageParams`；
`responses.py`：`envelope`、`page`、`created`、`accepted`、`deleted`，以及
`@envelope_errors` 装饰器；文档见
`apps/backend/docs/openapi-v1/README.md`）。[work-items.zh-CN.md](work-items.zh-CN.md)
中工作项 RSI-07 的交付物是这些端点的 OpenAPI 文档，以及它们的网关路由和共享的
一致性测试。

## 2. 领域模型

这里的类型是共享的线上传输约定，而不是进化资源本身（这些资源在各自的负责
文档中定义）。

| 类型 | 含义 | 负责方 | 生命周期 |
| --- | --- | --- | --- |
| `Envelope[T]` | 统一的响应包装：`code`、`message`、`data`、`request_id` | 本文档（复用 OpenAPI v1） | 每个响应 |
| `ErrorEnvelope` | 失败时的同一包装，`data` 始终为 `null` | 本文档（复用 OpenAPI v1） | 每个响应 |
| `Page[T]` / `PageParams` | 列表结果的一页（`total`、`items`）及其控制参数（`page`、`page_size`） | 本文档（复用 OpenAPI v1） | 每个请求 |
| `BotRef` | 一个 bot 的完整标识：`owner_id` 加 `bot_id`，因为单独的 `bot_id` 在不同用户之间并不唯一（[§2.6](#26-botrefbot-标识)） | 本文档；所有进化记录都使用它 | 在 bot 的整个生命周期内固定 |
| `IdempotencyRecord` | 平台对一个幂等键的记忆。**键**是客户端 `Idempotency-Key` 请求头的值，例如 `nightly-bot_123-2026-10-08`；记录把它映射到**结果 id**，即携带该键的首个请求所创建或启动之物的 id，例如运行 `run_7f3`，外加请求的指纹。用同一个键重试会拿回 `run_7f3`，而不是再启动一个运行（[§2.3](#23-idempotencyrecord)） | 本文档；由各服务存储 | 在首个携带该键的请求时创建；在保留期内保留 |
| `Precondition` | 写入时的版本检查。对客户端整体替换的资源（目前只有 Bot 的绑定）发起 `GET` 时返回 `ETag` 响应头，即一个版本标签，例如 `"bind-v12"`；客户端在 `PUT` 时通过 `If-Match` 请求头回传它，如果资源在那次读取之后已变化，服务器以 `412` 拒绝这次写入（[§2.4](#24-preconditionetag-与-if-match)） | 本文档 | 每个资源版本 |
| `Operation` | 由请求启动、按 id 查询的长时工作单元（`queued`、`running`、`succeeded`、`failed`、`cancelled`） | 概念在 [03-strategy.zh-CN.md](03-strategy.zh-CN.md) 中定义；公开资源及其查询端点由本文档负责（[§10.8](#108-操作--本文档)） | 由 `202` 响应创建；完成后即为终态 |

### 2.1 Envelope 与 ErrorEnvelope

每个 JSON 响应，无论成功还是失败，都是一个 envelope。这是现有的 OpenAPI v1
形态；进化端点原样采用它，从而调用方以相同方式处理基因组端点和进化
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
// An error envelope: the bindings changed since the caller read them
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
from enum import StrEnum

class IdempotentResultKind(StrEnum):
    """What a key can create or start (one value per row of the §5.3 table)."""
    RUN = "run"
    OPERATION = "operation"
    REVISION = "revision"
    PROMOTION = "promotion"
    REVIEW_DECISION = "review_decision"   # an approve or reject of a review item
    FEEDBACK = "feedback"

@dataclass(frozen=True)
class IdempotencyRecord:
    bot: BotRef            # scope: the bot addressed by the request (owner + bot id, §2.6)
    key: str               # the client's Idempotency-Key header value
    fingerprint: str       # sha256 of the RFC 8785 canonical form of {method, path, body}
    result_kind: IdempotentResultKind
    result_id: str         # id of what the first request created or started, e.g. "run_7f3"
    created_at: datetime
    expires_at: datetime   # end of the retention window (open decision A-2, §15)
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
  "key": "nightly-bot_123-2026-10-08",
  "fingerprint": "sha256:5d1e…",
  "result_kind": "run",
  "result_id": "run_7f3",
  "created_at": "2026-10-08T02:00:03Z",
  "expires_at": "2026-11-07T02:00:03Z"
}
```

### 2.4 Precondition（ETag 与 If-Match）

**ETag** 是服务器随资源一起返回的版本标签。想要修改该资源的客户端在
**`If-Match`** 请求头中回传该标签；如果资源在此期间已发生变化，服务器以
`412 Precondition Failed` 拒绝这次写入，而不是悄无声息地覆盖另一位写入者
的修改。

它防止的是**丢失更新**，即同一资源的两个写入者之间的竞态。例如：所有者在 UI 中打开 bot_123 的绑定，同时一条流水线也读取了它们；两者都拿到 `ETag: "bind-v12"`。流水线提高了 `bind_01` 的预算并先保存（`If-Match: "bind-v12"` 匹配；绑定变为 `bind-v13`）。随后所有者为 `bind_02` 保存新的触发器，带的是 `If-Match: "bind-v12"`。没有这项检查，所有者的保存会用其过时的副本替换整个列表，悄无声息地撤销预算修改。有了它，所有者收到 `412`，重新读取 `bind-v13`，并在其上重新应用触发器修改（即 [§6](#6-并发etagif-match-与比较并交换) 中的循环）。

```python
@dataclass(frozen=True)
class Precondition:
    etag: str              # opaque, strong, quoted on the wire: "\"bind-v12\""
    resource: str          # the path it belongs to
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// What a caller keeps after GET /bots/bot_123/evolution/bindings, to send back as If-Match
{"etag": "\"bind-v12\"", "resource": "/openapi/v1/bots/bot_123/evolution/bindings"}
```

### 2.5 Operation（公开视图）

**操作**（operation）是可能比一个短 HTTP 请求持续更久的工作：一次评估、
一次实验记录导出。启动它会立即返回带有 `{operation_id}` 的
`202 Accepted`；随后调用方通过
`GET /bots/{bot_id}/evolution/operations/{operation}` 按 id 查询其状态，这是唯一的
公开操作资源（[§10.8](#108-操作--本文档)）。同一概念也在进化策略内部使用
（智能体会话、训练评估）；见
[03-strategy.zh-CN.md](03-strategy.zh-CN.md)。运行遵循相同的模式，但有其
自己的状态值（[06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)）。

```python
class OperationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

class OperationKind(StrEnum):
    """The public operation kinds."""
    EVALUATION = "evaluation"          # an ad-hoc evaluation (07-verification.md)
    LEDGER_EXPORT = "ledger_export"    # a ledger export (05-experiment-ledger.md)

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
  "result": {"digest": "sha256:e7a2…", "format": "filesystem", "entries": 412},
  "error": null
}
```

### 2.6 BotRef（bot 标识）

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

在第一次迭代中，API 有三类调用方。它们通过 HTTP 使用相同的端点；每类调用方可以做
什么由负责文档决定（晋升和批准见 [08-promotion.zh-CN.md](08-promotion.zh-CN.md)），
而不是为每类调用方提供单独的 API。

| 调用方 | 示例 | 接入面 | 典型调用 |
| --- | --- | --- | --- |
| **确定性流水线 / CI** | 夜间作业、技能变更后的 CI、产品后端 | 向 `/openapi/v1` 发送 HTTP 请求 | 启动运行、查询其状态、读取候选报告、在所有者的自动晋升策略允许范围内批准 |
| **UI 后端** | 工作台的进化页面（过渡期为 AgentEvolve UI） | 向 `/openapi/v1` 发送 HTTP 请求 | 人类在 UI 中所做的一切：编辑绑定、评审队列、报告、晋升、浏览实验记录 |
| **人类操作员** | Bot 所有者、租户管理员、研究人员 | 工作台 UI，它经由 UI 后端调用 API | 一切操作，包括评审、晋升以及回到旧版本 |

进化策略作业工作器是平台服务，通过进化策略 SDK 使用内部作业协议，而不是
这个公开 API（[06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)、[03-strategy.zh-CN.md](03-strategy.zh-CN.md)）。

Bot 调用方（驱动自身进化的 bot，或作为作业运行器的 bot，以及它们会使用的
技能或 MCP 适配器）随 DR-3 一并推迟
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
  `/candidates/sha256%3Ac41e…:approve` 没有歧义。调用方
  按响应中出现的形式获取 id，并在构造路径时对其编码。
- **每个 bot 的序号。** 修订版的 `seq`（向人类展示为 `r41`）不是标识
  （[01-genome.zh-CN.md](01-genome.zh-CN.md)）。API 接受修订版 id；只有在
  负责文档说明的端点上才接受 `r41` 形式（例如
  [08-promotion.zh-CN.md](08-promotion.zh-CN.md) 中的 `POST /bots/{bot_id}/genome/promotions`）。
  其他地方，调用方先通过 `GET /bots/{bot_id}/genome/revisions` 解析 `r41`。

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

调用方为每个失败的请求记录其请求 id；支持人员要靠它找到该请求。

### 4.3 JSON

- 请求体和响应体都是 JSON（`application/json`），UTF-8 编码。
  本设计中的一切新内容都是 JSON；Bot Config Manifest 与现在一样保持 YAML
  （[01-genome.zh-CN.md](01-genome.zh-CN.md)）。唯一的例外是进化策略注册，它以
  `multipart/form-data` 上传智能体定义目录（[03-strategy.zh-CN.md](03-strategy.zh-CN.md)）。
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
- 调用方忽略其不认识的响应字段。
- 文档中标明为封闭的状态枚举（运行状态、操作状态、判定状态）在 v1 内不会
  新增取值；新增取值将是破坏性变更。其他枚举是开放的，客户端将未知取值视为
  不透明值。
- 删除或重命名字段，或收紧校验，需要端点的新版本。

## 5. 幂等键

任何创建资源或启动工作的 `POST` 都接受 **`Idempotency-Key`** 请求头。运行
提交是幂等的，并由平台保证：使用相同键重复 `POST` 会返回相同的运行 id，
不会启动任何新的东西。端点索引中标记为“key”的每个端点同样如此
（[§10](#10-端点索引)）。

### 5.1 客户端规则

- 键是一个**由客户端选定的字符串**，长度为 1 到 255 个可打印 ASCII
  字符；推荐的字符集为 `[A-Za-z0-9._:/-]`。它放在请求头中原样
  发送；只有路径中的 id 才做百分号编码（[§4.1](#41-路径资源与自定义方法)）。
- 它**在一个逻辑请求的每次重试中保持相同**，并且**对不同请求各不
  相同**。每个逻辑动作在首次尝试之前生成一次，并在每次重试中重用。
- 它**不得**是在重试之间会变化的东西。在发送时取的时间戳，或每次尝试生成的
  随机值，都会使每次重试看起来像一个新请求，从而失去意义。
- 如果进程可能在拿到应答之前重启，就把键与作业状态一起**持久化**，或由使
  请求唯一的因素确定性地生成它。只保存在内存中的键无法保护由新进程发起的
  重试。
- 只自动重试 `GET`、带 `If-Match` 的 `PUT`，以及带键的 `POST`。绝不重试未带
  键发送的 `POST`：第一次尝试可能已经成功。
- 遇到瞬时失败时，采用带抖动的指数退避，并在响应带有 `Retry-After` 时遵循它。
  哪些代码属于瞬时失败见 [§7.2](#72-重试指引)。

各类调用方的良好键：

| 调用方 | 键 | 示例 |
| --- | --- | --- |
| 流水线（一次性逻辑请求） | 一个 UUID，**在首次尝试之前创建一次**，并与作业状态一起保存 | `0b6f7c52-3d0e-4c1b-9a63-8f2a7d0e5c11` |
| 流水线（定时，每个周期一个） | 由使请求唯一的因素确定性地生成 | `nightly-bot_123-2026-10-08` |
| UI 后端（一次用户操作） | 用户打开该操作（例如批准对话框）时创建的一个 UUID，因此双击或网络重试只记录一项决定 | `4b0f6c1e-9a7d-4f53-8d1e-2c6a9e1b7f02` |
| 平台触发器（按计划触发的绑定） | `<binding_id>/<scheduled_fire_time>` | `bind_01/2026-10-08T02:00:00Z` |
| 进化策略（内部，作业协议） | `<run_id>/<own step>` | `run_7f3/round-2/tune` |

触发器键中的计划触发时间是计划所指定的时间，而不是触发器实际运行的时间，
因此即使触发器延迟触发或触发两次，也仍只产生一次运行。

### 5.2 平台规则

- 平台存储 **`(owner_id, bot_id, key) → result id`**，以及请求的指纹
  （对规范化 `{method,
  path, body}` 的 `sha256`）。对于 bot 范围的路径，作用域是被寻址的 bot：
  路径中的 `bot_id` 及其所有者（`entity_id`，省略时为调用方），因此两个
  所有者下 `bot_id` 相同的 bot 永远不会共享键。
- 该记录与其指向的资源**在同一事务中**写入，因此崩溃永远不会留下一个
  没有键的已创建运行，或一个没有运行的键。
- **相同键、相同请求**（指纹匹配）：平台返回原始结果：该端点的成功状态码
  （每个端点只有一个，例如运行是 `202`，因此无需存储）和相同的结果 id，`data` 中为资源的**当前**状态（一个
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
  决策（[§15](#15-待定决策)）；过期后，该键可以被重用。
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
`PUT /config-manifest` 两者都没有。基因组不改变该端点：通过基因组 API 进行的
编辑改由引用（ref）上的比较并交换保护，而直接的 Manifest 编辑会在事后作为漂移
被检测到（[01-genome.zh-CN.md](01-genome.zh-CN.md) §5.5）。

- 对可变资源的 `GET` 返回一个 `ETag` 响应头（一个强的、不透明的、带引号的
  标签）。
- `PUT` 必须发送 `If-Match: <etag>`。如果资源在此之后发生了变化，服务器
  应答 `412`（`412000`）。如果缺少 `If-Match`，则应答
  `428 Precondition Required`（`428000`），因此客户端不会意外跳过这项
  检查。
- 公开接口面上的第一个可变资源是 bot 的绑定
  （`GET|PUT /bots/{bot_id}/evolution/bindings`）。

调用方通过**读-改-写循环**修改这类资源：`GET` 资源并保存其 `ETag`；在该副本上
应用修改；以 `If-Match` 设为保存的标签执行 `PUT`。遇到 `412` 表示有其他人在
这期间写入：重新 `GET`（新的响应体、新的 `ETag`），把修改重新应用到新副本上，
再次 `PUT`。几轮仍未成功时，调用方停下并报告冲突，而不是无限循环。

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

每份负责文档为其自身的失败定义子码。本文档确定共享的子码，以及调用方据以
分支的**系列**：

| 代码 | 含义 | 是否重试？ |
| --- | --- | --- |
| `400000` | 请求体或查询未通过校验（schema、枚举、范围） | 否：修正请求 |
| `404000` | 资源未找到（bot、运行、修订版、候选、进化策略、套件、条目） | 否 |
| `409001` | 幂等键被用于不同的请求 | 否：客户端缺陷 |
| `409002` | 使用此幂等键的请求仍在处理中 | 是，使用相同的键 |
| `409010` | 引用的比较并交换失败：`expected_revision` 已过时 | 重新读取后 |
| `409011` | 进化策略版本已以不同内容注册 | 否 |
| `409020` | 动作与资源状态冲突（例如批准已被拒绝的候选、取消已结束的运行） | 否 |
| `412000` | `If-Match` 与当前 `ETag` 不匹配 | 重新读取后 |
| `428000` | 需要 `If-Match` 但缺失 | 添加后 |
| `403xxx` / `422xxx`（策略系列） | 请求违反了 Bot 的绑定、其基因组 policy 或平台规则：基因不在 Bot 的 `mutable_genes` 或绑定的 `allowed_genes` 中、绑定检查失败、进化被紧急停止开关冻结、进化策略已禁用、候选在门禁下不可晋升 | 否 |
| `403100` 区段（预算系列） | 预算上限拒绝了请求（每 bot 或每租户的日或月上限、每日最大晋升次数） | 在上限重置之前不可 |
| `429000` | 被限流 | 是，在 `Retry-After` 之后 |
| `502000`、`503000`、`504000` | 上游或服务不可用 | 是，使用相同的键 |
| `500000` | 意外的服务器错误 | 仅在携带幂等键时 |

策略系列和预算系列中的具体子码由负责文档设定（预算、紧急停止开关和绑定
检查见 [06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)；基因组策略配置见
[01-genome.zh-CN.md](01-genome.zh-CN.md)；门禁见
[08-promotion.zh-CN.md](08-promotion.zh-CN.md)）。

**被拒绝的候选不是错误。** 验证拒绝一个候选，或一次运行以
`budget_exhausted` 结束，都是在资源状态中报告的正常结果，返回 `200`。

### 7.2 重试指引

- `GET` 总是可以安全重试。
- 带幂等键的 `POST` 在瞬时失败（`409002`、`429`、`5xx`、网络错误，或完全
  没有响应）后，使用**相同的键**重试是安全的。
- 不带键的 `POST` 从不自动重试。
- 带 `If-Match` 的 `PUT` 可以安全重试；重试后出现 `412` 意味着有其他人在
  这期间写入（或第一次尝试已成功）：重新读取并做决定
  （[§6](#6-并发etagif-match-与比较并交换)）。
- 采用带抖动的指数退避，并遵循 `Retry-After`
  （[§5.1](#51-客户端规则)）。

## 8. 分页与过滤

- 列表使用 `page`（从 1 开始）和 `page_size`（1 到 100，默认 20），并
  返回 `Page {total, items}`，与当前 OpenAPI v1 一致
  （`contracts.py` 中的 `PageParams`）。
- 过滤器是以所过滤字段命名的查询参数
  （`status=`、`revision=`、`outcome=`、`run=`、`strategy=`）；时间范围
  使用 `since=` 和 `until=`（RFC 3339）。每份负责文档列出其过滤器。
- 除非负责文档另有说明，默认排序为按创建时间从新到旧；时间戳相同时排序
  稳定（按 id）。
- 要读取整个集合，调用方使用相同的过滤器依次请求 `page=1, 2, …`，直到取得
  `total` 个条目或收到空页。
- 大型、仅追加的集合（片段（episode）、实验记录条目）可能需要游标分页而非
  页码，因为 `total` 开销大，且随着条目追加页会发生偏移。这是一项待定决策
  （[§15](#15-待定决策)）；在决定之前，沿用现有约定。

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
4. **id 是唯一的句柄。** 没有回调或 webhook 通道。
   等待就是重复查询；调用方在自身超时后放弃时，工作仍在运行，之后按同一 id
   查询（或用同一个键重复启动）会再次找到它。
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

## 10. 端点索引

所有公开路径都位于前缀 **`/openapi/v1`** 下，并相对于它书写。每个端点的
完整 schema、示例和错误都在其负责文档中；本节列出每个公开端点，指明其负责
文档，并在每组中展示一次共享约定。每组由哪个服务提供，见
[§1](#1-目的与范围) 中的“运行位置”。图例：**key** = 接受 `Idempotency-Key`；**202** = 启动
按 id 查询的长时工作；**ETag** = 写入时必须携带 `If-Match`；
**CAS** = 请求体中的比较并交换。

### 10.1 基因组 — [01-genome.zh-CN.md](01-genome.zh-CN.md)

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
{"revision": "sha256:7c1e…", "expected_revision": "sha256:a90b…"}   // r42 replaces r41, only if draft is still r41
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

### 10.2 经验 — [02-experience.zh-CN.md](02-experience.zh-CN.md)

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

### 10.3 进化策略注册表 — [03-strategy.zh-CN.md](03-strategy.zh-CN.md)

| 方法与路径 | 用途 | 约定 |
| --- | --- | --- |
| `GET /evolution/strategies` | 列出已注册的进化策略（`engine=`、`conformance=`） | Page |
| `POST /evolution/strategies` | 注册一个进化策略版本（及其智能体定义） | key；按内容幂等 |
| `GET /evolution/strategies/{id}/versions/{version}` | 单条注册记录及其一致性状态 | 不可变 ETag |
| `GET /evolution/capabilities` | 能力目录 | |

共享约定示例：以不同内容重新注册一个已存在的版本是冲突，而不是悄无声息的
覆盖。请求是 `multipart/form-data`，包含一个 `registration` 部分，以及每个智能体
定义一个 `definition:<name>` 部分，如 [03-strategy.zh-CN.md](03-strategy.zh-CN.md)
所定义；这里只展示 `registration` 部分，并做了缩减。

```http
POST /openapi/v1/evolution/strategies
Content-Type: multipart/form-data; boundary=…
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Part "registration" (other needs and the definition parts omitted)
{
  "id": "clawevolve/bot-evolution",
  "version": "2.0.0",
  "runtime": {"kind": "job_worker", "image": "registry.example/clawevolve@sha256:…"},
  "needs": {"experience.sessions@1": {}, "evaluation.train@1": {}}
}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// 409: version 2.0.0 already exists with a different record; register 2.0.1 instead
{
  "code": 409011,
  "message": "Strategy version already registered with different content",
  "data": null,
  "request_id": "2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c"
}
```

### 10.4 实验记录 — [05-experiment-ledger.zh-CN.md](05-experiment-ledger.zh-CN.md)

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

### 10.5 进化运行 — [06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)

| 方法与路径 | 用途 | 约定 |
| --- | --- | --- |
| `GET /bots/{bot_id}/evolution/bindings` | bot 的绑定（绑定列表） | 返回 ETag |
| `PUT /bots/{bot_id}/evolution/bindings` | 替换绑定（在此执行绑定检查） | ETag（必须携带 `If-Match`） |
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
  "data": {"run_id": "run_7f3", "status": "queued"},
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
  "data": {"run_id": "run_7f3", "status": "running"},
  "request_id": "5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f"
}
```

共享约定示例 2：绑定上的 `If-Match`。

```http
PUT /openapi/v1/bots/bot_123/evolution/bindings
Content-Type: application/json
If-Match: "bind-v12"
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
// 200 with the new ETag header: ETag: "bind-v13"
{
  "code": 200000,
  "message": "OK",
  "data": {"bindings": [{"id": "bind_01", "strategy": "clawevolve/bot-evolution@2.0.0"}]},
  "request_id": "6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a"
}
```

过时的标签得到 `412000`（[§2.1](#21-envelope-与-errorenvelope)）；缺少
`If-Match` 得到 `428000`。（上面响应中的 `data` 为示例而做了缩减；完整的
绑定形态由负责文档定义。）

**内部（不属于公开 API）。** 位于 `/evolution/v1/...` 下的作业
协议供作业工作器类进化策略认领运行并调用其上下文，由
[06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md) 负责。它不通过网关暴露，
也不在已发布的 OpenAPI 文档中。它遵循相同的约定：
JSON、使用 `<run_id>/<step>` 形式的键实现幂等启动，以及以 `202` 和操作 id
应答的长时调用。

### 10.6 验证 — [07-verification.zh-CN.md](07-verification.zh-CN.md)

| 方法与路径 | 用途 | 约定 |
| --- | --- | --- |
| `GET /evolution/suites` | 列出套件 | Page |
| `GET /evolution/suites/{suite}` | 单个套件；用例内容受划分可见性约束 | |
| `POST /bots/{bot_id}/evolution/evaluations` | 仅限操作员：在选定的套件上对修订版进行临时评估 | key；202 + 操作 id |
| `GET /bots/{bot_id}/evolution/evaluations/{evaluation}` | 评估结果（id 来自操作的 `result`） | |

共享约定示例：启动，然后按 id 查询操作。

```http
POST /openapi/v1/bots/bot_123/evolution/evaluations
Content-Type: application/json
Idempotency-Key: adhoc-bot_123-r42-2026-10-08
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"revision": "sha256:7c1e…", "suites": ["support-core"], "splits": ["validation", "regression"]}   // shortened; full body in 07-verification.md
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

### 10.7 晋升 — [08-promotion.zh-CN.md](08-promotion.zh-CN.md)

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
Idempotency-Key: review-bot_123-sha256:c41e-approve
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"reason": "Reviewed diff and verification report; escalation fix is correct."}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// 200; approving again with the same key replays this answer (data shortened; full shape in 08-promotion.md)
{
  "code": 200000,
  "message": "OK",
  "data": {
    "review": {"candidate_id": "sha256:c41e…", "status": "approved", "promotion_id": "prm_5d2"},
    "promotion": {"id": "prm_5d2", "ref": "active", "revision_id": "sha256:7c1e…", "status": "applying"}
  },
  "request_id": "9a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d"
}
```

批准一个已被拒绝的候选返回 `409020`；批准一个调用方无权批准的候选，或门禁
不允许的候选，属于策略系列。谁可以批准哪个风险等级在 [08-promotion.zh-CN.md](08-promotion.zh-CN.md) 中定义。

### 10.8 操作 — 本文档

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
created_at, updated_at}`（§2.5）。由流水线和 UI 后端在等待操作完成期间反复
调用（§9）。每次查询都会立即返回；响应可能带有
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

## 11. OpenAPI 文档与网关路由

公开 API 以 OpenAPI 文档的形式发布，并通过网关（`apps/gateway`）访问：网关把
每个 `/openapi/v1` 路径转发给提供它的服务。本节描述现有做法，以及进化端点
在其上新增的内容。标为*（提议）*的条目目前尚不存在。

### 11.1 现有做法

- **文档是构建产物。** `apps/gateway/configs/schemas/*.openapi.json` 中的文档
  已签入仓库，但不是手写的：每份文档由其上游的 `scripts/dump_openapi.py`
  生成（Backend、BaaS 和 BCS 各有一个），并由网关的
  `scripts/gate_and_publish_openapi.py` 发布；该脚本拒绝向后不兼容的变更，
  除非为一次协调变更传入 `--allow-breaking`
  （`apps/gateway/configs/schemas/README.md`；
  `apps/gateway/scripts/dump_and_publish.sh` 对每个上游执行这两端）。
- **Backend 发布 `bots.openapi.json`**：其位于 `/openapi/v1/bots`、
  `/openapi/v1/org` 以及该 README 中列出的其他前缀下的公开接口面。
  Backend 的 `dump_openapi.py` 把文档收窄到公开路径及其引用的组件。
- **网关域（domain）。** `apps/gateway/configs/application.yaml` 声明
  `upstreams.domains`。没有 `match:` 的域占有 `/openapi/v1/<name>/**`；带
  `match:` 模式的域占有该模式，且更具体的模式胜出（字面段多的优先于字面段
  少的）。如今的划出（carve-out）就是这样实现的：`collaboration-publish`
  （`/openapi/v1/collaboration/bots/{bot_uuid}/public/**`）把由 BCS 提供的
  `collaboration` 命名空间中的一棵子树交给 Backend。域模式由字面段和
  `{param}` 段组成，以 `**` 结尾。谁可以调用某个路径，由同一文件中的
  `route_security` 单独设定。
- **命名空间测试。**
  `apps/backend/tests/community/contracts/gateway/test_public_namespace.py`
  在 Backend 提供了已声明前缀之外的 `/openapi/v1` 路由时失败；扩展这些前缀
  必须与对应的网关域和 `route_security` 条目一起落地。

### 11.2 每个服务一份文档

*（提议。）* 每个提供端点的服务发布自己的文档；不存在合并后的文档。这沿用了
上面的现有做法，并解决了待定决策 A-6（[§15](#15-待定决策)）。

| 文档 | 发布方 | 包含 |
| --- | --- | --- |
| `bots.openapi.json`（现有，扩展） | Backend，使用其现有的 `dump_openapi.py` | 基因组端点（§10.1）和晋升端点：候选报告、评审队列、批准/拒绝、晋升（§10.7） |
| `evolution.openapi.json`（新增） | `apps/evolution`，使用它自己的 `scripts/dump_openapi.py` | 经验、进化策略注册表、实验记录、进化运行、验证、操作（§10.2 到 §10.6、[§10.8](#108-操作--本文档)），以及 [10-meta-evolution.zh-CN.md](10-meta-evolution.zh-CN.md) 中第 3 层元进化端点（在构建它们时） |

`evolution.openapi.json` 与现有文档以相同方式门禁和发布：通过
`gate_and_publish_openapi.py`，并在 `dump_and_publish.sh` 中增加一项。内部
作业协议（`/evolution/v1/...`）不在任何一份文档中。

### 11.3 网关路由

*（提议。）* 路径均为完整的网关路径。

| 路径模式 | 提供方 | 网关条目 |
| --- | --- | --- |
| `/openapi/v1/bots/{bot_id}/genome/**` | Backend | 现有的 `bots` 域（`/openapi/v1/bots/**`）；无新条目 |
| `/openapi/v1/bots/{bot_id}/evolution/**` | `apps/evolution` | 带此 `match:` 的新域，server 为 `evolution` |
| `/openapi/v1/bots/{bot_id}/evolution/candidates/**` | Backend | 新的划出域，server 为 `backend`；优先于上一行 |
| `/openapi/v1/bots/{bot_id}/evolution/review-queue/**` | Backend | 新的划出域，server 为 `backend`；`**` 也匹配不带后缀的 `…/review-queue` |
| `/openapi/v1/bots/{bot_id}/experience/**` | `apps/evolution` | 带此 `match:` 的新域，server 为 `evolution` |
| `/openapi/v1/evolution/**` | `apps/evolution` | 新域 `evolution`（无需 `match:`）以及一个新的 `route_security` 条目 |

对照索引核对：候选报告和 `:approve`/`:reject`
（`…/evolution/candidates/{candidate}` 和
`…/evolution/candidates/{candidate}:approve`）落在 `candidates` 划出域中，
到达 Backend；晋升则位于 `genome` 下。一次运行的候选列表
（`…/evolution/runs/{run}/candidates`）仍在 `runs` 下，到达负责运行的
`apps/evolution`。绑定、运行、评估、实验记录和操作到达 `apps/evolution`；
进化策略、能力、套件和元进化路径位于 `/openapi/v1/evolution/**` 下。

bot 范围的路径位于 `/openapi/v1/bots/**` 下，沿用其现有的 `route_security`
规则。只有 `/openapi/v1/evolution` 是新的公开前缀：它的网关域、
`route_security` 条目和 `servers.evolution` 基础 URL 在同一次变更中落地。
Backend 在该前缀下不提供任何内容，因此 Backend 的命名空间测试不变；
`apps/evolution` 获得同样的命名空间测试，使用它自己声明的前缀（其 bot
范围路径的 `/openapi/v1/bots`，以及 `/openapi/v1/evolution`）。

```yaml
# Illustrative excerpt of upstreams.domains in apps/gateway/configs/application.yaml (proposed)
evolution:
  server: evolution
  schema:
    source: file
    path: schemas/evolution.openapi.json
    refresh_seconds: 300
evolution-bots:
  match: /openapi/v1/bots/{bot_id}/evolution/**
  server: evolution
  protocols: [http]
evolution-promotion:
  match: /openapi/v1/bots/{bot_id}/evolution/candidates/**
  server: backend
  protocols: [http]
```

### 11.4 兼容性门禁

发布门禁以机械方式执行 [§4.4](#44-v1-内的兼容性)：如果某次发布的文档删除或
重命名了路径或字段，或收紧了请求，`gate_and_publish_openapi.py` 就会失败。
`--allow-breaking` 只用于迁移到新端点版本的协调变更，并在 PR 中记录原因。

### 11.5 契约测试

- 每个服务测试它提供的路由与它发布的文档一致，因此文档从不描述服务并未
  提供的接口面。
- 各项约定（envelope、`Idempotency-Key` 重放、键重用时的 `409001`、
  `If-Match` 与 `412000`/`428000`、[§7.1](#71-形态与编码空间) 中的错误码）由
  共享的一致性测试检查，这些测试针对两个服务运行（工作项 RSI-07）。

## 12. 服务接口

约定接口是每个提供端点的模块（Backend、`apps/evolution`）在其 HTTP 适配层中
使用的接口，从而使每个端点的行为一致。

```python
from collections.abc import Awaitable, Callable
from typing import Protocol

class IdempotencyStore(Protocol):
    """Implements §5. Writes happen in the caller's transaction."""

    async def lookup(self, bot: BotRef, key: str) -> IdempotencyRecord | None:
        """The record for (owner_id, bot_id, key), or None if the key is new or expired."""

    async def record(self, record: IdempotencyRecord) -> None:
        """Stores (owner_id, bot_id, key) -> result id in the same transaction that creates the resource.
        Raises KeyInProgress if another request holds the key and has not committed."""

class IdempotentHandler(Protocol):
    async def handle(self, *, bot: BotRef, key: str | None, fingerprint: str,
                     create: Callable[[], Awaitable[tuple[str, int]]],
                     current: Callable[[str], Awaitable[dict]]) -> tuple[int, dict, bool]:
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

## 13. 示例

每个示例都是一串原始 HTTP 请求。响应体缩减为示例用到的字段；完整形态见各
负责文档。

### 13.1 夜间流水线

一个定时作业在 `user_owner_5` 拥有的 `bot_123` 上启动今晚的运行、等待其完成，
并批准所有者的策略允许流水线批准的候选。由于流水线不是所有者，每个请求都带有
`entity_id=user_owner_5`。键是确定性的，因此即使作业本身崩溃并重新运行，第 1
步也会重新关联到同一次运行，而不是启动第二次运行。

1. 启动运行。

   ```http
   POST /openapi/v1/bots/bot_123/evolution/runs?entity_id=user_owner_5
   Content-Type: application/json
   Idempotency-Key: nightly-bot_123-2026-10-08
   ```

   ```jsonc
   // Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
   {"binding": "bind_01"}
   ```

   ```jsonc
   // Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
   // 202; Location: /openapi/v1/bots/bot_123/evolution/runs/run_7f3?entity_id=user_owner_5
   {"code": 202000, "message": "Accepted", "data": {"run_id": "run_7f3", "status": "queued"},
    "request_id": "4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e"}
   ```

2. 按 id 轮询状态，每隔几分钟一次，或按 `Retry-After` 的提示，直到状态为
   终态。如果作业自身的时间上限先到，它就停止；运行继续进行，作业重新运行时
   用相同的键重复第 1 步并找到它。

   ```http
   GET /openapi/v1/bots/bot_123/evolution/runs/run_7f3?entity_id=user_owner_5
   ```

   ```jsonc
   // Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
   // 200; failed, cancelled, or budget_exhausted would end the job here with nothing to approve
   {"code": 200000, "message": "OK", "data": {"id": "run_7f3", "status": "completed", "candidate_count": 1},
    "request_id": "a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5"}
   ```

3. 列出该运行的候选，然后读取每份报告。

   ```http
   GET /openapi/v1/bots/bot_123/evolution/runs/run_7f3/candidates?entity_id=user_owner_5
   GET /openapi/v1/bots/bot_123/evolution/candidates/sha256%3Ac41e…?entity_id=user_owner_5
   ```

   ```jsonc
   // Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
   // data of the report: the gate wants a human for T2, above the auto-promote ceiling T1
   {"candidate_id": "sha256:c41e…", "verdict": "accept",
    "gate": {"id": "gd_204", "outcome": "needs_review", "risk": {"tier": "T2", "raised_by": []},
             "auto_promote_ceiling": "T1"}}
   ```

4. 对门禁结果为 `needs_review` 的候选，在所有者给流水线的策略范围内尝试批准。
   批准键由运行键和候选 id 派生，因此重新运行时会重放它。

   ```http
   POST /openapi/v1/bots/bot_123/evolution/candidates/sha256%3Ac41e…:approve?entity_id=user_owner_5
   Content-Type: application/json
   Idempotency-Key: nightly-bot_123-2026-10-08/approve/sha256:c41e
   ```

   ```jsonc
   // Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
   {"reason": "nightly auto-policy"}
   ```

   ```jsonc
   // Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
   // 403, policy family: the owner lets this pipeline approve up to T1 only.
   // 403000 stands in for the policy_denied subcode, which 08-promotion.md sets.
   {"code": 403000, "message": "Policy denied", "data": null,
    "request_id": "d3e4f5a6b7c8d9e0f1a2b3c4d5e6f708"}
   ```

   流水线不重试策略系列的错误。它记录候选 id 和 `request_id`，把该候选留在
   评审队列中由人处理（§13.3）。

### 13.2 安全地编辑绑定

所有者的 UI 后端提高 `bind_01` 的预算，同时另一个写入者也在修改绑定（§6）。

1. 读取绑定并保存 `ETag`。

   ```http
   GET /openapi/v1/bots/bot_123/evolution/bindings
   ```

   响应为 `200`，带响应头 `ETag: "bind-v12"`。

2. 带 `If-Match` 发送修改后的文档。

   ```http
   PUT /openapi/v1/bots/bot_123/evolution/bindings
   Content-Type: application/json
   If-Match: "bind-v12"
   ```

   ```jsonc
   // Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
   // 412: someone else wrote first; nothing was changed
   {"code": 412000, "message": "Precondition failed: the resource changed since it was read",
    "data": null, "request_id": "e4f5a6b7c8d9e0f1a2b3c4d5e6f70819"}
   ```

3. 重新读取（`ETag: "bind-v13"`），把预算修改重新应用到新副本上，并带
   `If-Match: "bind-v13"` 发送。应答为 `200`，带 `ETag: "bind-v14"`。连续出现
   几次 `412` 后，UI 停止重试，并向用户展示冲突。

### 13.3 UI 后端中的评审队列

1. 列出未处理的评审条目，每次一页（§8）。

   ```http
   GET /openapi/v1/bots/bot_123/evolution/review-queue?status=open&page=1&page_size=20
   ```

   ```jsonc
   // Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
   // data
   {"total": 1, "items": [{"candidate_id": "sha256:c41e…", "revision_seq": 42, "risk_tier": "T2",
                           "run_id": "run_7f3", "status": "open"}]}
   ```

2. 当评审者选中某一条时，打开其报告（diff、验证、门禁决定）。

   ```http
   GET /openapi/v1/bots/bot_123/evolution/candidates/sha256%3Ac41e…
   ```

3. 批准。UI 后端在评审者打开批准对话框时创建键，并在每次重试中重用它，因此
   双击或网络重试只会记录一项决定。

   ```http
   POST /openapi/v1/bots/bot_123/evolution/candidates/sha256%3Ac41e…:approve
   Content-Type: application/json
   Idempotency-Key: 4b0f6c1e-9a7d-4f53-8d1e-2c6a9e1b7f02
   ```

   ```jsonc
   // Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
   {"reason": "Reviewed diff and verification report; escalation fix is correct."}
   ```

   应答是 §10.7 中展示的 `200`：评审条目变为 `approved`，并产生一次状态为
   `applying` 的 `r42` 晋升。`409020` 表示另一位评审者先做了决定；UI 重新读取
   报告并展示该决定。

### 13.4 回到旧版本

回到旧版本就是晋升一个更早的修订版；没有单独的回滚调用。

1. 读取引用，找到要回到的修订版以及 `active` 当前指向的修订版。

   ```http
   GET /openapi/v1/bots/bot_123/genome/refs
   ```

   ```jsonc
   // Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
   // data, shortened: active is r42, previous is r41
   {"refs": [{"name": "active", "revision": "sha256:7c1e…"}, {"name": "previous", "revision": "sha256:a90b…"}]}
   ```

2. 晋升 `r41`，并对 `active` 进行比较并交换。

   ```http
   POST /openapi/v1/bots/bot_123/genome/promotions
   Content-Type: application/json
   Idempotency-Key: goback-bot_123-r42-to-r41
   ```

   ```jsonc
   // Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
   {"revision": "sha256:a90b…", "ref": "active", "expected_revision": "sha256:7c1e…",
    "reason": "r42 raised refund escalations"}
   ```

   ```jsonc
   // Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
   // 201, data shortened
   {"id": "prm_6a0", "ref": "active", "revision_id": "sha256:a90b…", "revision_seq": 41,
    "from_revision_id": "sha256:7c1e…", "going_back": true, "status": "applying"}
   ```

   `409010` 表示 `active` 在第 1 步之后已移动：重新读取引用再做决定。对于服务型
   bot，该修订版会通过现有的发布流程作为下一个版本发布
   （[08-promotion.zh-CN.md](08-promotion.zh-CN.md)）。

## 14. 交互

| 其他部分 | 方向 | 流转内容 |
| --- | --- | --- |
| [01-genome.zh-CN.md](01-genome.zh-CN.md)（基因组注册表，Backend） | API → 基因组 | 修订版、引用、diff 和内容调用；引用上的 CAS；按内容幂等的修订版创建 |
| [02-experience.zh-CN.md](02-experience.zh-CN.md) | API → 经验 | 片段查询、反馈记录 |
| [03-strategy.zh-CN.md](03-strategy.zh-CN.md)（进化策略注册表） | API → 注册表 | 进化策略注册（`POST /evolution/strategies`）、列表、能力目录；面向进化策略作者的进化策略 SDK 由该文档负责 |
| [05-experiment-ledger.zh-CN.md](05-experiment-ledger.zh-CN.md) | API → 实验记录 | 条目查询、以操作形式进行的导出 |
| [06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md) | API → 运行 | 绑定（ETag）、运行提交（幂等键 → 运行 id）、按 id 查询状态、取消；预算、紧急停止开关和绑定检查产生策略和预算错误系列；内部作业协议使用相同的约定 |
| [07-verification.zh-CN.md](07-verification.zh-CN.md) | API → 验证 | 套件、以操作形式进行的临时评估 |
| [08-promotion.zh-CN.md](08-promotion.zh-CN.md) | API → 晋升 | 候选报告、评审队列、批准/拒绝、晋升（包括回到旧版本） |
| 流水线与 CI | 调用方 → API | HTTP；确定性的幂等键；错误码 |
| UI 后端 | 调用方 → API | HTTP；编辑绑定时使用 ETag；每个用户操作一个键 |
| 人类操作员 | 调用方 → API | 工作台 UI，经由 UI 后端 |
| 网关（`apps/gateway`） | 把 API 路由到 Backend、`apps/evolution` | 按路径的域路由、已发布的 OpenAPI 文档、兼容性门禁（[§11](#11-openapi-文档与网关路由)） |
| Backend OpenAPI v1 适配器 | 被本层复用 | `Envelope`、`ErrorEnvelope`、`Page`、`PageParams`、envelope 构造器与错误映射 |

## 15. 待定决策

| ID | 问题 | 当前立场 |
| --- | --- | --- |
| A-2 | 幂等记录保留多久？ | 提议：至少 30 天，长于任何调用方的重试周期（第二天重试的夜间作业仍必须能重新关联）。随 RSI-07 一并决定 |
| A-3 | 是否为大型仅追加集合（片段、实验记录条目）采用游标分页？ | 提议：为保持 v1 一致性保留页码；如果 `total` 或页偏移成为问题，再为这两个集合增加不透明的 `cursor` |
| A-4 | 结构化的错误详情（例如在绑定检查中哪项能力没有提供者）？ | OpenAPI v1 规定错误时 `data` 为 `null`，并使用固定消息。可选方案：保持现状，将详情编码到子码中；或仅对进化错误允许 `data.details` 对象。尚未决定 |
| A-5 | 是否为写入提供统一的仅校验模式（`dry_run`）？ | `POST /bots/{bot_id}/genome/revisions` 的试运行（对照基础修订版检查补丁而不记录修订版，[01-genome.zh-CN.md](01-genome.zh-CN.md)）需要它；其他端点是否提供由其负责文档决定 |
| A-6 | 一份合并的 OpenAPI 文档，还是每个服务一份？ | 已解决：每个提供端点的服务一份文档，没有合并文档（[§11.2](#112-每个服务一份文档)） |
| A-8 | 事件投递（[work-items.zh-CN.md](work-items.zh-CN.md) 中的 Q3；那里的 Q1，即 CLI 二进制，在 CLI 不在范围内后已无意义） | 当前设计没有回调或 webhook 通道：按 id 查询状态是唯一机制。推送通道将是一项补充，而不是替代 |

[work-items.zh-CN.md](work-items.zh-CN.md) 中的 Q2（受管 bot 是否默认可以请求运行）
属于已推迟的 bot 调用方（[§3](#3-调用方)）。
