# Plan: 任务 Singlebox 集成链路同构化

## Approach

生产实现是唯一行为契约基线。任务核心以既有 `OpenApiBotPort`、`BcsClientPort` 和 `BcsTokenProvider` 为唯一对外 Plugin API，不读取 profile、URL、token 或 `SINGLEBOX_*` 开关。Avernet 是包含 Task Core、Port contract 与默认装配规则的真内核；OCB 仅提供依赖公司中间件的 corp plugin、credential provider 和配置覆盖，`ocb-public` 只镜像 Avernet，不能直接修改。

singlebox 调用 singlebox 部署的应用服务，生产调用生产部署的应用服务，绝不跨环境。若 singlebox 引擎暂不具备生产契约的能力，则仅在 TaskModule 对应 Port 的 binding 注入 production-contract mock。mock 模拟生产端的授权、run、错误和幂等语义，却不能改任务图、跳过 `TaskExecutor`/poller/callback ingress，或替换 Engine/BCS/Backend 的全局服务。引擎能力补齐后，只变更 singlebox 的 plugin binding，不改任务核心、执行流程或用例主路径。

### Non-negotiable deployment invariant

singlebox 通过不是“另一套实现也能跑”，而是生产任务实现的同构验证。生产发布允许的 diff 被限定为部署配置与外部 binding：endpoint、credential reference、tenant reference、selected plugin implementation。以下任何生产前后差异均违反设计：Task Core 改动、任务状态机或策略改动、401/403 特判、grant/revoke 调用顺序调整、prompt/metadata 变换、run 轮询/取消逻辑调整、callback 分支调整，或环境专用 fallback。

生产发现任务业务或权限问题时，修复只能落在 production contract、integration plugin、singlebox capability/config/fixture 或覆盖测试；不能以 production-only task branch 救火。

```text
Avernet Task Core / Engine / Runner
  └─ OpenApiBotPort / BcsClientPort / callback contracts
       ├─ open-source singlebox config → singlebox application plugin
       ├─ open-source deployment config → deployed application plugin
       ├─ OCB corp-singlebox config → corp singlebox plugin
       └─ OCB corp-production config → corp production plugin

       capability missing in singlebox → task-scoped production-contract mock
```

## 任务功能、依赖场景与差异清单

下面按任务实际功能组织，而非按组件组织。生产链路是每一行的行为基线；“接口已实现”只表示可以收发请求，不等于其调用者身份、授权、异步收敛和错误语义已经与生产一致。

