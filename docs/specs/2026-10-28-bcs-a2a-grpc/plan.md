# BCS A2A HTTP 接入与 Harness 联调一期

状态：一期功能与真实模型 E2E 验证完成。全仓架构检查仍有既有失败和环境跳过项，不能视为全部 CI 通过；详情见 `acceptance-checklist.md`、`validation-results.json` 及 `implementation-status.md`。

传输范围已调整：一期先基于 SOFABoot HTTP 跑通链路。该阶段已完成，下面原有一期范围作为 HTTP 验收基线保留。2026-10-30 启动标准 A2A gRPC + MOSN + SOFARPC Triple 阶段，新增范围见本节；不能将 HTTP 阶段的通过记录作为 gRPC 验收结果。

## 2026-10-31 Harness 存储范围调整

本节覆盖下文旧版持久化实现与验收描述。Harness SDK 不随本次 Avernet PR 提交。
Harness adapter 移除 H2、自动建表和内置持久化提交去重；默认使用官方 Java SDK
InMemoryTaskStore / InMemoryQueueManager，宿主可注入 TaskStore、TaskStateProvider
和队列工厂。Provider 自有会话映射及运行状态默认也驻留内存，并保留替换接口。
审批接受记录先写入选定 TaskStore 再推进引擎，默认只在当前进程有效。
不承诺 Task、adapter 会话关联、审批或初始提交去重跨 Harness 重启恢复。
多实例执行归属、跨实例取消、事件传播和故障接管由后续宿主集成；仅接入 Redis
TaskStore 不等于具备完整分布式执行能力。Harness 原生 AgentSessionStore 独立配置。
BCS 仍兼容显式声明并协商提交去重扩展的第三方 Server，但默认 Harness Card 不声明
该扩展；首个 Task 返回前结果未知时保持不重发，取得 Task 后仍有界查询/重新订阅。

发布前同步最新 dev：保留上游 MySQL 037/038 与 SQLite 038，同一期未提交的 A2A 草稿迁移合并为
MySQL/SQLite 039（最小 Card 与规范化 Task/Run/context 关联结构），不先创建再删除实验表/字段。
已有旧草稿数据库仅作为可丢弃开发数据，需重建；历史证据中的旧编号不适用于当前迁移链。

## 后续实施：标准 A2A gRPC 经 MOSN（2026-10-30）

用户确认的目标链路：BCS tonic → 调用端 MOSN（订阅、寻址、负载均衡）→ 服务端 MOSN → Harness SOFARPC Triple。保留原生 protobuf 方法与服务端流，不经过 Layotto unary InvokeService 的 Hessian 转换。直连只用于定位互通问题；经过真实 MOSN 的端到端验证是完成条件。

- 复用固定版本的 A2A Rust `a2a-pb 0.3.2` 生成客户端与 Java SDK `1.4.0.Final` gRPC Handler。Java gRPC 与 REST 共用同一个 RequestHandler、Task 存储、事件队列和 Harness 引擎，不另建一套任务生命周期。
- 协议保持 A2A/1.0，新增 gRPC binding；stream 更新方式与具体传输分开。Card 继续经 HTTP 发布，新增真实可达的 gRPC 接口及 Bot tenant。选定 binding 后冻结路由，不静默降级为 HTTP 或直连后端。
- 同一个标准 A2A gRPC service 使用请求 tenant 选择 Bot 隔离的 runtime；先校验对应 Bot 凭据与版本，再委托官方 Handler。MOSN 服务路由头来自可信部署配置，不能从用户消息 metadata 推导。
- 初始 SendStreamingMessage、异步 SendMessage(returnImmediately=true)、GetTask、SubscribeToTask、CancelTask 都要贯通。订阅断开不等同于取消 Task；工具与 Structured Input exec 审批继续使用已协商的原有模型。ask_user 仍不在本次范围。
- 复用现有文本投影、工具事件、Task/context 关联、执行/人工等待预算、终态与取消规则。gRPC 错误需保留标准 typed ErrorInfo，屏蔽远端原始诊断；明确区分提交拒绝与结果未知，不能把网络中断推断为 Task 失败。
- MOSN 部署配置与凭据保持在本地私有运行目录，不将公司服务地址、应用身份或机器状态写入公开默认配置。需验证实际发布/订阅、路由头及 listener，不能仅凭端口连通或 mock 宣称寻址已验证。
- 验收依次覆盖标准 gRPC 互通、真实 MOSN 发布/订阅与请求路径、流式文本及工具、异步提交后订阅、Task 查询、精确取消、exec 审批、错误/鉴权/Bot 隔离及原 HTTP 回归。记录实际版本、服务路由与过程证据；每项分别标注通过或未完成。

当前已完成两端装配与真实 MOSN 链路验证：最新构建的流式、异步、文本、多轮、工具、精确取消及 exec 批准/拒绝均通过。配置、寻址与重启限制见 `grpc-mosn-runbook.md`；独立验收结果见 `grpc-validation-results.json`。仍不承诺在途任务跨进程重启无损恢复。

## 目标与范围

让 BCS 注册、发现并调用通过 A2A 暴露服务的 Agent，完成单 Agent 的异步任务、流式文本、多轮对话、任务查询和取消。

- BCS 担任 A2A Client，Harness A2A SDK adapter 担任 A2A Server。
- 固定 A2A 1.0 协议，采用 HTTP+JSON/REST 绑定和 SSE 流式订阅；数据模型遵循官方对应版本定义。一期不增加 JSON-RPC 或 gRPC 绑定。
- BCS 保留现有 WS、Provider HTTP 接入；新增 A2A 下行方式。
- Harness 实现在现有 `harness-bcs-demo` 中新增模块。
- 工具过程采用显式声明、协商和版本化的 BCS 工具扩展，承载现有 BCS 工具事件的必要字段；Gemini CLI RFC 仅作设计参考，不作为兼容依赖。
- HITL 一期优先保留 demo 已有 exec 工具审批，使用实验性 Structured Input Extension 承载审批输入并映射现有 exec 卡片。按用户最新确认，ask_user 的验证与实现均推迟到二期，不作为一期交付前置。
- 一期采用 BCS 单实例部署，默认服务端队列开启并保证同一 Bot/Session 同时只有一个新消息 send；依赖已有准入规则，不另设计并发 send 或首轮 context 竞争机制。交互回复和取消必须能够推进当前任务。
- 可信终端用户上下文、签名身份扩展、多实例订阅 owner/租约、跨实例恢复及精确消费位置持久化暂缓；一期不承诺 BCS 进程重启后无损恢复在途事件。业务工具仅使用 demo 的受信任配置身份，不将该版本作为用户委托授权方案。
- 首期不包含新的多 Agent 协作协议、多模态、mode_switch 和 A2A Task push notification。保留既有反向 BCS 工具调用方式；不新增 BCS A2A Server 或 MCP Server。

## 注册、发现与接口

### AgentCard 注册

Provider 管理方使用原 `POST /providers/{provider_id}/bots`，提交可选
`agent_card_url` 即可原子注册 A2A Bot，不需要 webhook。仍要求 name、稳定
provider_bot_ref 和单个 owner；鉴权后拉取 Card，拉取后重新校验 Provider，
复用原子 Bot/归属/权限/Card 仓储事务。禁止与 Bot webhook 或 plugin 混用。
A2A 重复身份返回 409，不隐式更新或重发凭据；原不带 URL 或 Card 的请求行为保持。
详细接口契约见 `../a2a/provider-admin-registration-v1.md`。现有 `/register`
的 scoped Card-aware 入口继续保留，不能将两种凭据混用。

支持两个互斥来源：提交 `agent_card_url` 由 BCS 拉取，或直接提交完整 `agent_card` JSON。
两条路径共用 A2aCardPort 的官方 SDK 声明校验与接口出站策略，不提交真实任务探测服务。
校验通过不保证远端接受凭据或执行成功；原始 Card 完整保留，未知字段和技能描述不丢失。

