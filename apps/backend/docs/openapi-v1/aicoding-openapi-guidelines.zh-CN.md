# AICoding 专属 OpenAPI v1 设计与开发规范

本文定义 OCB 对外发布 AICoding 专属产品接口时的路由、身份、分层、契约和测试规范。
它不是 AICoding Engine/Relay 内部接口的说明；Relay 内部协议只作为 OCB 服务内部的上游实现细节。

## 1. 适用范围与边界

### 1.1 两个必须区分的面

| 面 | 位置 | 性质 | 是否进 OpenAPI v1 |
|---|---|---|---|
| AICoding 产品面 | OCB 定义并持有契约 | 面向用户/前端/租户的产品 API | 可以 |
| AICoding Engine 面 | Relay `/api/aicoding/...`、`/data/...` | 单 Bot 容器的运行时协议 | 默认不透传 |

已有 `engine-surface.md` 的 **C4** 排除的是第二条：不要把 Relay 的
`/api/aicoding/sessions`、`/data` 等内部面按路径原样包装成租户合同。
C4 不阻止 OCB 拥有自己的 AICoding 产品合同。若某个产品接口需要访问多个 Bot，
OCB 可以在服务内部调用 Relay，但对外响应必须是 OCB 自己定义、命名和演进的产品契约。

### 1.2 当前已发布的专属资源

- `GET /openapi/v1/bots/aicoding/work-items`
  - 已登录 human 的跨 Bot 近期 DevFlow 任务快照。
  - OCB 先枚举该用户可触达的 owner/collaborator Coding Bot，再按 Bot 并发查询 Relay。
  - OCB 归一化、过滤、去重、排序，并把每个 Bot 的探测结果透出为 `data.bots`。
  - 用户点击某条任务后，详情仍按既有 DDF 详情链路实时查询；本接口不内联详情。

## 2. 路由规范

### 2.1 路由模型

| 场景 | 推荐路径 | 权限锚点 | 说明 |
|---|---|---|---|
| 签名用户自己的跨 Bot 聚合 | `/openapi/v1/bots/aicoding/<resource>` | signed principal | `aicoding` 占用 `/bots` 下的保留字，必须先于 `{bot_id}` 挂载 |
| 单个 Bot 上的 AICoding 产品能力 | `/openapi/v1/bots/{bot_id}/aicoding/<resource>/...` | `{bot_id}` + bots guard | 先解析 Bot，再进入 AICoding 子域 |
| 租户级 AICoding 目录 | 不默认创建 | 需单独裁定 | 必须说明为何没有用户或 Bot 轴，并补 admission/authorization |

规则：

1. AICoding 专属资源统一用 `aicoding` 作为路径段，不再新增 `agentcoding`、`ai-coding` 等别名。
2. 资源名使用 kebab-case，例如 `work-items`；路径必须是名词。
3. 不要新增 `/openapi/v1/bots/aicoding/sessions` 这类通用转发名来模仿 Engine 路径。
   如果未来需要单 Bot 会话级产品合同，先定义真实的业务资源，再放在
   `/openapi/v1/bots/{bot_id}/aicoding/...` 下。
4. 保留字必须同步更新：
   - `openapi_v1/__init__.py` 的挂载列表；
   - `admission_routes.py`；
   - `authorization_rules.py`；
   - `test_path_convention.py` 的 `_BOT_FREE`;
   - `docs/openapi-v1/README.md` 与 `README.zh-CN.md` 的 reserved names。
5. 当前 internal 镜像路由 `/api/v1/aicoding/work-items` 只是开发和吻合现有内部 `/api/v1` 调用习惯的过渡面。
   对外权威合同是 OpenAPI；两个 adapter 必须通过同一个 Service Protocol，不允许行为漂移。

## 3. 身份与准入规范

AICoding work-items 是“签名 human 自己的跨 Bot 快照”，因此：

1. 身份来源只能是 OpenAPI principal 中的 user，使用 `caller_owner_id(principal)`。
2. 不接受调用方可选的 `user_id`。任何能扩大读取范围的 query id 都是冒用边界。
3. `refuse_app_only_caller` 必须启用。应用若未来要代理用户，必须先设计 LIVE grant 级别
   的 Bot 集合收缩，并重新裁定 admission；不能复用当前人类自服务语义。
4. 用户可触达的 Bot 列表由 OCB own/collaborator 关系决定，不要求当前护航精灵挂在某个 Bot 上。
5. Public handler 不做越权判断；它只把 principal 交给 Service。Bot 枚举和设备解析的失败语义由 Service 汇总。

若后续接口是单 Bot 面，仍要复用现有 `/bots/{bot_id}` guard 和 addressed-owner
授权规则；`aicoding` 只是该 Bot 下的产品子域，不另设第二套 Bot 鉴权。

## 4. 代码分层与目录规范

AICoding 专属 OpenAPI 不做独立分层，而是完全落在 OCB 现有架构内：