| 任务功能 / 触发场景 | Task 调用的外部接口（owner） | Singlebox 当前行为 | 生产基线行为 | 差异造成的影响 | 需要改造及归属 |
| --- | --- | --- | --- | --- | --- |
| **单 Bot 执行**：任务节点把指令投递给目标 Bot，并轮询结果、超时或取消。 | `OpenApiBotPort.ensure_grant/send_message/get_run/cancel_run`（BaaS OpenAPI / Engine 应用服务） | `TaskModule._resolve_ports()` 创建 `SingleboxEngineAdapter`；用 `x-user-id` 查询 binding/device，再直连每个 Bot 的 WebSocket。run ID 和状态在 adapter 内存中维护。 | `OpenApiBotAdapter` 调 BaaS `/openapi/v1/messages`，以 task 服务的 Bearer API-key 调用；服务端维护 run 生命周期。 | 本地没有走生产入口、服务身份、Bot scope、allow-list、标准错误码或持久 run；本地通过不代表上线不会 401/403、无法查 run 或取消失败。 | **Task**：移除 `TaskModule` 的 profile/URL/直连 WebSocket 选择，只依赖 Port。**Engine/BaaS**：Singlebox 提供同一服务端 OpenAPI 契约；未具备前，以 Task 绑定的 `OpenApiBotPort` production-contract mock 暂代。 |
| **单 Bot 授权**：任务首次使用某个目标 Bot，或恢复/重试前确认授权。 | `OpenApiBotPort.ensure_grant/grant/revoke`（BaaS OpenAPI / Bot 管理面） | `ensure_grant` 只解析本地 target；`grant/revoke` 未实现。 | 校验 task service、owner、tenant、目标 Bot 与 allow-list；必要时经管理面授予/回收；权限失败稳定映射 401/403。 | 这是历史上生产部署最主要的差异：本地绕过的授权前置在生产才暴露，且 Task 可能被迫加生产特判。 | **Engine/BaaS**：补齐 Singlebox gateway、API-key/scope、allow-list 与授权错误契约。**Task（临时）**：mock 必须返回生产一致的 grant、过期和 401/403 语义；不允许 Task 流程按环境跳过授权。 |
| **协作群建群**：任务创建协作群，指定 driver Bot 与成员。 | `BcsClientPort.create_group`（BCS / BCN） | `SingleboxBcsAdapter` 调真实 local BCS；local 默认 `require_authentication=false`，HMAC 被忽略。E2E 会先 onboard，且把成员设为 `public` 绕过 protected-Bot 关系检查。 | `BcsHttpAdapter` 使用 HMAC，并带 driver Bot bearer / caller 身份；BCN 校验 onboard、owner、成员可达性与关系。 | **`create_group` 方法本身已实现，差异不在“能不能建群”，而在谁有权建、成员是否允许加入以及失败是否可被测到。**本地公共可见 fixture 会掩盖生产建群拒绝。 | **BCS/BCN**：Singlebox 支持或可配置启用生产等价 HMAC、caller/driver bearer、onboard 与 reachability 校验。**Task**：继续调用真实 `create_group`，删除 public 可见性绕过；补充无 HMAC、错误 bearer、未 onboard、不可达成员的负向用例。无需把整个 BCS mock 掉。 |
| **协作会话创建**：群建立后创建协作 session，并携带 bootstrap prompt / 上下文。 | `BcsClientPort.create_session`（BCS） | 调 local BCS；接口协议可用，认证通常不强制；本地 fixture 与错误响应可被 adapter 兼容处理。 | 同一 BCS 应用接口，但请求带调用者凭据，并受群成员、driver、owner 与 session 访问控制约束。 | **`create_session` 也已实现。**风险不是缺少 endpoint，而是本地没有证明 caller 是否有权在该 group 建 session，以及错误签名/错误身份能否被拒绝。 | **BCS**：提供强制鉴权和生产错误语义的 Singlebox 配置。**Task**：统一使用 `BcsHttpAdapter` 契约、移除仅为本地响应形状存在的分支（经契约测试确认后），补充创建者/成员/非成员的授权测试。 |
| **协作任务驱动与群消息投递**：Task 启动状态机，由 BCS 把工作分派/消息递送给群内 Bot，再查询 run 收敛。 | `BcsClientPort.start_state_machine_run/get_state_machine_run`（BCS → BCN → Engine） | 可调真实 BCS API；但 E2E 可设 `SINGLEBOX_BCS_DOUBLE=1`，直接给确定性完成结果。真实 local 运行还可能缺少 BCS→Engine 的 gateway 身份或成员投递能力。 | BCS 以经认证的 caller/driver 身份启动，实际触发成员 Bot，返回可查询 lifecycle 与失败原因。 | double 绕过了真正的 BCS→Engine 执行、投递、鉴权与异步收敛；只验证 Task 图，不验证协作任务真的执行。 | **Task**：live parity E2E 禁止 double。若仅 BCS→Engine 投递能力暂缺，注入的是 *task-scoped 的 BCS 执行子能力 mock/proxy*，仅替代 `start/get_state_machine_run` 的外部结果，仍让 Task poller、状态机和 callback ingress 正常运行；`create_group/create_session` 保持真实调用。**BCS/Engine**：补齐服务间身份、成员投递、run lifecycle 与错误回传。 |
| **BCS 回调收敛**：BCS/Engine 完成或失败时回投 Task，驱动任务状态变化。 | `BcsClientPort.task_callback_url`、Task callback ingress（BCS / Gateway） | callback auth 是 `NoopCallbackAuthenticator`，地址可回退 `localhost:8888`。 | 环境 callback URL 可达；HMAC/Principal 验签、owner/tenant、event ID 与重放语义均受校验。 | 任意调用方可伪造完成事件；网络、签名、过期、重放与 owner 问题在生产才发现。 | **Task**：callback URL 与验证器由组合根配置，禁止 localhost fallback；实现/复用 event-id 幂等。**BCS/Gateway**：提供签名、短期身份、回调可达性与错误码契约。 |
| **任务发现后的用户确认会话**：为发现的任务给用户创建可打开的 Bot session。 | `SessionCreator.create_session`（Backend connection API + Engine session API；注意：不是 `BcsClientPort.create_session`） | `HttpSessionCreator` 通过 `x-user-id` 查 binding/device connection，再直接 `POST http://{target}/api/sessions`；含 localhost backend/frontend 默认值。 | 应通过当前环境部署的 Backend/Engine 应用服务，由服务身份/用户授权创建 session，并返回稳定的 session ID/URL。 | 与单 Bot 执行同类的私有寻址与身份绕过，可能跨环境或因 device/端口差异在生产失效。 | **Task**：为 `SessionCreator` 建立环境无关 Port，移除 binding lookup、直连 target、硬编码 URL。**Backend/Engine**：提供 BaaS-compatible session create/query 应用 API；未支持时只给该 Task Port 注入 task-only mock，不改变发现/确认业务流。 |
| **Bot 入网、可见性与 BBS 唤醒**：为协作 E2E 准备成员与启动消息。 | Backend bot onboarding/visibility、BCS OpenAPI、Gateway Principal（Backend / BCN / Gateway） | fixture 主动 onboard、`visibility=public`，并用本地签名 key 自铸 Principal JWT。 | 由受管身份发行方签发短期凭据，并按照 owner、关系、可见性、scope 完成 onboarding 和唤醒。 | 测试夹具把真实前置条件改成“总可达”，覆盖不了生产权限失败。 | **非任务依赖 owner**：提供测试 tenant 的 credential broker 和生产规则兼容的 onboarding/reachability。**Task 测试**：最小权限 fixture，禁止 public-bypass 或本地私钥；将这些准备动作作为前置证据，不进入 Task 业务逻辑。 |