- `/providers/{provider_id}/bots` 使用已有 Provider Admin Bearer，支持两种来源；`name`
  在 A2A 注册中可省略或为空，默认 Card.name；显式值作为 BCS 展示别名，不修改原 Card。
  `summary` 未提供时默认 Card.description，skills 不压缩映射到旧 Skill 字段。名称仍受 BCS
  2..64 字符展示限制，Card.name 超限可提供有效别名，不能静默截断。
- 来源都未提交时保持传统 Provider Bot 注册，仍要求名称及原有注册约束。来源同时提交、
  Card 非对象、非法 URL、A2A plugin 模式或混用 webhook_url 都拒绝。
- legacy/OpenAPI `/register` 的 Provider-scoped v2 token 同样支持 URL query 或 JSON body
  `{agent_card: {...}}`，可省略 bot-name；普通 v1 token 不能注册 A2A。登记 Human 仍须拥有
  Provider 管理权限。注册先完成授权/校验，再原子提交 Bot、Provider/ref、owner、profile、审计和 Card。
- BCS 归属、可见性、权限、Provider 身份及 provider_bot_ref 不从 Card 推导。
- 管理 `PUT /providers/{provider_id}/bots/{bot_uuid}/a2a` 接受互斥 URL/Card 与 expected_revision；
  GET 返回完整快照、可空 source URL 与 revision。`POST .../a2a/card:refresh` 只重读已保存 URL，
  inline Card 的显式更新走 PUT，不能猜测 Card 来源地址。
- 发现、详情、搜索和批量查询返回完整 agent_card，沿用现有权限。`GET /bots/{id}/agent-card`
  复用详情授权，非 A2A Bot 返回 404；匹配 Card name/description/skill id/name/description/tags，
  不回退旧能力字段，不从 BCS 字段反向拼装 Card。

### Card 同表存储与派生

不新增独立配置表。`bcs_bots` 的 A2A 专用列严格保留三个：
`agent_card`、可空 `agent_card_url`、`agent_card_revision`。移除选定接口、凭据引用、
校验时间、下次刷新时间和刷新错误五个冗余字段。

- 新请求从本地已保存 Card 的 supportedInterfaces 按顺序，结合当前 HTTP 出站策略和
  显式 MOSN/gRPC 目标清单选择接口；不联网重取 Card，不引入另一份接口缓存。
- 凭据由 Bot 的可信 Provider 绑定与固定 downlink_bcs_to_provider 类型推导，从既有凭据仓储取值。
  不在 Card/Bot JSON 保存密钥或重复的凭据引用。Provider/Bot 禁用、归属、凭据检查保持。
- 在途 run 使用提交时冻结的接口和部署路由，不因 Card 更新而换目标。
- revision 仅为 BCS 乐观锁版本。Card 内容、URL、revision 在同一短写入/注册事务内提交；
  网络操作不持有数据库事务。旧 URL 刷新不能覆盖新 inline Card。
- 通用 gmt_modified 是 Bot 行审计时间，可能由其他 Bot 操作更新，不表示最后 Card 校验时间，
  不作为周期刷新依据。普通 Bot 查询仍用明确列投影；批量可用性只读取接口数组投影，不加载 skills。
- 首次发布统一使用 MySQL/SQLite 039，直接添加三个 Card 列，不引入旧的五个管理字段。
  不转换早期草稿数据；历史已提交迁移不改，旧草稿开发库需重建。

### Bot 路由与绑定

- 每个 Harness Bot 对应独立 Card URL 和接口基地址，例如 `/a2a/bots/{botRef}/agent-card.json` 与 `/a2a/bots/{botRef}`。多个 Bot 共用 SOFABoot 进程和端口，Card 的接口地址指向相应 Bot 的路由。
- Harness 从经鉴权的路由绑定取得 botRef，通过 BotDefinitionResolver 选择 Definition；不从模型文本、任意 metadata 或 Card 名称推导目标。Task 查询、订阅、取消及交互回复均验证 Task 属于该路由 Bot。
- BCS 注册授权验证的是注册者有权绑定该远端 Agent，使用 Provider 管理权限、受信任的部署配置映射和目标凭据作用域。可访问的公开 Card、Card 中的 provider 或名字均不是归属证明；不要求公开 Card 天然独占属于某个 BCS Bot。
- Card 刷新不能改变 BCS botRef、身份或凭据绑定；Card 中接口变化仅在当前出站策略允许时影响后续 run。在途任务保留原接口及凭据类型关联。

### AgentCard 更新感知（一期简化）

- A2A 没有通用的 Card 更新主动通知；URL 来源采用手动刷新和固定周期重读，inline 来源显式更新。
- 拉取并验证后 Card 与来源 URL 均未变化时，复核当前归属和修订版本后返回原快照，不写数据库、不递增 revision；有变化才走 CAS 更新。显式 PUT 的版本更新语义保持一致。
- 单实例后台保留一份进程内轮次游标，每轮间隔 300 秒；每个 tick 一次按 bot_uuid keyset 查询，
  最多 16 张 URL-backed Card（单张上限 1 MiB），逐项处理后推进游标。完整一轮可能跨多个 tick。
  重启重置轮次，不保存每 Bot 的持久化调度/错误字段。
- 批次中一个 URL 失败不阻止后续 Card；失败后保留旧快照和 revision，不写失败状态，记录脱敏日志。
  写库错误向上返回，不能报告成功；游标推进避免坏地址长期霸占第一页。
- 成功 Card 按原校验规则更新，通过 revision 拒绝迟到覆盖。新请求仍可使用最后成功快照，
  当前部署策略、Provider/Bot 禁用和凭据限制始终重新检查；在途任务按冻结信息继续。
- 初次拉取仍拒绝 no-cache/no-store；刷新遇到时拒绝保存本次响应，按失败保留旧快照。
  这是一期应用配置的 stale-on-error 策略，不宣称实现完整 HTTP 缓存语义。
- 不新增 ETag、Last-Modified、条件 GET、缓存 revision 之外的元数据或 per-Bot 退避状态。
  SQL 成本、扫描复杂度和验证范围见 `docs/specs/a2a/minimal-card-registration-v1.md`。

### BCS 内部边界

- 增加独立 A2A delivery target 和 BCS A2A 下行 adapter（HTTP+JSON/SSE outbound adapter），由 composition root 装配到现有投递入口。
- A2A JSON DTO、HTTP/SSE 编解码和绑定错误处理留在协议适配层；application/core 使用传输无关的任务和事件类型。
- 扩展注册、存储、任务关联及事件接入契约；同步更新 API 文档和 conformance tests。
- SQLite/MySQL 开发迁移将 `bcs_bots` 收敛为三个 Card 列，移除冗余接口、凭据和刷新元数据；任务关联按运行持久化边界保存，不将每轮任务塞入 Bot 行。不修改已冻结历史迁移。

### 两端 adapter 的职责

```text
Harness Engine
    ↕ Harness A2A SDK adapter
标准 A2A 1.0 HTTP+JSON / SSE + 已协商扩展
    ↕ BCS A2A 下行 adapter（BCS 服务端）
BCS 内部命令、事件与持久化
```

- **Harness A2A SDK adapter**：将 Harness 执行转换为 A2A Task、Message、Artifact、状态更新及已协商的 BCS 工具 / Structured Input 扩展，对外可由其他支持相应能力的 A2A Client 使用，必要审批须支持 Structured Input；基础文本服务不强制要求工具观察扩展。核心 A2A 层不依赖 BCS `delta_text`、文本片段或历史存储；BCS 工具事件契约仅由独立扩展编码层承载，不透传整个内部事件包。
- **BCS A2A 下行 adapter**：将 BCS 命令转换为 A2A 请求，将 A2A 响应转换为 BCS 事件；负责 Artifact 与已投递文本状态、delta 转换、工具事件分段兼容及恢复校准。本文涉及 A2A → BCS 的转换均归此层。
- 一期文本替换限制属于 BCS A2A 下行 adapter 的能力边界，不是 A2A 标准或 Harness SDK 的协议限制。Harness 按引擎实际语义产生追加或替换；不得为了适配 BCS 将替换误报为追加。demo 的追加型场景是联调选择，不构成 SDK 禁止替换的要求。