| 关注点 | 位置 | 规则 |
|---|---|---|
| Public HTTP adapter | `adapters/http/openapi_v1/aicoding/<resource>.py` | 只做鉴权依赖、query 解析、Envelope、错误映射；不访问 repository、不感知 Relay 版本 |
| Internal HTTP mirror | `adapters/http/aicoding/<resource>_router.py` | Only for explicit internal contract; 与 public 共享 Service Protocol |
| Domain contract models | `core/aicoding/<resource>_models.py` | Pydantic 模型和共享 token 归一化 |
| Service Protocol | `core/aicoding/<resource>_service_protocol.py` | `@runtime_checkable Protocol`，含精确签名 |
| API re-export | `api/<resource>_service.py` | 只 re-export Protocol；不定义具体 service |
| Concrete service | `core/aicoding/services/<resource>_service.py` | 持有产品语义、Bot 枚举、fan-out、过滤、聚合、partial 语义 |
| Repository access | `core/repository/...` | 只经 repository Protocol，不泄进 HTTP |
| DI | `di/modules/aicoding_module.py` | 绑定 repository/driven service 与 Protocol，不在 adapter 中构造 service |

新增接口的默认文件形态：

```text
adapters/http/openapi_v1/aicoding/work_items.py
adapters/http/aicoding/work_items_router.py
core/aicoding/work_items_models.py
core/aicoding/work_items_service_protocol.py
core/aicoding/services/work_items_service.py
api/work_items_service.py
```

实现要求：

1. `openapi_v1/aicoding/` 是 AICoding 域包；不要为每个资源再建
   `aicoding_<resource>/` 顶层目录。
2. HTTP adapter 不得 import concrete service。`Injected(XServiceProtocol)`
   与 `api/<x>_service.py` re-export 是硬约束。
3. Service Protocol 的新增或签名变化必须加入 `test_service_api_conformance.py`。
4. Service 返回 typed contract model；public adapter 直接用它生成 OpenAPI schema，
   internal mirror 可转换为既有 `{success, data}` 形状。
5. token/query 词表只在 core contract 中解析一次；public/internal adapter 只负责
   把解析异常转换成各自传输契约的错误形状。
6. 不在 OpenAPI router 中写 AICoding 路由分支判断。路由注册、admission 和
   authorization 都是声明式表；真正的版本、来源或状态差异放在 Service。
7. 目录移动或合同扩展必须同步 `core/aicoding/README.md` 的 Context Boundary。
8. 不因 AICoding 专属接口引入另一套 Auth、Envelope、分页或 Pydantic 序列化风格。

## 5. work-items 查询契约

### 5.1 可选参数

```text
GET /openapi/v1/bots/aicoding/work-items
  ?source=mine,others,collab
  &since_ms=1794000000000
  &run_status=non_idle,running,awaitingHuman
  &limit=50
```

| 参数 | 省略时含义 | 说明 |
|---|---|---|
| `source` | 不过滤来源 | 允许 `mine`, `others`, `collab`；逗号组合为 OR |
| `since_ms` | 不限制时间 | inclusive lower bound；比较 session `updated_at_ms` |
| `run_status` | 只要求 work item 存在非 idle 状态 | 允许 `non_idle` 和已知 DevFlow 状态组合 |
| `limit` | 使用默认 50 | 每个 Bot 的 Relay 扫描窗口，1–200；不是最终聚合条数 |

`run_status` 细则：

- `non_idle`：状态非空且不是 `idle`。它表示一个别名集合，不是新的引擎状态。
- 其他 token 是精确匹配，如 `running`、`awaitingHuman`、`completed`、`failed` 等。
- 多个 token 是 OR。
- `run_status=non_idle` 与抽象状态一起出现时，以更宽的 `non_idle` 为准。
  例如 `non_idle,failed` 表示所有非 idle 状态，而不是“只有 failed”。
- Response 的 `run_status` 保持开放字符串。Relay 新增状态可以被返回/展示时，
  未同步 OCB 查询词表前不能作为 query token 精确查询。
- `idle` 和空状态永远不会进入 `data.items`；这不是 query 筛选，是 work-items 产品定义。

### 5.2 分页语义

本接口不是全局分页接口：

1. `limit` 只传给每个 Bot 的 Relay session list 查询，作为每个 Bot 的扫描上限。
2. OCB 对每个 Bot 的返回执行 fallback 过滤后合并。
3. 最终 `data.items` 数量不受 `limit` 约束，可能小于也可能大于 `limit`。
4. 不提供 `offset` / global page。产品口径是“近期任务面板快照”，不是完整会话归档列表。
5. 如果未来需要完整全局分页，必须新增 cursor/total 契约，不得复用当前 `limit`
   或悄悄改变既有语义。

### 5.3 来源分类

`source` 分类基于 session key 与当前用户：