### 已实现接口与真正能力缺口的边界

`create_group`、`BcsClientPort.create_session` 的 HTTP/API 实现不是当前首要缺口，默认应连 real Singlebox BCS；它们需要补的是生产同构的身份与访问控制。暂时需要 Engine mock 的核心是单 Bot OpenAPI 执行能力；只有当已经建群/建会话后，BCS 无法以受认证身份把状态机工作投递到 Engine 时，才对 Task 的 **BCS 执行子链路** 做局部替身。该替身不能取代群、会话、BCN 或全局 BCS。

### 全量 Task 对外依赖接口清单

本清单覆盖 Task Core、Task Runner、Task Discovery 和 Task Center 在运行时向其他 bounded context、应用服务或公司中间件发起的依赖；任务图、任务节点、callback 记录等 repository 虽属 Task 自己的数据边界，也列入以避免把持久化/回查差异留到部署时才发现。每个接口都必须拥有下列之一：已验证的同构实现、显式的 task-scoped production-contract mock，或一个在启动前阻止 live-parity 的 capability 缺口。没有列为“当前已发现差异”，不等于可以不测。

| 依赖接口 / Port | 被哪个任务功能、何时调用 | owner / 边界 | Singlebox vs 生产当前差异 | 影响 | 改造及守卫要求 |
| --- | --- | --- | --- | --- | --- |
| `OpenApiBotPort.ensure_grant/send_message/get_run/cancel_run` | 计划拆解、单 Bot 节点执行、BBS 胜出 Bot 唤醒；投递后轮询、超时或取消。 | BaaS OpenAPI / Engine 应用服务 | Singlebox 为 `SingleboxEngineAdapter`：查 binding/device 后 WebSocket；生产为 `OpenApiBotAdapter`：服务 API-key 调 BaaS。 | 授权、run ID、取消、错误分类与 metadata 不同。 | 单一 Port + 环境 binding；Singlebox Gateway 未具备前只 mock 此 Port；必须覆盖 401/403、过期、scope、幂等、cancel。 |
| `OpenApiBotPort.grant/revoke`（经 `TaskClaimGrantService`） | 用户开启/关闭“任务认领”时，前端请求 Task 服务管理候选 Bot 授权。 | BaaS Bot 管理面 | Singlebox adapter 未实现；生产透传人类 Cookie/Referer 到管理面，并由服务端保管 API-key。 | 本地无法验证用户身份、人类会话与 grant/revoke 权限，生产可能认领失败。 | BaaS 提供 Singlebox 同构管理 API/身份；Task 只复用 Port，禁止 Singlebox no-op。负向覆盖非 owner、过期人类会话、错误 Bot scope。 |
| `BcsClientPort.create_group/get_group` | 协作/manager-worker 节点选定成员后建群与读取群状态。 | BCS / BCN | endpoint 已有；Singlebox 常不强制 HMAC，fixture 将 Bot public 化；生产校验 HMAC、driver bearer、owner/onboard/reachability。 | 建群“成功”不代表成员关系和调用方合法。 | real Singlebox BCS，启用生产等价身份/关系能力；Task 去除 public-bypass；失败覆盖签名、caller、未入网和不可达成员。 |
| `BcsClientPort.create_session/get_session_messages` | 群建立后创建协作会话、带 bootstrap prompt，并读取协作消息。 | BCS | endpoint 已有；Singlebox 认证弱、可能有本地响应兼容；生产有成员/driver/session ACL。 | 会话创建或读取的权限、消息可见性差异会在生产暴露。 | 统一 `BcsHttpAdapter` 契约，删除仅本地响应兼容分支（由契约测试证明）；测试创建者、成员、非成员和错误凭据。 |
| `BcsClientPort.start_state_machine_run/get_state_machine_run/validate_definition` | 协作任务真正启动、轮询协作 run；创建前校验协作定义。 | BCS → BCN → Engine | local 可被 `SINGLEBOX_BCS_DOUBLE` 替代，真实 local 可能缺 BCS→Engine 受认证投递；生产为实际异步执行。 | double 跳过了群消息、成员执行、run 收敛和错误回传。 | parity gate 禁用 double；仅缺投递时 mock 这组 execution 子能力而非整个 BCS，且不绕过 Task poller/callback。BCS/Engine 补齐服务间身份、lifecycle 和错误码。 |
| `BcsTokenProvider`（BCS base URL、HMAC key、callback URL） | 所有 BCS REST 调用和 Task 回调 URL 组装。 | 配置/密钥管理 / BCS | local 默认 localhost、空 HMAC、callback 可回退；生产为受管 endpoint 和 HMAC。 | 写死或回退会跨环境调用，callback 不可达或鉴权失效。 | 移出 TaskModule/Task Core，受 schema 校验的 deployment config 注入；启动校验 endpoint scope、credential ref 与 callback origin。 |
| `BcsBotTokenProvider` | manager-worker 含 event subscriptions 等需 driver Bot bearer 的 BCS 请求。 | BCS Bot credential store / corp extension | Singlebox 为 `NullBcsBotTokenProvider`；生产读取 `bcs_bots.session_token`。 | 依赖 Bot bearer 的能力本地被弱化/不覆盖，生产可能拒绝。 | BCS/credential owner 提供短期测试 bearer；Task 不以 null/环境分支跳过能力。将 bearer presence 和错误 token 作为 parity 前置。 |
| Task callback ingress / `CallbackAuthenticator` / `CallbackDataEnricher` | BCS/Engine 回推节点或 run 结果，Task 落库并推进任务图；必要时回查 run 补全数据。 | BCS / Gateway / Backend application API | Singlebox Noop 鉴权、localhost fallback；enricher 可能直调配置 base URL；生产为签名/Principal、可达 callback、受控回查。 | 可伪造事件；重放、过期、owner/tenant 和网络差异只在生产暴露。 | 配置注入 verifier、callback URL、回查 client；禁止 Noop/localhost 进入 live parity；验证签名、event-id 幂等、重放、过期、错误 tenant。 |
| `BcnService.list_bots_by_task_modes` | BBS 搜索候选、任务认领名单交集、协作候选预筛。 | BCN / Bot Management | 两端复用该服务接口的设计已存在；但 Singlebox fixture 通过 public visibility/onboard 改变候选可见范围，生产受 provider identity 与关系控制。 | 候选集合不同，可能导致本地命中、生产无可派发 Bot，或反之。 | BCS/BCN 提供测试 provider identity 与真实可见性；Task 记录候选来源/过滤原因，E2E 验证无权限、无候选、仅 claim_on 候选。 |
| `BotDiscoverServiceProtocol` + `OpenApiBotPort` search-skill 调用 | Task 搜索/派发策略需要语义候选时。 | Bot Discovery / Bot Runtime | 当前需逐实现核对：Task 已通过 Port 注入，但 Singlebox 是否具备相同 search-skill、skill readiness 与结果排序尚未证明。 | 本地候选排序或技能缺失会改变派发策略，且很容易被“没有候选”的 stub 掩盖。 | 建立 search request/result/version 契约；Singlebox Engine 未具备时 task-only discovery mock 标记 `simulated`，不得用于 parity gate；增加 skill readiness 预检。 |
| `SessionInitiator` / `SessionCreator` / `OpenApiBotSessionInitiator` | 任务发现后，为用户创建/唤醒确认 Bot 会话并生成可访问 session link。 | Backend connection API / Engine session API / BaaS | `HttpSessionCreator`、部分 initiator 会查 binding/device 后直连 `target`，带 `x-user-id` 和 localhost 默认；生产可走 OpenAPI adapter，但两条实现仍并存。 | 这是除 runner 外另一条 Singlebox 特化直连链路；device 端口、服务身份、session URL 在生产不等价。 | 收敛为 session 应用 Port；Singlebox/生产各调本环境服务；移除 direct target、URL fallback。尚缺服务端 session API 时只 mock session Port，发现/通知流程保持不变。 |
| `FrontendUrlProvider` | 创建确认会话后拼接用户打开的前端链接；通知卡片中携带该链接。 | Frontend deployment/config | Singlebox 有 `localhost:8000` 构造兜底；corp provider 按环境配置/holder 取值。 | 链接可能指向错误环境，构成跨环境体验和安全问题。 | 部署配置以 frontend scope 注入并预检；live 不允许 localhost/空 provider fallback；链接的 host/scheme 纳入 E2E evidence。 |
| `NotifyMessagesProvider` / `NotifySenderPlugin` | 发现任务、任务状态变化后给用户发送卡片/通知。 | 企业 IM / notification middleware（OCB corp extension） | Singlebox/test 是 `NullNotifyMessagesProvider`；生产为 DingTalk 等公司通道和凭据。 | 功能流虽通过，但实际通知、收件人授权、卡片链接和失败重试未验证。 | 可选通知不阻断核心任务，但必须显式 capability；提供 task-scoped sandbox notification plugin 或受管测试通道。生产通知需要收件人/凭据/失败可观测测试。 |
| `StaffDeptPlugin` | 任务展示、归属或人员/部门解析（TaskService 注入）。 | 组织架构 / corp middleware | Singlebox 可能未绑定，生产由 corp plugin 提供；需按实际调用路径核验。 | 人员/部门过滤、展示或授权辅助信息可能在生产改变任务结果。 | 把缺失定义为明确 capability（不是 silent `None`）；若影响派发/授权则为 critical，需 contract fixture；若仅展示则降级策略需文档化。 |
| `bot_service` / `BotBinding` / `BcsBotIdentityResolver` | 解析 publisher Bot 名称、owner+bot 映射、driver Bot BCS identity。 | Bot Management / Backend metadata | Singlebox 的 binding/device 本地模型与生产注册/身份数据不同；部分调用为 optional/null fallback。 | 可能建错 driver、无法解析展示、或把元数据缺失误当作无候选。 | 抽取最小 metadata/identity Port，要求 owner/bot/BCS identity 一致；启动检查必需 binding，错误 mapping 必须 fail-fast，不可用本地默认用户替代。 |
| `TaskAuthGate` | 任务认领、派发时的权限门禁与 claim-on 名单交集。 | IAM / task authorization / corp extension | Task 已注入 gate，但 Singlebox 是否装配生产同构 gate、与 BaaS grant 是否一致仍待核验。 | 本地可认领/派发、生产拒绝，或产生越权派发。 | 定义 TaskAuthGate 与 OpenAPI/BCN 身份的一致性契约；Singlebox 提供受控测试 issuer 或 task-only mock；同一正负用例验证三者。 |
| Task repositories：`task_info_repo`、`task_node_repo`、`task_node_run_info_repo`、`callback_repo` | 创建任务、节点/runs 状态、callback event 记录、恢复/重试。 | Task data store / persistence | 纯内核可 `None` 跳过落库；部署环境数据源、事务、唯一键和 callback 幂等能力需验证。 | Singlebox 看似完成但无法恢复/去重；生产重试或回调重放造成状态错乱。 | live Singlebox 禁止 repository null fallback；统一 schema/migration/transaction 语义，验证恢复、重复 callback、并发回调和失败写入。 |
| `WorkOrderService`（发现/通知旁路） | 发现任务后创建/发送工单类通知。 | Work-order service / corp middleware | 当前由 discovery service 编排，Singlebox 是否接入同构实现待核验。 | 通知/确认闭环在生产与 Singlebox 不一致。 | 明确它是核心确认闭环还是可选旁路；核心则提供 sandbox plugin 与契约测试，可选则 E2E evidence 标注 disabled，不得静默消失。 |