## BCS 与 A2A 协议映射

本节定义一期适配契约，不假设两套协议逐字段等价。A2A 数据模型以官方 1.0 定义为准，下表使用 HTTP+JSON 绑定的 JSON 字段名与枚举表示。参考 [A2A 数据模型与事件定义](https://a2a-protocol.org/v1.0.1/specification/#4-protocol-data-model)。

### 下行命令与消息字段

一期具体 HTTP 端点以 Card 中选定的 HTTP+JSON 服务地址为基础，按 [A2A HTTP 绑定](https://a2a-protocol.org/v1.0.1/specification/#113-url-patterns-and-http-methods) 调用：

| A2A 操作 | HTTP 绑定 |
| --- | --- |
| SendStreamingMessage | `POST /message:stream`，响应 `text/event-stream`；正常聊天使用此入口，提交与后续事件通过同一条 SSE 连接完成。 |
| SendMessage | `POST /message:send`，`configuration.returnImmediately=true`，返回 JSON Task；保留给只提交、稍后查询或订阅的调用方，交互回复同样使用此接口。 |
| SubscribeToTask | `POST /tasks/{id}:subscribe`，响应 `text/event-stream`，每个数据事件承载标准 A2A StreamResponse。 |
| GetTask | `GET /tasks/{id}`。 |
| CancelTask | `POST /tasks/{id}:cancel`。 |

仅异步 SendMessage 使用 `returnImmediately=true`；SendStreamingMessage 不依靠该配置启动流式输出。两种入口都以后台 Task 执行为基础，不因订阅断开而取消任务。Card 声明 `HTTP+JSON` 和 streaming 能力，一期不声明尚未实现的 gRPC 接口。SOFABoot 同一 HTTP 服务提供 Card 和上述业务端点，配置对应访问控制及 SSE 响应刷新，不能让网关缓冲整个任务结果后才返回。

| BCS 命令 | A2A 调用与处理 |
| --- | --- |
| `chat.send` / `BotDeliveryKind::Send` | 调用 `SendStreamingMessage`，首次收到 Task 时持久化任务关联，然后消费同一条 SSE 的后续事件，不额外发起初始 SubscribeToTask。Harness 首事件返回已保存到选定 TaskStore 的 Task；第三方直接返回 Message 时按下述 Message 分支处理。 |
| `chat.abort` | 从可信 Bot/Session/Run 关联解析远端 taskId，调用 `CancelTask`。作用域取消只能在唯一活动任务可确定时下发；不能用 BCS run ID 代替 taskId，也不能扩大取消范围。 |
| `chat.inject`、`chat.history` | 已弃用（deprecated），不属于本期 A2A 映射、实现或验收范围。保留此行仅标识旧协议状态，不要求新 adapter 实现；A2A GetTask.history 仍只是任务消息历史。 |
| `bot.ping` | 无标准等价业务 RPC；不能通过提交空任务模拟。连接或健康检查只能报告其实际验证的范围。 |
| `interaction.resolve`（exec） | 按 Structured Input schema 将用户答案转换为结构化 Message，通过 SendMessage 提交给原 taskId/contextId；取消按下节规则处理，不创建新 Task。 |
| `interaction.resolve`（mode_switch）、`TaskDispatch`、`TaskMessage`、`TaskResult` | 尚未纳入一期映射，明确拒绝；不将 BCS 协作消息伪装为 A2A Task 生命周期操作。 |

| BCS 输入字段 | A2A 字段或本地处理 |
| --- | --- |
| `message.content` 文本块 | 保持顺序和原始空白转换为 `message.parts[].text`，不重复注入 BCS 已组合的上下文。 |
| 发送方角色 | 下行统一为 `ROLE_USER`，表示 A2A client 输入；这不改变 BCS 内的真实发送者身份。 |
| 规范 `BotDeliveryCommand.run_id` | 作为本轮稳定的 A2A `messageId`；队列 attempt/frame ID 不替代它。对普通第三方这只是关联选择；自家 Harness 在协商幂等提交扩展后提供下述保证。 |
| Bot + 规范 Session | 本地查找远端 `contextId`；首次省略，后续使用远端分配的值。新一轮不填写上一轮 taskId。第三方没有返回 contextId 时不能宣称已建立可续聊会话。 |
| `bcs_group_id`、`bcs_session_id`、目标 Bot、provider ref | 保存在本地可信运行关联中，不要求远端回传，不从远端 metadata 恢复权限或归属。 |
| `channel`、`session_context`、tags | 标准 A2A 无对应字段；复用 BCS 现有文本上下文构造结果传递必要语义，协议对象留在本地。需要独立结构化语义的多 Agent 场景不在一期内。 |
| `attachments`、非文本内容块 | 一期发送前明确拒绝，不静默删除、不自动下载 URL。 |
| `timeout_ms` | BCS 本地执行预算，Structured Input 人工等待按独立期限暂停执行计时；HTTP 请求超时仅限制对应请求等待，不能当成远端任务 TTL。预算耗尽时请求 CancelTask 并区分是否确认停止。 |
| `idempotency_key` | 保留现有 BCS 入站去重语义，不假定标准 A2A 提供执行恰好一次。 |
| 可信 Provider 绑定与固定下行类型 | 从既有 Provider 凭据仓储取值，转为 HTTP `Authorization: Bearer ...` 请求头，同时发送 `A2A-Version: 1.0` 和已协商的 `A2A-Extensions`；不进入 Message.parts、Card 或日志。原 HTTP bypass headers 不自动转发。 |

### 入站 StreamResponse 与 BCS 事件

BCS A2A 下行 adapter 处理 A2A 的四种响应变体：Task、Message、TaskStatusUpdateEvent、TaskArtifactUpdateEvent。所有 BCS 输出均由已验证的运行关联填充 `BotEventCommand.bot_id/run_id/group_id/bcs_session_id`；remote taskId、contextId 不覆盖这些字段。事件中携带的远端标识必须与关联一致，否则拒绝接入。

| A2A 响应或状态 | BCS 处理 |
| --- | --- |
| Task 快照 | 保存远端任务状态和 Artifact 快照，再按状态映射；不是一个新的 BCS run。Task.history 不重复写入 BCS 会话历史。 |
| SUBMITTED / WORKING | 更新运行进度；不生成 `chat.final`，无文本时不制造空文本 delta。 |
| TaskArtifactUpdateEvent | 一期支持首次文本、后续追加及最终完整快照校准；adapter 按 taskId + artifactId 维护产物及已输出文本，转成 BCS `chat.event / Delta` 的 `delta_text`。中途替换明确报适配不支持，具体规则见下文。`lastChunk` 只结束某个产物，不能结束整个 run。 |
| COMPLETED | 必要时先 GetTask 校准完整产物，将尚未输出且可安全追加的文本送入 Delta，再输出 `chat.event / Final`。Final 沿用 BCS 追加模式的收尾语义，不把全 Task 文本再次追加或作为当前片段写入历史。最终读取失败进入恢复处理，不能凭流关闭伪造成功。 |
| FAILED | 输出 `chat.event / Error`，结构化记录远端失败原因，保留已有文本，不把状态说明追加成答案。 |
| REJECTED | 输出 `chat.event / Error`，以独立 errorCode 区分主动拒绝与执行失败。 |
| CANCELED | 输出 `chat.event / Aborted`，由统一终态入口处理；与取消 RPC 返回去重。 |
| INPUT_REQUIRED | 已协商且可表达的 Structured Input 请求转为 BCS exec interaction requested，保持 Task 等待用户。未支持的 schema 或交互类型明确报适配错误并尝试取消，保留真实远端状态。 |
| AUTH_REQUIRED | 一期未实现动态授权流程，明确报能力错误并尝试取消；取消未确认时保留待清理标记并阻止该 context 自动推进，不能伪报远端 failed/canceled。 |
| UNSPECIFIED 或未知状态 | 记录协议异常并查询 GetTask；按有界恢复预算处理。预算耗尽后尝试精确取消；无法确认远端终态时保留 Unknown 与执行槽，等待显式处置，不推断成功或自动放行下一次执行。 |
| 独立 Message 响应（无 Task） | `ROLE_AGENT` 文本作为本次独立回复，映射 `chat.event / Final`；不虚构 taskId 或订阅。Harness 主验收仍要求异步 Task 路径。 |
| 已关联 Task 的 Message、status.message | 已协商的 BCS 工具事件和 Structured Input 请求按各自扩展映射；其他内容作为状态说明保存在任务诊断中，不猜测为工具事件或默认最终产物。任务完成时优先使用 artifacts，不重复拼接 history/status message。 |
| A2A SSE 流正常关闭或断开 | 均不等于 COMPLETED；查询任务并按已定义恢复流程处理。SSE 注释心跳不映射成 BCS 业务事件。 |

错误映射保留 HTTP 状态及 A2A HTTP 错误体中的类型、详情和可用的结构化原因，写入现有 `errorKind/errorCode/errorMessage`，对可见错误信息脱敏。发送前明确不支持或明确拒绝可以作为失败；RPC 超时、连接中断等可能已执行的情况保持结果未知，仅协商到幂等提交保证时允许按下述规则重试。鉴权失败不触发匿名降级。

初始提交收到完整 HTTP 400/401/403/404/405/406/415/422 拒绝时，发送 `errorKind=a2a_protocol`，`errorCode` 使用识别到的标准 reason，否则为 `HTTP_<status>`。`errorMessage` 保留 HTTP 状态和经过筛选的 typed ErrorInfo 原因；远端任意 message、metadata、URL 和未知 detail 不进入可见诊断。408/409/429、5xx、超时及不完整响应保持结果未知，不能仅凭 reason 推断未执行。GetTask/CancelTask 和交互续传错误属于相应请求的失败，在请求错误中保留同样的安全原因，不据此发布 Task 终态或伪报取消成功。

### BCS A2A 下行 adapter 文本转换与一期兼容边界

本节的 adapter 均指部署在 BCS 服务端的 A2A 下行 adapter。由它消化 A2A Artifact 与 BCS 文本片段的差异，沿用 BCS 现有追加、工具事件分段和历史持久化语义，不增加按 Artifact 定向修订 BCS 消息的能力，也不改成全 Task 累计快照投递。这些处理不进入 Harness A2A SDK adapter。

一期收窄为“流式追加文本＋最终完整快照”，不实现通用的中途替换快照转换。以下首次、后续和最终均按单个 Artifact 判断；最终快照以 `append=false`（或缺省）且 `lastChunk=true` 表示。GetTask / Task 完整快照用于初始建档、最终校准和断流恢复，按下述快照规则核对，不作为中途替换事件处理。

| A2A 文本更新 | adapter → BCS |
| --- | --- |
| `append=true` | 在已知基线上更新本地 Artifact，将新增文字作为 `chat.event / Delta` 的 `delta_text`。 |
| `append=false` 或缺省，首次取得文本 | 保存快照，将全部文字作为首次 delta 输出。 |
| 已建立基线后再次收到 `append=false` 或缺省，且 `lastChunk` 不为 true | 中途替换一期不支持；即使文本相同或仅增加后缀，也明确报适配能力错误，不误当增量追加。 |
| 最终完整快照与已输出文本相同 | 不重复输出文本事件，等待 Task 终态。 |
| 最终完整快照以该 Artifact 已输出文本为完整前缀 | 只输出新增后缀，不重新发送完整快照，等待 Task 终态。 |
| 最终完整快照修改或缩短已输出文本，包括清空非空文本 | 一期不支持；明确报适配能力错误，不能把替换误作追加、静默忽略修改或仍报告完整成功。 |

- adapter 按 taskId + artifactId 保存当前产物和已成功接入 BCS 的文本位置，区分远端状态与已投递状态。前缀比较保持原始 Unicode 文本和空白，不做 trim、模糊匹配或仅按长度截取。空增量不输出；重复文字可能是合法增量，不能按文本内容哈希去重。
- 工具事件映射为现有 `tool_call_start/end`，BCS 自然结束前一文本片段并在后续 delta 开始新片段。adapter 的 Artifact 状态不因工具事件清空，也不重新发送工具前的文字；即使同一 Artifact 跨越工具事件继续追加，也只下发尚未输出部分。
- 例如 A2A 先输出“我先查询”，再发工具事件，最后追加“结果是成功”：BCS 依次收到前一段 delta、工具 start/end、后一段 delta，不收到“我先查询结果是成功”的全量快照。
- Harness 为同一产物保持稳定 Artifact ID，不同独立发言可以使用不同 ID，并保存完整产物供 GetTask 查询。SDK 按引擎实际输出表达 append 或替换，不感知哪些文字已被 BCS 显示。BCS 服务端联调优先使用追加型发言；第三方或 Harness 产生合法修订时，由 BCS A2A 下行 adapter 判断能否转换，不要求 SDK 篡改产物。工具输出若非助手回复，不转换成答案文本。
- 多 Artifact 的实时文本按接收事件顺序输出，保持各 Part 文本顺序，不为了重建产物排序而重排已输出历史，不自动插入分隔符或把 name/description/metadata 混入答案。首次只取得全量 Task 快照时按其 artifacts/parts 顺序输出；这仅是恢复出的文本，不能宣称重建了离线期间文本与工具的原始交错顺序。
- Final 前先校准并补发安全新增文本。已有 delta 的 run 由 BCS 累加结果收尾；不在 Final 中重送全 Task 文本以跨越工具分段覆盖历史。仅有最终文本的 Task 也先走首次 delta，再统一 Final；合法空结果可直接结束。独立 Message 响应仍按其无 Task 分支处理。
- 不兼容替换保留远端快照和明确的诊断原因，BCS 结果标记为适配失败而非伪造远端 FAILED。远端仍在执行时尝试取消并记录是否确认；保留此前显示的部分结果，不用错误内容冒充最终答案。
- A2A 不保证跨订阅重放游标。重连先隔离旧订阅，使用完整 Artifact 快照与已投递位置核对：相同不输出、前缀扩展仅补发缺失后缀、修改或缩短已输出文本则明确失败。这是恢复校准，不扩大中途替换事件的支持范围。无法建立可信基线时暂停增量投影，转为有界 GetTask 校准，不盲目消费可能重放的 append。一期仅保证单进程内断流校准；BCS 进程重启后的精确位置恢复、原子事件日志和多实例接管另期设计，不宣称无损恢复。无法确认基线时明确报告恢复不确定性。
- 中途替换和任意文本修订不属于一期支持范围；对外文档明确这属于 BCS 适配限制，不是 A2A 协议或 Harness SDK 禁止替换。SDK 忠实输出引擎语义，一期联调选择追加型场景；未来确需支持时再扩展转换规则或增加消息修订能力。
- A2A 核心协议未定义 BCS 工具过程的完整等价字段；一期通过版本化 BCS 工具扩展支持工具过程展示，通过 Structured Input 支持 exec 审批，不从自由文本或任意 data/metadata 猜测。thinking、mode_switch 交互、usage 和 routing 未支持的字段不伪造。
- 除已协商的 BCS 工具事件和 Structured Input 数据外，非文本 Artifact/Message 内容及不支持的必需扩展产生明确的能力错误，不能丢弃后仍报完整成功。非必需 metadata、Part 属性和 Artifact 标识保留在适配器任务快照中，不自动转为 BCS 路由或权限信息。
- A2A status timestamp 转为 BCS 毫秒时间；没有时间戳的内容事件使用接收时间，仅用于展示和诊断，不用于判断事件先后或授权。
- 协议、绑定与更新方式分开建模：协议为 A2A/1.0，绑定为 HTTP+JSON（未来可增加 JSON-RPC/gRPC），更新方式为 stream（一期 SSE；未来可增加 push/poll）。不将三者组合成不断增长的枚举值。
- 在新增投递配置和事件接入元数据中保存这些维度，由适配层将不同绑定归一化为 A2A 事件，再转换成 BCS 事件。明确区分旧 Provider SSE 和 A2A，保持原有终态幂等、生产者鉴权和持久化错误传播；不为此全面重构旧 Provider 路径。

### 版本化 BCS 工具扩展

一期复用 A2A 可扩展数据结构承载现有 BCS 工具过程语义，扩展仅用于过程观察，不替代核心 Task、Artifact 或任务操作。Gemini CLI development-tool RFC 仅供设计参考，不使用其 URI、不承诺 Gemini 兼容，也不假设未来 A2A 标准将采用该模型。

- 实现前形成独立的 v1 扩展契约：确定稳定的项目所有扩展 URI、版本、JSON schema、事件名、必需字段及兼容规则，并提供两端共享的线格式夹具。字段以现有 BCS 工具 start/end 契约为依据；逐字段列明工具调用 ID、工具名、参数、结果和错误的映射，不复制整个内部 EventFrame。具体字段名以核对后的版本化契约为准，不临时猜测。
- 工具事件通过 `TaskStatusUpdateEvent.status.message` 的结构化 Part 承载，使用扩展契约规定的命名空间和事件类型明确识别；具体封装按 A2A 1.0 与所选 SDK 验证。状态更新保持 Task 的真实状态，不因发送工具事件强制改成 WORKING。文本答案仍走标准 Artifact，工具输出不拼入答案，不使用自定义流结束标记代替 Task 终态。
- Card 声明扩展，按 A2A 扩展机制显式协商；未协商不发送该工具扩展。普通客户端可以只消费标准文本和任务状态，工具观察在不影响执行时为可选能力。BCS 若要求展示工具过程，则调用前确认支持；必需扩展不支持时明确拒绝。未知必需版本、错误封装或缺失必需字段不能静默降级。
- Harness 的独立扩展编码层将工具回调转换为约定的 BCS 工具事件；BCS 下行 adapter 解码后经现有 application 入口接入。Bot/Session/Run 权限、路由、数据库字段和凭据不进入扩展载荷；本地身份由可信 Task 关联填充，远端工具数据不作为协作指令执行。
- 按 taskId 与工具调用 ID 维护生命周期，同一开始或结束事件最多接入一次。保留结构化参数、结果和错误；失败或取消一个工具不等于整个 Task 失败或取消。仅恢复出结束记录时不伪造曾接收到开始事件，没有重放保证时不宣称恢复完整过程。
- 一期只承载现有工具开始、结束和结果，不新增持续进度、thought 或 slash commands。工具调用需要用户授权时走下述 Structured Input exec 流程，不伪装成普通工具事件或 ask_user，也不自动批准；未支持的审批类型明确拒绝并执行取消清理。
- 若未来出现适合的正式工具标准，在协议边界新增对应编解码并按 Card 协商选择，保留旧扩展的兼容期。Harness 执行和 BCS 内部消费逻辑尽量保持不变；不承诺未来标准字段必然与本扩展等价。

### Structured Input：一期 exec，ask_user 延期

复用 [Structured Input Extension](https://github.com/a2aproject/experimental-ext-structured-input/blob/ed844541803ad096dc2e12e084a29d761d91b44e/v1/spec.md) 的固定版本。扩展仍属实验性；SDK 不自动提供 Harness 暂停/恢复或业务授权。exec 的具体审批输入与确认凭据遵循 [BCS exec profile](../a2a/structured-input-exec/v1.md)，不新增私有 RPC。

上游基线固定为提交 `ed844541803ad096dc2e12e084a29d761d91b44e` 的 `v1/spec.md`；协商 URI 保持上游规定的 `https://github.com/a2aproject/experimental-ext-structured-input/tree/main/v1`，不把提交地址作为另一个 URI。使用 JSON Schema 2020-12；请求/回复的标记放在 **DataPart.metadata**，分别为 `input_schema` 和 `structured_input` 并携带请求 `id`，不能误放在 Message.metadata。Task 同时只有一个 pending input request；并行工具审批需组合为同一 schema，BCS 可以投影成多张本地审批卡。

- 一期保持现有 exec 的 allow_once/deny 能力。Structured Input 使用枚举 schema 承载决定，BCS adapter 映射现有 kind=exec 审批卡，不降级为普通 ask_user。工具调用 ID、工具名、参数版本/摘要、请求身份及审批有效期必须与决定绑定；额外审批描述的承载按固定扩展契约核实，若需补充 BCS 属性，必须版本化并显式声明，不声称通用 schema 自带授权语义。
- Harness 通过现有 ConfirmationRequiredEvent / ConfirmationInput 接入真实工具确认。批准只执行一次，拒绝不执行该工具；deny 本身不等于 CancelTask，由真实引擎后续状态决定 Task 是否继续。工具参数改变必须重新审批，冲突、过期、重复及跨 Bot/Task 的决定按现有授权和身份规则处理。
- Harness 创建 INPUT_REQUIRED；BCS 验证请求后建立 interactionId 到 Bot/Session/Run、taskId/contextId 及具体审批/问题的关联，可靠保存交互后主动关闭本地 SSE 订阅。Harness 释放订阅资源并保持 Task 等待，不将断流视为取消。等待中不循环重新订阅，回答后 GetTask 校准并按需 SubscribeToTask。
- 分开配置执行预算、单次人工等待期限和 Task 总生命周期上限。执行预算沿用现有默认；`[a2a_input]` 的 `wait_timeout_ms` 默认 600000（10 分钟），`total_timeout_ms` 默认 1800000（30 分钟），要求 `0 < wait <= total <= 86400000`，本地联调配置显式给出。Harness 默认审批过期同为 10 分钟，两端取更早期限；不能把人工等待计入原执行预算。进入等待暂停剩余执行预算；非最后一项决定保持等待，最后一项决定可能立即触发引擎执行，因此必须在发送该 continuation **之前**恢复执行计时并调整期限，不能等 ACK 到达后才开始计时。同一请求重复更新不重置等待期限，拒绝或结果未知也不能重新获得一个完整等待周期。BCS 超时清理、Harness 定时器及 Redis run context deadline/TTL 一起调整，覆盖等待期与保留期，不能只暂停一端。
- 预算计算由协议无关的 Core 负责；应用服务 `RunBudgetService` 用可信 Bot/Group/Session/规范 Run 编排 managed delivery 的 deadline 与 Run context/active/transport/alias 的 TTL，协议 adapter 随后保存 A2A 关联，全部成功后才能发布交互卡片或发送 continuation。协议 adapter 不直接调用 Core，也不通过 re-export 绕过应用边界。这些存储不承诺跨 SQL/Redis 原子事务；任一步失败必须 fail closed 并尝试取消，不可显示一张关联可能已过期的可用卡片。变更保留原 attempt、request、执行槽和输出归属，不创建或复活 producer。终态与 deadline 变更在同一 BCS 进程内互斥；这不是多实例租约。
- 等待期间保持该 Bot/Session 的逻辑执行槽，后续普通消息排队；这是一期明确的产品行为。交互回复、取消不进入该普通 send 队列。人工等待或总生命周期到期请求取消并使卡片失效，取消未确认时不伪报远端已停。
- 跟踪或事件转换失败先使待审批卡失效，并将同一规范 Run 标记为 Unknown（已有取消意图则为 CancelUnknown）。仅在精确 CancelTask 或 GetTask 确认远端终态后发布本地错误终态并释放槽位；取消失败且查询仍非终态时不发布伪终态，不重发原请求。持久化失败必须记录并保留执行阻塞。
- 回复使用新的 messageId，通过 SendMessage(returnImmediately=true) 推进原 taskId/contextId，沿用原 Run；重复提交不重复应用决定。RPC ACK 不等于审批已应用，只在远端明确接受或可信交互证据确认后关闭原交互。输入校验失败保留可修正请求；旧回复不能误投新请求，不以任意 WORKING 帧作为接受证明。
- 按用户 2026-10-29 最新确认，ask_user 本期不验证、不实现，留待二期。以下仅记录二期目标，不能据此对外声明当前能力：支持范围为平面 object 的字符串、枚举单选、枚举数组多选，沿用现有 1～4 题、每题最多 4 个选项。保持原始类型、枚举值和必填/跳过约束，复杂或越界 schema 明确拒绝。
- 用户显式取消整个交互任务走 CancelTask；ask_user 的取消按整任务取消处理，与 exec 的 deny 区分。权限校验仍由 BCS 现有交互入口负责；答案不写普通日志。默认 Harness 重启后 Task 与审批关联丢失，不承诺恢复引擎内部暂停点。BCS 重启精确恢复另期设计。

工具观察走 BCS 工具扩展，审批/问答走 Structured Input，同一交互不重复通过两种扩展发布。不新增私有 ResolveInteraction RPC；现有 interaction.resolve 是本地命令。mode_switch 仍不纳入一期。

## 异步执行与恢复

正常聊天主链路为：

`SendStreamingMessage → SSE 首个 Task → 保存任务关联 → 消费同一条 SSE → BCS 输出事件`

保留独立异步提交入口：`SendMessage(returnImmediately=true) → 保存任务关联 → 稍后 GetTask / SubscribeToTask`。它用于只提交后续再跟踪的调用方，不作为 BCS chat.send 的默认路径。两条入口共用任务执行、存储和事件管理，不维护两套任务状态机。

- Harness 每轮新的 chat.send（包括简单文本问答）均先保存 Task 到选定 TaskStore，在首个 SSE 事件中及时返回 Task，再输出后续执行事件；独立异步提交则返回 JSON Task。BCS 不等待模型完成，收到身份后先保存关联再接入后续业务事件；保存失败不得假报接入成功。交互回复推进已有 Task，不新建 Task。
- BCS 保存 Bot、Session、Run 与远端 `taskId`、`contextId` 的关联。远端分配的 context 按 Bot/Session/目标路由隔离。Harness 新一轮默认创建新 task；BCS 持久化允许同一可信作用域的多个 run 关联同一远端 task，不把自家 SDK 的一对一策略固化为数据库约束。文字澄清的产品入口仍属后续范围，不从普通消息或相同 Session 猜测续接目标。
- Harness 输出标准任务状态和文本 Artifact 更新。BCS A2A 下行 adapter 一期仅转换首次文本、后续追加和最终完整快照；最终快照相同则去重，仅增加后缀则补发。中途替换以及最终快照修改已输出文本均明确报 BCS 适配不支持，不能声称远端违反 A2A 协议。Task 终态只结算一次，Artifact 的 lastChunk 不触发 run 完成。
- 独立异步提交和断流恢复中，任务可能在订阅前完成，通过 `GetTask` 获取结果并按统一终态入口收尾。正常流式提交也覆盖快速完成、首个 Task 后立即终态的情况，不依赖额外订阅。
- 订阅断开不取消执行。BCS 有界退避后查询任务：终态读取结果，非终态重新订阅；不重新提交原始请求。
- 断流按连续失败计数，最多三次恢复尝试；只有恢复流中的事件通过校验并成功接入后才重置计数。仅 GetTask 成功或订阅 HTTP 成功但没有有效事件，不重置预算。持续正常恢复的长任务可跨越多次断流，所有尝试仍受当前整轮 deadline 约束；未知状态查询保留独立上限。
- 不假设标准 A2A 提供事件重放游标。重连期间按上述基线规则校准完整 Artifact 快照，对追加型输出只补发缺失文本；一期不承诺恢复所有中间事件或工具交错顺序，不兼容修订与恢复不确定性明确报错。
- 流式提交在首个 Task 到达前断开，或独立异步提交超时且尚未获得 taskId 时：协商到 Harness 幂等提交扩展则使用同一身份和内容有界重试；未协商则保持结果未知，不自动切换入口或盲目重发。
- 收尾时先 CAS 保存远端终态，再发布可能释放 BCS 执行槽的 Final / Error / Aborted；独立 Message 回复也按 Completed → Final 顺序处理。持久化失败不发布终态。保存成功后若本地终态接入失败，仅以相同 Run/作用域/负载最多重试 3 次，间隔 250ms；不重放 delta、不重写终态、不重发或取消已确认结束的远端任务。重试耗尽保留远端终态，尝试将本地投递跟踪标为未知并保持保守阻塞，不提前标记本地 transport Terminal。一期不增加重启 outbox 恢复。
- 用户取消调用 `CancelTask`。只有远端确认 canceled，BCS 才报告取消成功；完成与取消竞争以实际终态为准。
- 一期 BCS 单实例，保留正常多轮所需的 Bot/Session/context 关联和运行状态，断流在当前进程内恢复。进程重启后不自动重放不确定的输出或任务，不承诺在途事件无损恢复；可靠消费位置、多实例租约和接管整体后移。
- 一期要求启用 BCS 服务端队列，保证同一 Bot/Session 串行 Send；适配器的 active 查询与 Run 创建不构成跨实例原子准入，也不承诺直接并发调用投递端口时的唯一活动任务约束。
- 初始提交结果 Unknown、无主的在途记录或无法确认远端终态时，同一 scope 保持阻塞，不按本地 deadline 自动放行。已知 Task 可通过现有精确取消尝试确认终态；缺失 taskId 或取消仍不确定时，一期不提供专门的人工解除阻塞入口。重启接管、确认与解锁管理整体后移，这是本期明确的运行限制。
- `input-required` 按已协商 Structured Input 的 exec 流程处理；不支持的输入 schema、交互类型或 `auth-required` 返回明确的能力限制，不伪装成成功完成。

### 初始提交与幂等能力边界

- 默认 Harness 不提供自定义持久化提交去重，不将标准 messageId 等同于执行幂等。
- BCS 只在第三方 Server 的 Card 声明且显式协商自有提交去重扩展时，保留现有有界重试兼容能力；该扩展的服务端可靠性由宿主保证。
- 未协商时，首个 Task 前断流保持结果未知且不重发；取得 Task 后通过 GetTask / SubscribeToTask 恢复观察，不重复提交。
- 标准 ListTasks 不能可靠代替初始提交去重；不扫描 history 推断“未执行”。

### 反向协作与后续兼容

Agent → BCS 一期保留现有受限 Bot 凭据和 API/工具入口，A2A 下行不替代这些能力。原凭据通过现有可信配置注入，不从 Card、Task metadata 或 Provider 下行 Bearer 推导反向权限；不在一期新增身份委托协议。TaskDispatch/TaskMessage 的 A2A 映射仍在后续范围，manager-demo 保持 webhook 或原工具路径。

后续按实际需要评估 JSON-RPC 绑定、A2A 0.3 兼容、文本修订、push 和 OAuth2/mTLS。绑定与协议版本分别评估；一期是 A2A 1.0 REST 的受限接入，不宣称所有 A2A Agent 均可接入。长任务更新方式先测量连接与内存容量，再决定是否采用 push，不提前将其定为唯一主路径。

## SDK 复用与框架集成

两端优先复用 A2A 项目维护的 SDK，把自行实现的范围集中在框架装配、引擎桥接和 BCS 业务映射。SDK 版本与协议版本分别锁定；协议以已发布的 v1.0.1 规范为依据，线上协商版本为 1.0。具体依赖版本、上游提交和兼容测试结果在最小集成验证后记录，不直接跟随 main 或使用浮动版本。

### Harness：Java SDK + SOFABoot

- 优先复用 [官方 Java SDK](https://github.com/a2aproject/a2a-java) 的协议模型、请求处理、任务存储接口和事件管理能力。通过 `AgentExecutor` 桥接现有 Harness 执行及取消，默认使用官方内存存储，并提供 TaskStore、TaskStateProvider 和队列工厂注入点；持久化及多实例协调由宿主按需接入。
- SOFABoot 集成层负责 Spring Bean 装配、HTTP+JSON/SSE 端点、鉴权和应用生命周期。先验证 SDK 公共组件与现有 SOFABoot/Spring Boot、JDK、序列化库及异步线程模型的兼容性，不直接引入 Quarkus reference server 启动另一套运行时。Java SDK 的公开要求为 Java 17+，实施时核对所选版本。
- 首选参考 [Sh1bari/spring-a2a](https://github.com/Sh1bari/spring-a2a) 的 Spring MVC REST 集成：自动配置、AgentCard、AgentExecutor、可替换运行时 Bean 及 REST Starter。它是复用官方 SDK 的独立社区项目；[官方讨论 #964](https://github.com/a2aproject/a2a-java/issues/964) 记录了外置维护的决定，作者提供了契约测试与 TCK 工作流并报告通过。本文未复跑这些测试，不据此宣称已适配 SOFABoot 或具备生产验证。
- 补充参考 [spring-ai-community/spring-ai-a2a](https://github.com/spring-ai-community/spring-ai-a2a) 及 [Spring 官方介绍](https://spring.io/blog/2026/01/29/spring-ai-agentic-patterns-a2a-integration/)。其示例、主分支和已发布版本的 SDK、协议及 Spring Boot 基线可能不同；不为复用集成层而将 Harness 改写为 Spring AI ChatClient，也不直接照搬旧版 JSON-RPC 示例。
- Starter 是否直接采用由兼容验证决定；不兼容时参考其装配方式，编写最小 SOFABoot 集成层。工具扩展、Structured Input、真实取消和 Harness 暂停/恢复仍由我们实现，不假设 SDK 已提供这些业务语义。

### BCS：Rust SDK 客户端 + BCS 映射

- 部署在 BCS 服务端的下行 adapter 在 A2A 协议中是 **Client**。优先评估 [官方 Rust SDK a2a-rs](https://github.com/a2aproject/a2a-rs) 的核心模型与异步客户端；上游公开支持 HTTP+JSON/REST、JSON-RPC 和 SSE。对应发布包为 `a2a-lf`、`a2a-client-lf`，仓库 crate 名为 `a2a`、`a2a-client`；实施时核对实际发布版本及 Cargo features。
- SDK 用于请求/响应模型、编解码及异步 REST/SSE 通信；不因运行在 BCS 服务端而引入 SDK 的 `a2a-server` 或新增 A2A 服务端端点。一期仍只启用 HTTP+JSON，gRPC 留待下一期。
- BCS 自己负责 Card 注册缓存与权限、接口选择策略、Bot/Session/Run/task 关联、文本 delta 转换、工具与问答映射、恢复和终态幂等。SDK 类型限制在 outbound adapter 边界，不传播到 application/core。
- 最小验证核对 Rust 工具链、Tokio/HTTP/TLS 依赖、静态 Bearer、媒体类型、版本头、超时、SSE 取消与断流行为，以及 SDK 是否允许注入受 BCS 出站策略约束的 HTTP 客户端。SDK 默认发现、重试或重定向不能绕过已验证 Card、出站限制或已协商的幂等重试边界；缺口先明确再决定补集成层或局部使用 SDK。

## Harness 实现

在现有 demo 中新增 Harness A2A SDK adapter 模块及启动配置，复用 SOFABoot 现有 HTTP 服务和端口，提供 AgentCard、A2A REST 端点及 SSE 订阅；对外使用标准 A2A 与已协商扩展，仅在工具扩展中暴露约定的必要 BCS 工具字段。

- Card HTTP 端点提供适合固定 300 秒刷新周期的声明；一期不要求 ETag/304。注册后，部署脚本可选调用 BCS 刷新接口加速变更生效；管理凭据由部署配置注入，不进入 Card 或日志。
- 按上述 SDK 复用策略提供固定版 A2A HTTP+JSON/SSE 接入，通过 Harness 执行桥接接入现有 `AgentEngine`、Definition resolver、SessionStore 和执行协调能力；只补 SDK 未覆盖的 SOFABoot 集成逻辑。
- 通过独立扩展编码层将 Harness 工具执行回调转换成固定版本 BCS 工具事件，随任务状态更新发送；保留稳定工具调用标识和最新记录，助手回复输出标准文本 Artifact。追加/替换忠实表达 Harness 输出语义；转换为 BCS delta 和工具分段的职责完全属于 BCS A2A 下行 adapter。
- 先将现有真实 exec 审批接入 Structured Input，保留 allow_once/deny 和真实引擎恢复验收。ask_user 的能力验证及同 Task 暂停/回答/恢复场景推迟到二期，不以模拟状态代替。
- 实现 `SendStreamingMessage` 主入口、`SendMessage` 独立异步接收、`GetTask`、`CancelTask`、`SubscribeToTask`；按所固定协议的必需方法要求补齐基础服务行为。
- 将执行生命周期从现有 webhook/SSE 订阅生命周期中分离，使任务可以在没有订阅者时继续运行。
- SOFABoot 通过异步 HTTP/SSE 输出任务订阅，避免长期占用同步请求线程；设置有界输出缓冲，验证首事件及时 flush、慢消费者、连接断开和应用关闭。订阅断开只释放订阅资源，不自动取消 Task。保留原 Provider webhook 路径，A2A 使用自己的路由和事件编码。
- 通过可替换 TaskStore 保存 task、context、状态和 Artifact（默认内存）；订阅先提供当前任务快照，再发送后续更新，快照与订阅切换不能丢失终态。
- 保留同 Session 串行、不同 Session 并发、队列有界及真实取消收尾语义；慢订阅者不能导致无限内存增长。
- 默认不保证服务重启后续聊或找回 Task；宿主共享存储和执行协调属于后续集成，不自动重放可能有副作用的工具调用。
- 原 BCS webhook adapter 继续可用。A2A 示例使用独立 Bot 和运行状态，避免两条协议路径竞争同一任务。
- 新增显式注册和联调脚本，允许指定本次 BCS 构建版本，替代旧脚本固定历史 commit 的限制。

## 验证与交付

分三步交付：先完成版本化契约、测试夹具及两端 SDK 最小集成验证，再实现业务适配，最后完成跨进程联调。最小验证使用 SOFABoot + Java SDK 服务端和 Rust SDK 客户端，覆盖流式提交首个 Task、同流文本 Artifact、独立异步提交与稍后订阅、查询、真实取消和断流；验证通过后固定依赖版本，再接工具与问答扩展。

必须覆盖：

- Card 注册、发现、读取、刷新；非法 Card、版本不符、鉴权失败、出站地址拒绝及写入失败。
- 完整 Card 存储与返回的字段保真，特别覆盖 skill 的 id/name 区分、description、tags、examples、输入输出类型、鉴权声明及扩展字段；A2A 搜索和能力消费不依赖旧 skills 映射，非 A2A Bot 的发现行为保持兼容。
- 同表新增列的旧数据兼容、Card 与管理元数据原子更新、刷新后发现缓存失效；普通列表和调度不读取完整 Card，刷新查询使用到期索引及有界批次。
- 协议映射契约测试逐项覆盖上表未弃用的下行命令、四种 A2A 响应变体和全部任务状态；检查 BCS run 与 remote task 身份隔离、错误标识、无任务 Message 及不支持命令的明确拒绝。已弃用的 `chat.inject`、`chat.history` 不新增映射或专项验收。
- 两端边界测试：Harness SDK 可由独立 A2A Client 驱动，不需要 BCS 内部字段；使用标准 A2A 更新夹具分别验证 SDK 的追加/替换表达和 BCS 服务端的 delta 转换。合法替换超过一期能力时，只报告 BCS 适配限制，不要求修改 SDK 协议或把错误归因于远端。
- BCS 工具扩展 v1 共享夹具与双方互通测试：开始、结束、结构化参数/结果/错误、失败、取消、重复事件、仅结束记录恢复、未知必需版本、非法字段与未支持审批类型拒绝。验证 Card 声明和协商、未协商时不发送工具事件、普通客户端仍能消费标准文本；断言工具输出不混入答案、工具事件不改变真实 Task 状态、不传播内部路由/权限字段，失败工具不擅自终止整个任务。
- Structured Input 上游夹具与 Harness exec 联调：批准执行一次、拒绝不执行、参数变化重新审批、多 pending、重复/冲突/过期决定、越权、等待取消与超时、等待时关闭 SSE 和回答后重订阅。验证等待暂停执行预算、总上限及 Redis TTL，等待期间普通消息排队且回答/取消不被阻塞。ask_user 的文本、单选、多选、必填/跳过和 schema 越界验证均属于二期。断言交互回复复用原 taskId/contextId/Run，旧卡片失效，未收到远端接受证据不伪造 resolved。
- Artifact 首次文本、后续追加、最终完整快照相同或仅增加后缀、多产物、lastChunk 非终态、空输出和断流校准；覆盖文本→工具→文本，断言工具前文本不重复、Final 不重复写入整轮答案、前端与历史片段一致。中途再次发送 append=false（含缺省），即使内容相同或为前缀扩展，也必须明确报适配不支持；最终或恢复快照修改/缩短/清空已输出文本必须明确失败。恢复快照相同不重复输出、前缀扩展仅补后缀，不能由此放开中途替换事件；无法确认恢复位置时明确失败。重复字串不得被误去重，单进程断流恢复不能重复追加；进程重启精确恢复不纳入一期验收。非文本、必需扩展及无标准对应的工具/交互字段不能被静默转换。
- Card 注册、手动/部署脚本刷新、固定周期全量拉取；不支持的 no-cache/no-store 明确拒绝，不静默存储。
- 刷新失败保留旧快照、当前策略/权限限制及旧响应覆盖保护；新任务使用新配置、在途任务保留原接口。验证调度的分页、批次及数据库访问成本。
- 异步响应先于执行完成；真实 HTTP 异步响应及 SSE 状态与文本流；多轮 context 复用及不同 Bot/Session 隔离。
- HTTP/SSE 绑定验收：请求头、媒体类型、标准错误体、JSON/SSE 编解码、首事件及时送达、心跳、断流后重新订阅、订阅时任务已终态的竞争；SOFABoot 同端口 Card 与业务接口可达，不依赖 gRPC 运行时或端口。
- 流式提交及时返回首个 Task，关联保存后消费同流事件；首事件前断流仅在远端已协商幂等扩展时恢复，默认 Harness 与未协商第三方保持结果未知，首事件后断流通过 GetTask / SubscribeToTask 恢复。覆盖两种提交入口、快速完成、独立异步提交后订阅前完成、重订阅、最终文本去重、BCS 单进程断流恢复和 Harness 中断处理。
- 取消收尾、重复取消、取消与完成竞争、任务不存在；默认 Harness 不声明持久化幂等；BCS 兼容扩展的模拟 Server 覆盖相同请求重试、内容冲突、重试预算耗尽及未协商时不重发。
- Bot 独立 Card/接口路由、Definition 选择、错误 Bot 下查询/取消/回复拒绝、授权绑定及按可信 Provider/固定类型解析凭据（不新增 Bot 凭据引用列）。协议/绑定/更新方式分别记录，旧 Provider SSE 不混入 A2A 事件。
- 真实 Harness 文本事件验证：增量与最终模型快照、工具前后文本、重试和结构化输出；确认追加场景兼容。若最终文本改写已输出内容，明确记录一期限制，不修改 SDK 语义伪造兼容。
- 现有 WS 和 HTTP Provider 注册、投递、取消路径回归。
- SQLite/MySQL 迁移、架构边界检查、相关 Rust 测试、Java Maven 测试及修改源码文件行数检查。

Avernet 内提供不依赖 Harness 私有制品的 HTTP+JSON/SSE 测试 Agent，保证公共 CI 能独立验证 BCS。另在 demo 中使用真实 Harness 和可控模型完成跨语言测试，再运行真实模型与 Java 工具验收；真实模型所需网络或凭据不可用时单独报告，不能以 mock 结果代替。

最终交付包括跨仓库设计说明、接口契约、两端实现、启动/注册/验收脚本和脱敏验收结果。一期功能与真实模型 E2E 已完成；实际验证范围、明确延期项及架构门禁限制以 `acceptance-checklist.md` 和 `validation-results.json` 为准，不将编译成功等同于全仓测试执行或全部 CI 通过。

### A2A task / run / context 关联模型（2026-10-31）

详细字段和仓储契约见 `docs/specs/a2a/task-bindings-v1.md`。

- `bcs_a2a_tasks`：唯一远端 task 身份、可信作用域摘要、contextId、共享的
  `pending_input_json` 与独立 Task 乐观锁版本。不将本地 run 的终态等同于远端 Task 生命周期。
- `bcs_a2a_run_bindings`：每个 BCS run 一条，多个 run 可以引用同一 task。
  提交阶段 task 引用为空；收到远端身份后在同事务中建立绑定。Bot/Session/Provider/
  路由、接口协议和绑定、凭据类型、请求身份、状态、版本、期限与预算全部独立列出。
  `extensions_json` 只保存协商扩展数组，不再保存整个 `record_json`。
- `bcs_a2a_contexts`：Bot/Session/Provider/远端路由到 contextId 的长期映射，
  作用域字段独立列出，不再保存 `scope_json` 或重复的业务更新时间；保留 scope 摘要索引。
- 三张表均有自增 `id`、`gmt_create`、`gmt_modified`。只保留扩展数组和结构可变的
  审批协议消息作为 JSON；密钥不入这些表。
- Run CAS 与 Task CAS 分别校验版本；共享 Task 的输入快照发生变化后，其他 run
  持有的旧 Task 版本不能覆盖新回执。仓储返回已提交的完整投影和 Task 版本。
  已绑定的 run 不能更换或解除 task，不能跨 Bot/Session/目标作用域复用远端身份。
- 普通新消息默认新 Task；已有 exec 审批仍沿原 run 续接。队列串行准入继续生效，
  本次不添加文字澄清入口、多实例 owner 或并行 Task 驱动。发现 task 还有其他活动 run 时，
  run 级 abort 拒绝调用 CancelTask，避免扩大取消范围；作用域取消仍要求唯一活动 run。
- context 仅在首次 task 绑定时尝试建立/变更；同 context 的写入跳过，普通状态更新不写
  context 表。文本 delta 不更新这些关联表。
- 首次发布合并为 MySQL/SQLite 039，直接创建规范化关联表；不创建旧实验表再删除。
  不修改上游已提交迁移，不转换早期本地 JSON 数据。旧草稿测试库需重建，保留数据另行设计迁移。

发布前合并上游之前的存储阶段历史验证：相关 4 包 433 项回归、SQLite 全链迁移专项 43 项、真实 MySQL
全链及共享仓储契约 1 项通过；默认回归 6 项外部 DB 测试跳过，其中 A2A MySQL
已另行通过，其余 5 项未跑。R10、源文件大小和空白检查通过。未重跑完整工作区、
真实 Harness E2E 或容量压测，未升级当前运行实例。
记录见 `docs/specs/a2a/task-bindings-validation.json`。

### 2026-10-31 三字段与 inline Card 阶段历史验证（合并上游之前）

实现及契约见 `../a2a/minimal-card-registration-v1.md`，命令、范围、源码哈希和
历史失败修正见 `../a2a/minimal-card-validation.json`。本轮验收已完成：

- Card 管理应用 21 项、版本化注册 11 项、HTTP Card 路由 6 项、接口/声明/发现专项 19 项通过。
- 仓储与 legacy 注册专项 16 项通过，包含一次查询的最大批次和失败路径断言。
- SQLite 完整迁移 43 项通过；真实 MySQL 8.4 的完整 001–038 链、三字段结构、
  UTF-8 接口投影、inline NULL 来源及 Card/Task 契约通过。
- 应用/仓储整包回归 361 项、A2A 适配器整包回归 69 项通过。命令之间有覆盖重叠，
  不相加作为独立测试总数。默认跳过 6 项外部 DB 测试，其中 A2A MySQL 已单独通过，其他 5 项未运行。
- R10、Port 纯度、禁用符号、YAML 语法、文件大小和空白检查通过。导入规则检查
  与仓库 HEAD 的既有失败输出完全一致，无新增违规；不宣称全工作区 CI 全绿。

未重跑全工作区或真实 Harness E2E，未部署本轮到当前联调服务。隔离 MySQL
测试实例已关闭。历史阶段证据不能替代本轮结果；临时 review 文档仍在仓库之外。