- `mine`：用户自己发起；
- `others`：其他用户发起；
- `collab`：member-task 协作形态；
- 未传 `source` 时三种来源都可出现。

新 Relay 会尽量在源头过滤；旧 Relay 可能忽略过滤参数并返回最近一段列表。OCB 无论收到
新/旧结果，都执行同一套分类、过滤、去重和排序，因此不需要先探测 Relay 版本。

## 6. Relay 兼容与聚合语义

Service 对每个目标 Bot 调用现有：

```text
GET /api/aicoding/sessions?user_id=<caller>&session_kind=user&limit=<scan_limit>&offset=0
```

并尽可能透传 `source`、`since_ms`、`run_status`。

兼容规则：

1. 所有 Bot 使用同一份参数；不做新旧 Bot 双轨参数转换。
2. 新 Relay：在源头先过滤 source/time/status，再返回满足条件的前 `limit` 条。
3. 旧 Relay：忽略不认识的可选参数，返回最近前 `limit` 条；OCB 再做同样的过滤。
4. OCB 永远保留 fallback 过滤，即使新 Relay 已经保证结果。这样升级期和异常结果都稳定。
5. OCB 按 `(bot_id, owner_id, session_key)` 去重，保留 `updated_at_ms` 最新一条。
6. 聚合排序使用 `updated_at_ms` desc，再按 Bot/owner/session key 稳定排序。
7. 不新增 `until_ms`。
8. 会话详情不在本列表返回。用户点开任务后走既有 DDF 详情查询，避免列表接口被大量详情查询放大。

Partial 语义：

- 每个 Bot 总是有 `WorkItemBotSummary`。
- `status=ok`：Relay 返回可用结果；
- `status=unavailable`：没有活动绑定，不算接口 partial；
- `status=error`：绑定解析失败、HTTP 失败、超时或响应形状非法。
- 任何一个 Bot `error` 时 `partial=true`，但整个 HTTP 响应仍为 200+Envelope，
  其他 Bot 的数据照常返回。
- 用户的 typo/token 错误是请求级 400，不是 partial。

## 7. 测试规范

每个 AICoding OpenAPI 变更至少覆盖：

1. Public adapter：
   - signed principal;
   - app-only refusal;
   - optional-query defaults;
   - invalid token/numeric query error;
   - `Envelope` 形状。
2. Internal mirror（若存在）：
   - auth dependency;
   - 与 public 相同的过滤归一化；
   - internal 400 contract；
   - partial Bot 不导致整体失败。
3. 静态合同：
   - route path convention;
   - admission table;
   - authorization table;
   - explicit user-id exemption/absence;
   - generated OpenAPI schema。
4. Core service：
   - Bot 枚举和 dedupe;
   - source 分类;
   - time/status fallback;
   - old/new Relay 均可被同一结果覆盖;
   - per-Bot error summary;
   - terminal/open-set status passthrough。
5. Service Protocol:
   - `issubclass` conformance;
   - exact signature;
   - no abstract method leftovers。
6. Repository/Dependency Context Boundary:
   - 更新 `core/aicoding/README.md`;
   - 保持 architecture gates 通过。

无需为文档-only 变更启动 Relay；涉及 Relay 参数时必须同步 Relay tests，
并确认旧协议忽略新参数的兼容性。

## 8. 演进规则

1. 新参数必须可选，不得破坏旧调用。
2. 已有 response 字段只能 additive；不得重命名字段或改变 nullable 语义。
3. 状态集合是开放的，但 query token 是封闭词表；Relay 新增状态时应在同一 PR 中
   同步 OCB 词表、文档和测试。
4. 新 AICoding 资源进 `openapi_v1/aicoding/` 域包，不建顶层 resource 包。
5. 新单 Bot 资源必须复用 `bots/{bot_id}` guard；新用户级资源必须先明确是否
   只允许 signed human。
6. 不把 Engine response 原样透传为 public contract。所有 Relay 字段必须经过
   OCB projection/model 命名。
7. 不做 speculative proxy byte-for-byte compat；兼容目标是产品契约的向后兼容，
   不是复制内部实现的任意形状。

## 9. 当前 work-items 合规结论

| 规范项 | 结论 |
|---|---|
| 用户级跨 Bot 路由 | 通过：`/openapi/v1/bots/aicoding/work-items` |
| signed human、无 caller `user_id`、拒绝 app-only | 通过 |
| OCB-owned 产品契约，非 Engine passthrough | 通过 |
| Service Protocol / api re-export / DI | 通过 |
| partial Bot 聚合 | 通过 |
| old/new Relay fallback | 通过 |
| 可选参数与 no-filter 默认语义 | 通过 |
| OpenAPI 目录演进性 | 已优化：收敛为 `openapi_v1/aicoding/` 域包 |
| public/internal token 解析重复 | 已优化：收敛到 `work_items_models.parse_optional_token_filter` |