#### 依赖分级与“是否允许 mock”的统一规则

| 等级 | 上述接口 | Singlebox functional E2E | Singlebox parity / 发布门禁 |
| --- | --- | --- | --- |
| **Critical execution** | `OpenApiBotPort` 执行、`BcsClientPort` 群/会话/run、BCS token、callback、BCN 候选/可达性、Task repository | 可 task-scoped mock，但报告 `simulated`、只证明 Task 主流程。 | 不允许 mock；必须 `auth_mode=enforced`。若引擎尚缺能力，则不能宣称生产同构。 |
| **Critical authorization** | OpenAPI grant/revoke、BcsBotToken、TaskAuthGate、Bot identity/binding | 可用生产契约 mock 覆盖正负语义。 | 不允许绕过或 `Null`；必须验证一致的 caller、scope、owner、tenant。 |
| **User journey support** | 确认 session、FrontendUrl、Notify、WorkOrder、StaffDept | 可由显式 sandbox plugin 或 disabled capability 运行。 | 若属于发布承诺的用户闭环，必须 real/sandbox 受管实现并有证据；若不承诺，报告 disabled，不能伪装成功。 |

### Confirmed Engine capability gap matrix

生产 Task 的单 Bot integration contract 是评估 singlebox Engine 的基线。当前 singlebox adapter 已确认或需引擎团队补齐的差异如下；不属于 Engine 的 BCS/BCN/callback 能力不在本表中。

| Production-contract capability | 当前 singlebox 证据 | 临时 task-only plugin | 引擎补齐后的替换条件 |
| --- | --- | --- | --- |
| 服务端 OpenAPI Gateway | task adapter 自行查询 binding 并直连 WebSocket。 | `TaskMockOpenApiBotPort` | singlebox Engine 应用服务提供稳定服务端 API。 |
| 服务身份、Bot scope 与授权名单 | `x-user-id` 本地身份；grant/revoke 未实现。 | mock 返回生产一致的 allow-list、401/403、scope 与过期语义。 | Gateway 校验 task service、owner、target Bot、tenant 与凭据。 |
| Durable run lifecycle | adapter 内存维护 `_runs` 并生成 `ws_*` ID。 | mock 返回标准 `run_id` 与 RUNNING/terminal 状态。 | 服务端持久化 start/query lifecycle。 |
| Real cancel | 当前仅取消本地 WebSocket collector。 | mock 实现幂等 cancel 状态机。 | 服务端 cancel/abort 可查询终态。 |
| Idempotency and correlation | metadata 未完整传至 Engine，未见服务端去重。 | mock 校验 task/node/request/idempotency key。 | Gateway 支持关联字段和重复启动语义。 |
| Structured task result and errors | adapter 从 `chat.final` 拼接文本和字符串错误。 | mock 返回标准状态、内容、错误码、可重试性。 | Gateway 返回稳定结果及 timeout/auth/rate-limit/engine-error 分类。 |
| Skill readiness and capability discovery | skill active 状态可能因 local device-sync no-op 不可确认。 | mock capability report 明确 `simulated`。 | Engine 提供 skills readiness/version 与 `/capabilities` 预检。 |

## Non-task dependency requirements

| 依赖 owner / 边界 | 需要提供的能力 | 验收标准 |
| --- | --- | --- |
| BaaS OpenAPI / Bot Runtime | 可隔离的兼容 deployment：服务 API-key、allow-list grant/revoke、消息创建、状态查询、取消。 | 同一 `OpenApiBotAdapter` 跑 grant→send→poll→cancel；未 grant、错误 scope、过期 key 有稳定 401/403。 |
| Backend / Engine | 本地 Bot/Engine 生命周期接入 BaaS-compatible API；device connection/WebSocket 仅为 provider 内部实现。 | task runtime 与 live E2E 不再调用 connection endpoint 或私有 WebSocket 协议。 |
| BCS / BCN | 可开启的 HMAC、driver Bot bearer、Bot owner/caller、onboard、reachability 和 catalog；生产 schema contract。 | 同一 `BcsHttpAdapter` 完成群、session、状态机；错误 HMAC/token、未 onboard、不可达成员可断言拒绝。 |
| Gateway Principal | 测试 issuer/credential broker，按生产 verifier 规则签发短期 principal。 | 正确 owner 成功；错误 issuer/audience、过期 token、非 owner 返回 401/403；测试不保存长期 key。 |
| Callback | 可达 callback URL、签名、event ID/replay contract。 | 错签名、过期、重放被拒绝或幂等；合法 callback 收敛任务。 |
| Identity / Config | 临时凭据引用、统一 schema、启动 preflight。 | task core 不读 env/选实现；CI 输出脱敏 adapter、endpoint、identity mode 和证据。 |

## Target topology

```text
Task core
  ├─ OpenApiBotPort → OpenApiBotAdapter → BaaS-compatible endpoint
  ├─ BcsClientPort  → BcsHttpAdapter   → authenticated BCS endpoint
  └─ callback ingress → HMAC authenticator → BCS event delivery

Singlebox / pre-production / production
  └─ task-core contracts and execution flow identical
  └─ only plugin binding, same-environment endpoint, credential reference,
     test tenant and principal issuer differ
```

### Composition and configuration rules

```python
# Avernet task core — no profile or URL branching
bot = injector.get(OpenApiBotPort)
bcs = injector.get(BcsClientPort)
```

```yaml
# singlebox: only same-environment endpoints
task_integration:
  bot_execution_plugin: task_production_contract_mock # temporary
  collaboration_plugin: singlebox_bcs_application
  endpoint_scope: singlebox
```

```yaml
# after Engine capability is ready
task_integration:
  bot_execution_plugin: singlebox_engine_application
  collaboration_plugin: singlebox_bcs_application
  endpoint_scope: singlebox
```

Configuration bootstrap validates that every task endpoint resolves to the selected deployment scope. The task module must not retain `http://localhost:8888`, `localhost:21000`, direct WebSocket path, or production domain fallback values. A real endpoint that belongs to another scope fails startup/preflight.

### Verification tiers and release gate

| Tier | Dependency implementation | What it proves | Production readiness |
| --- | --- | --- | --- |
| Task unit/contract | deterministic mock allowed | task graph, orchestration and consumer assumptions | no |
| Singlebox functional E2E | same-environment real plugins plus explicitly reported mocks | task flow works against local deployment | conditional |
| Singlebox parity E2E | all critical plugins real and `auth_mode=enforced` | same task code has production-equivalent integration/auth behavior | yes, subject to deployment config review |
| Managed pre-production smoke | deployed non-production services and production-equivalent credentials | network, issuer, tenancy and deployment wiring | release gate |

Every E2E report emits the selected plugin, endpoint scope, `auth_mode` and redacted correlation IDs. A dependency in `simulated` mode makes the run useful for task-flow regression but cannot be reported as production parity. Production release requires all critical dependencies to be `enforced` and negative authorization cases to pass: unauthorized caller, wrong scope, expired credential, invalid signature and unreachable target Bot.

## Rollout

1. 固化生产 Port contract、授权失败与 run lifecycle 的特征测试。
2. 将 `TaskModule._resolve_ports()` 的 profile/URL/double 分支迁出任务核心，由 composition root 绑定 Port。
3. 增加 task integration capability/config preflight 和 endpoint-scope 校验。
4. 对 Engine 暂缺能力仅注入 task-scoped production-contract mock；live 报告该能力为 simulated。
5. 引擎逐项补齐 Gateway、auth、durable run、cancel、metadata/idempotency、capability discovery 后，将 binding 切换为 singlebox Engine application plugin。
6. 对 BCS/BCN/callback 依赖重复上述方式，但不得归入 Engine 交付范围；最终退役 double 和所有任务 runtime 特化 adapter。

## Test Strategy

- 端口契约：OpenAPI grant/send/poll/cancel，BCS HMAC/caller/group/run，以及无秘密的错误分类。
- live E2E：拒绝 double，建立最小 grant/reachability，验证三种模式和 callback 签名。
- 受管预发 smoke：验证真实测试租户、identity issuer 和回调可达性。
