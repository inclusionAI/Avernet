# Group as an Agent：BCS 群 A2A 入站与执行规范

## Record

- 状态：**proposed / 待书面评审**。核心范围和生命周期已经 brainstorming 确认；具体存储、凭据生命周期和参数是本稿的设计建议，不表示已实现或已部署。
- 日期：2026-11-16。所属模块：公共 BCS；部署方负责凭据保管、公开地址与网关配置。
- 分支：`docs/bcs-group-as-agent-spec`，基于 `origin/dev` 的 `3b1b3859b32b3acf20888b9ad20ba6d32a047b26`。
- 调研来源：PR [#3979](https://github.com/inclusionAI/Avernet/pull/3979)，引用版本 `387cd5c32a4d7c391a298ce6dfc3164cf3c764ab`；本稿不是对其代码的 cherry-pick。
- 本稿收敛并替代该 PR 中本功能的待定设计方向；原材料保留为历史调研，不改写其结论和验证记录。
- 协议基线：A2A **1.0.1**，官方源提交 `3303592588e388e62e0f69f701af531d2f4e3991`；线上 `A2A-Version` 与 `supportedInterfaces[].protocolVersion` 使用 `1.0`。
- 阅读顺序：本规范 → [请求响应示例](examples.md) → [证据与差异](research.md) → [实施计划](plan.md)。实施计划由后续 writing-plans 工作补充，尚未执行。
- 约束：[架构规则](../../../../docs/arch/arch.rules.md)、[CI 约束](../../../../docs/arch/ci.enforce.md)、[BCS 分层](../../CLAUDE.md)、[BCS 工作规则](../../AGENTS.md)、[知识规范](../../docs/agents/knowledge-standard.md)、[决策索引](../../docs/adr/README.md)。本稿不覆盖已接受的 ADR。
- 当前拥有者合同：[Group 目录发布](../../docs/bcsfuse-catalog.md)、[HTTP 合同](../../api-contracts/README.md)、[Service API](../../crates/service-api/bcs-service-api/CONTEXT.md)。本稿描述它们拟进行的变化，而非宣称当前合同已经改变。
- 文档位置：`apps/bcs/specs/2026-11-16-group-as-agent/`，由 BCS spec 索引链接到这里。

## 1. 问题、目标与边界

BCS 已具备主从群和状态机群执行能力，但 Session 的可变输入输出、内部 Run 状态和聊天消息不能直接充当标准 A2A Task 合同。外部调用需要稳定的受理、每轮结果、取消确认、重试和故障恢复语义。

目标是让 **Agent 或应用系统**像调用一个 Agent 一样调用已经存在的 Group；调用方无需提交内部 manager、成员、Provider 或流程定义，也无需注册成 BCS Bot。

### 1.1 已确认的产品决策

| 决策 | 首期合同 |
| --- | --- |
| 认证 | A2A 专用、可配置的认证插件链；首期只交付 BCS 签发 Token 的 Bearer 认证，不支持无鉴权 |
| Group 调用权限 | 不配置调用方到 Group 的白名单或 Group-scoped token；认证成功即可调用满足开放条件的群 |
| 开放条件 | `visibility=public` **且** `supportA2A=true`，并通过群类型、无 Human 成员等资格校验 |
| AgentCard | 只有已开启且合格的群才对外发布和向目录注册；public 本身不代表开放 A2A |
| 执行对象 | 主从群 ManagerWorker、自定义协作群 StateMachine |
| 对外模型 | Group 是 Agent；Session 是 context；每轮独立执行对应一个新 Task |
| 同 context 并发 | 忙碌拒绝，不排队；相同已受理请求的重试优先走去重 |
| 完成 | manager 显式完成／Run 明确输出，加上执行退出和收尾确认；最终 Artifact 与成功终态原子提交 |
| 取消与恢复 | 整轮停止确认后释放占用；无法确认时有界失败并隔离，恢复确认后才允许新轮次 |
| 关闭开关 | 不再受理新轮次并撤销 Card；已受理任务继续执行，原调用方仍可查询、订阅和取消 |

### 1.2 首期交付及非目标

交付 HTTP+JSON、SSE、Card、SendMessage、SendStreamingMessage、GetTask、ListTasks、SubscribeToTask、CancelTask；文本／JSON 输入输出；持久化去重、历史、Artifact、恢复与清理。

不交付：无鉴权插件、调用方 Group 授权范围、JSON-RPC/gRPC 入站、单 Bot 标准入站、Push Notification、自由聊天群、自然语言建群、文件上传下载、同 Task 补充输入、同 context FIFO、业务操作回滚、对外部业务副作用的 exactly-once 承诺。

配置化不等于首期实现所有认证方式；不得加入匿名兜底、Cookie 登录兜底或依赖企业私有 SDK 的公共默认实现。现有 `/a2a/agents/**` 的注册／管理是控制面，不因本功能变成任意持有调用 token 者可管理。

### 1.3 方案选择

| 方案 | 判断 |
| --- | --- |
| **新增持久化 A2A 调用层，复用 Session／MessageFlow／Runtime** | 采用。独立保存轮次及结果，通过明确合同补齐底层能力 |
| 直接包装现有 Session REST | 不采用。无法可靠保留多轮结果，也不能把 Session.Completed 等同于 A2A 成功 |
| 新建独立群执行引擎 | 不采用。重复调度、投递和状态机；本期只增加调用生命周期与必要的执行集成 |

## 2. 术语与不变量

| 名称 | 含义与身份 |
| --- | --- |
| Caller | 认证插件解析出的稳定主体；可能代表 Agent、应用系统或其他获准主体，不是请求自报名称 |
| Group / Agent | 可复用的群能力，`group_id` 定位；不是单轮任务 |
| Context | A2A 创建的 `ServiceInvocation` Session；`contextId = Session.id` |
| Task | 独立入站调用轮次；服务端生成不复用的 ID，与内部派发 task_id 区分 |
| Execution generation | Task 绑定的 Session activation、Run、投递关联及持久化版本，用于拒绝旧轮次回调 |
| Message | 当前输入或允许公开的回复；输入 messageId 由调用方提供 |
| Artifact | 某一 Task 的最终交付结果，保序 Parts；不等同于内部工具输出或群聊天记录 |
| Context isolation | 停止结果未知时禁止该 context 新执行的状态；不是把 Task 永久留在 WORKING |

必须保持：

1. 一个 context 同时至多一个活动执行代次；隔离时不得创建下一轮。
2. 同一去重键最多受理一个 Task；并发、响应丢失、进程重启、结果清理均不允许重复受理。
3. 无持久化受理，不派发；数据库事务不跨 Bot、Provider、目录 HTTP 等外部请求。
4. `COMPLETED` 必须有已提交且通过校验的 Artifact；终态不可逆，旧事件不覆盖新代次。
5. 未确认停止不能释放执行权；租约过期、连接断开、无消息不是停止证据。
6. Group 的 public 不使 Task、context、输入和产物公开；所有调用资源按可信 Caller 隔离。
7. 开关关闭、Card 撤销、任务取消、群删除是不同动作，不互相隐含。
8. 认证身份与下游执行身份分离；入站 token 不下发给群成员，也不冒充 Human 授权。

## 3. Group 开放与 AgentCard 生命周期

### 3.1 `supportA2A` 配置合同

在 Group 新增持久化布尔属性：对外 JSON 名固定为 **`supportA2A`**；Rust／SQL 使用 `support_a2a`。创建省略为 false；PATCH 省略不变，null／非布尔值非法；读取始终返回布尔值。历史群迁移默认 false，不按 public 自动回填 true。

通过现有有管理权限的 Group 创建／更新用例设置，沿用原权限。调用 token、public 可见性或 Task 所有权都不授予修改开关的管理权限。新增字段覆盖 V1 detail/summary/create/update、相应原生管理入口和 Service API，不通过可任意写入的 meta 绕过验证；DM 不支持开启。

运行资格定义为：

```text
eligible = group_kind == normal
        && record_status == active
        && visibility == public
        && supportA2A == true
        && strategy in {manager_worker, state_machine}
        && all(group.participants, actor_kind == bot)
        && execution_contract_is_valid
```

`GroupStatus::Completed` 是会话执行语义，不等同于资源删除，不单独导致撤销一个仍有效的服务群。

### 3.2 开启校验

校验基于同一次修改后的完整群快照；同请求把 private 改成 public 并开启，可以在全部条件成立时原子提交。

- Human 判断使用 `Participant.actor_kind`／`is_human()`，不能使用兼容字段 `kind/type`（它目前不能可靠区分 Human）。Human 即使 absent／observer 也阻止开启；人类拥有或管理 Bot 不等于 Human 成员。
- ManagerWorker 必须有可解析、符合群角色合同的 manager，参与执行的 Bot 绑定完整；不要求启用瞬间所有 Bot 在线。
- StateMachine 必须能解析并编译固定版本的流程、完成绑定，且有显式最终结果选择。静态流程及展开的子流程不得含 HumanInput；不依赖本期不支持的等待补充输入。
- 最终结果声明只包含首期支持的文本／JSON，并能生成有效 Card。
- 需要受信 Human 执行凭据但无法由既有执行策略满足的节点不能开放；A2A 调用 token 不能替代该身份链。
- 缺失、损坏、读取依赖失败都不是“条件成立”。校验失败不改变开关，不产生成功注册通知。

### 3.3 持续不变量、并发与执行快照

开启期间，对成员、visibility、strategy、流程绑定、输出声明的变更必须再次验证；使资格失效的变更拒绝，提示先关闭开关。允许原子执行 `supportA2A=false` 与 private 等变更，但不得修改既有活动轮次的执行快照。

校验必须覆盖 REST、WS、邀请、Core upsert、流程配置等真实写路径，不只覆盖一个 PATCH handler。校验和保存共享数据库版本／锁，禁止“检查时无 Human，提交前插入 Human”的竞态。

使用持久化 `a2a_revision` 对影响开放资格和 Card 的变更编号；新请求受理与开关变更必须在数据库中按该版本串行化。只检查缓存、先查后写或依赖目录最终一致性均不足。受理事务先提交者保留任务；关闭事务先提交者阻止新任务。

首次 context 固定执行策略、成员、流程版本与结果选择，保存独立执行配置指纹。已有 Task 使用固定快照；下一轮只在当前群仍合格且执行配置与 context 兼容时受理。执行配置发生变化则返回 `CONTEXT_CONFIGURATION_CHANGED`，要求新建 context，不静默替换上下文。名称、描述和开关关闭再开启本身不改变执行配置指纹。

A2A Session 不能通过普通成员入口添加 Human，或增删已固定的执行成员；本轮内部派发限于固定快照中获准的 Bot。普通聊天、`/services` reactivation、原生 Run rerun 等也不得绕过 A2A 占用、归属及轮次入口。内部可信投递和精确关联的完成回调不是被禁止的旁路。

### 3.4 开关、Card 与历史任务的行为矩阵

| 状态／动作 | 新 Task、下一轮 | Card 发布／目录 | 已受理 Task |
| --- | --- | --- | --- |
| public、开关关 | 拒绝 | 不发布；删除旧目录 Card | 归属校验后可读取／取消 |
| private、任意历史值 | 拒绝 | 不发布；删除旧目录 Card | 不因改 visibility 公开或转移归属 |
| 开关开、全部合格 | 可受理 | 提供 Card；注册／更新目录 | 按正常生命周期 |
| 开关开启校验失败 | 不新增能力 | 不发注册成功通知 | 不改变原轮次 |
| 关闭开关 | 提交起拒绝 | Card GET 不再提供；安排撤销 | 继续运行，可查询、订阅、取消 |
| 同 messageId 重试已受理请求 | 不新增执行；返回原 Task | 不依赖 Card 仍存在 | 沿用原请求合同 |
| 资源删除 | 禁止受理；先关闭 | 撤销 | 先取消和核实停止；禁止直接删除恢复关联 |

群删除及 Session 删除必须先处理活动／隔离执行；未确认停止时拒绝物理删除，不能靠删除释放占用。已完成结果按保留策略保存并继续按原 Caller 授权；必要的群墓碑与所有权字段不依赖活跃 Group 行。法律／运维强制清理不是此标准调用接口。

### 3.5 Card 生成与可靠同步

Group Store 是 Card 模板及群配置的事实来源；HTTP 发布和 Fuse 注册使用相同投影与资格判断。模板可以在开关关闭时存在，但不能作为可调用 Card 对外提供或注册。

Card 必须声明真实 HTTP+JSON 基地址、协议 `1.0`、文本／JSON 能力、`streaming=true`、`pushNotifications=false`、`extendedAgentCard=false`，以及当前认证插件对应的安全声明。manager 输出仅文本；状态机按显式结果声明输出模式。Card 不包含成员身份、内部地址、token 或内部提示。

首次开关默认 false 会改变既有公共群的目录发布规则。实施必须包含一次**显式、可续跑、有界分页的目录核对**，撤销此前已发布但未开启的 Group Card；保留 Bot Agent 的现有发布规则。不能仅等待“未来恰好一次群更新”。

Group 开关／相关配置提交时，在同一事务写入以 group_id 为键的持久化同步意图和 revision，提交后唤醒现有目录同步路径。重启按游标恢复未完成意图；不把现有内存通知队列当作持久化成功。每次尝试重新读取当前快照，关闭后重试 DELETE，而不是重放旧 PUT。

同群出站操作串行，重复 PUT／DELETE 幂等；接收端不支持 revision fencing 时，对超时的旧写保留不确定记录，重读最新状态并再次核对，不能声称远端强一致。目录延迟／故障不回滚开关，也不允许入口绕过本地资格；远端撤销未确认必须可观测和重试。启用 Fuse 不是执行的前置条件，但启用的目录必须遵守此发布合同。

## 4. A2A 认证与资源隔离

### 4.1 插件合同与装配

A2A 数据面使用一套经配置选定的认证链，覆盖 Card GET、send、stream、get、list、subscribe、cancel，并供后续单 Agent 标准入站复用。首期仅内置 `bcs_token`，不是为每条 route 写一份 token 校验。

建议配置合同如下；这是**拟新增配置**，不是当前可直接使用的参数：

```toml
[a2a]
enabled = true

[a2a.auth]
chain = ["bcs_token"]
```

未开启 A2A 时不挂载新入口；开启后缺失／空链、未知插件、无鉴权插件、缺失必要依赖均启动失败。禁止继承现有 debug 下 local mock 的默认链。插件通过 composition root 注册和注入，业务层不读取配置、不选择插件。新策略是独立 A2A profile，不修改既有管理／登录认证链的优先级或成功语义。

复用现有认证插件基础，但需演进其 Human/Bot 导向的主体表达。A2A 认证合同返回：

- 已验证的稳定主体 `{issuer_namespace, subject_id}`，以及可选可信租户作用域、凭据有效期；不以 token 原文或 token hash 充当主体。
- `NotApplicable`：凭据不属于此插件；`Authenticated`：明确成功；`Rejected`：无效／过期／撤销；`Unavailable`：依赖故障。
- 显式拒绝不可回退到另一身份来源，依赖失败不可变成匿名／不存在。全链不匹配返回 401。
- 链的顺序与配置一致，插件名及配置在启动时验证；声明 HTTP 的方式由 adapter 映射，应用／Core 合同只接收无凭据的可信主体。

插件为受信进程内实现，只获得验证需要的存储、时钟和配置；不得读取聊天或修改 Task。验证超时有上限，关闭时释放资源。公共本地模式使用实际签发的测试凭据，不依赖内部服务。

### 4.2 BCS Token 首期落地建议

Bearer 只规定传递方式，不等于 JWT，也不代表任何 BCS 签发的凭据都能使用。首期建议交付独立的**调用方凭据**：服务端生成高熵 opaque token，绑定稳定 client_id；Agent 和应用都可以拥有 client_id，无需注册 Bot。

- 使用至少 256-bit 随机秘密；原文只在签发时展示一次。存储 token 的不可逆 SHA-256 摘要、client_id、用途、有效期和撤销状态，不存原文。
- 由 BCS 管理面签发／列出非秘密元数据／撤销；首期提供管理员 CLI 路径，不新增匿名换取凭据或通用 OAuth 服务。签发权限由现有部署管理边界控制，调用 token 不能给自己签发。
- client_id 与凭据 ID 分离；轮换可短暂保留两个有效 token，均映射同一主体，不转移或丢失旧 Task。删除／禁用 client 后拒绝其后续请求，不回收 client_id 给其他调用方。
- 校验用途、有效期、撤销／禁用状态；每次请求进行有界、精确索引查找。首期不引入跨请求正向缓存，避免无法界定撤销延迟。
- 不默认接受 Bot 注册 token、Provider-admin token、Human 登录 Cookie 或 Session WS token；兼容其他 BCS 凭据必须由同一插件显式声明用途和主体映射并测试，不能按“能解析”就放行。
- token 过期／撤销不自动取消已受理任务；续查需要同一主体的新有效凭据。已建立 SSE 最迟在凭据到期时断开；首期用独立的 15 秒定时器复验撤销状态（持续发送业务事件时也执行，不依赖空闲心跳触发），无法验证即关闭连接，不取消业务任务。

这是通用应用凭据的最小实现建议，需要书面评审确认；不得把现有仅解析 Bot token 的插件描述为已经满足此合同。

### 4.3 资源范围与安全边界

新调用不检查调用方 Group 白名单或成员身份，只检查认证和 §3 的群资格。Task 操作统一校验可信主体、目标 group_id、可信部署作用域；context 还必须是 A2A 创建的 ServiceInvocation。已关闭群的历史任务不要求当前仍可新调用。

Authorization 只接受一个 Bearer 值，scheme 大小写不敏感；重复／冲突凭据、query token、metadata token 均不作为替代来源。

未知、跨群、跨主体 Task 均返回 `TASK_NOT_FOUND`；不合格 context 返回 `CONTEXT_NOT_FOUND`。列表和总数在 SQL 分页之前按主体／群筛选。所有权检查同样覆盖重复请求、结果清理后查询、游标和 SSE 建连；public 群不改变它们。

不从 body.metadata、HTTP 自报 caller/user、IP、A2A tenant 字段推导可信主体。首期 Card 不声明 interface tenant；非空 tenant 在受理前拒绝。内部环境／部署 scope 来自配置与认证，不把现有标签型 env 自动提升为隔离证明。

生产只使用 HTTPS；Authorization、签发返回值、存储摘要都不能进入业务日志、异常正文、Card、指纹、Task 或成员上下文。HTTP 认证失败返回 401 与 Bearer challenge；依赖不可用返回 503。控制面权限不因这条调用链放宽。

## 5. HTTP+JSON 合同

### 5.1 路由及兼容性

基地址：`/api/v1/collaboration/a2a/groups/{group_id}`。

| 方法／相对路径 | 响应 |
| --- | --- |
| `GET /agent-card.json` | 符合资格的 Group AgentCard；同样要求认证 |
| `POST /message:send` | `SendMessageResponse` 的 `task` 分支，HTTP 200 |
| `POST /message:stream` | SSE；首帧为 `StreamResponse.task` |
| `GET /tasks/{task_id}` | Task 快照 |
| `GET /tasks` | 标准 ListTasksResponse |
| `POST /tasks/{task_id}:subscribe` | SSE；首帧为当前 Task |
| `POST /tasks/{task_id}:cancel` | 当前／更新后的 Task，不承诺响应时已停止 |

已知但未支持的 `/extendedAgentCard` 和 Task Push Notification 配置操作在认证后分别返回 UNSUPPORTED_OPERATION／PUSH_NOTIFICATION_NOT_SUPPORTED，不将未知路径的 404 误当能力合同；不挂载对应业务实现。

新路由归 `bcs-api-http`，不是把版本化路由实现复制到 legacy `bcs-http`。A2A 采用标准裸响应与错误，不套 V1 的 `code/message/data/request_id` envelope；OpenAPI validator 为明确的 A2A 路由增加窄范围协议例外，不全局放宽规则。既有 Agent 注册和 Card 管理路由、其 Provider-admin 权限和 envelope 不变。

请求与响应首选 `application/a2a+json`；JSON 请求兼容 `application/json`；SSE 使用 `text/event-stream`。客户端发送 `A2A-Version: 1.0`，省略版本按标准兼容规则视为 0.3，本期不支持并返回版本错误；Card 发现 GET 不强制已有版本头。业务响应及私有结果使用 `Cache-Control: no-store`。

动态动作路由遵守当前 router 能力：可将 POST 注册为 `/tasks/{task_action}`，只解析末尾 `:cancel`／`:subscribe`，不重复 URL 解码。未知后缀 404；空／非法 ID 或 body.id 与路径冲突 400。路径提供 ID 时允许 body 省略 id；不要求调用方重复同一信息。

### 5.2 输入与输出模式

- messageId 必填、非空、最多 256 个 Unicode 字符；role 为 `ROLE_USER`，服务器消息为 `ROLE_AGENT`，不按调用方是否 Agent 改 role。
- parts 非空、保序，每个 Part 只使用 text/data/raw/url 一个分支；首期最多 64 个 Parts。
- 支持文本及至多一个 object 类型 data Part；多个 data、数组／标量 data 返回 INVALID_ARGUMENT。这是 BCS 输入限制，不改变 A2A data 可表示任意 JSON 的定义。
- raw/url 文件输入返回 ContentTypeNotSupportedError；不尝试拉取 URL。文本／JSON 的 mediaType 与实际分支一致。
- 原始 Message 的标准字段保存于当前轮次；`referenceTaskIds` 首期不开放，非空明确拒绝，不偷偷跨 context 加载历史。
- `acceptedOutputModes` 省略／空表示使用群支持模式；非空与实际能力求交集，无交集在受理前拒绝。选择结果写入 Task，最终产物不满足时失败收尾。
- 首期成功产物仅文本或 JSON；JSON 输出可为任何合法 JSON 值，不受输入必须 object 的限制。
- 不支持 Push Notification 配置，显式返回标准错误，不能静默忽略。

### 5.3 返回时机、查询与分页

`returnImmediately=false`（默认）等待终态；本期没有 input_required/auth_required 的执行中断恢复。达到服务端等待上限返回 `504 / SEND_WAIT_TIMEOUT`，已知时附 taskId/contextId；不能成功返回未终态 Task。`true` 在持久化受理后返回当时已提交快照。stream 不等待完成，returnImmediately 不影响其行为。

HTTP 超时／断线不取消任务。网关 504 可能没有 taskId，也不证明未受理；调用方按同一 messageId 和完整请求重试，或使用已取得的 taskId 查询。不能为规避等待超时擅自修改原请求的 returnImmediately 再当同一次重试。

GetTask 是只读一致快照，不推进执行。historyLength 省略表示调用方不指定上限，服务端可应用文档化上限；0 不返回历史，正数返回至多 N 条最近公开消息，按时间顺序展示。historyLength 不控制是否生成进度事件。

ListTasks 支持标准 contextId、status、pageSize、pageToken、historyLength、statusTimestampAfter、includeArtifacts；pageSize 默认 50，范围 1..100；timestamp 筛选为 `>=`，默认不含 Artifact。返回 tasks、nextPageToken、pageSize、totalSize；totalSize 是同一主体与筛选条件的精确可见总数，不计所有调用方。

顺序为 `(status_timestamp DESC, task_id DESC)`；游标绑定主体、群、规范化筛选与最后排序键，并做完整性保护。游标只是分页位置，不是授权凭据；跨主体或更改筛选拒绝。并发状态更新期间不承诺跨页快照一致，客户端按 taskId 合并；单页与 totalSize 使用同一数据库读快照。禁止全表加载后内存过滤排序。

### 5.4 可选公开进度扩展

沿用原稿的 `SendMessageRequest.metadata.includeProgress`：boolean，默认 false，本轮固定。使用时须激活 Card 声明的 `call-metadata/v1` 可选扩展；缺激活头或类型错误受理前拒绝，不预留 messageId。

拟发布 URI：`https://github.com/inclusionAI/Avernet/blob/dev/docs/specs/a2a/extensions/call-metadata/v1.md`。实施阶段在该路径交付规范及相邻 `v1.schema.json`，Card 的 schema 引用使用机器可读取的 raw 地址；本次只写 spec，不声称此扩展已发布。响应确认实际激活的 URI，未实现扩展不假装激活。

首期扩展只定义 includeProgress；不冻结 caller/user 来源 schema，不将它们持久化为跨轮身份绑定，不引入 `CONTEXT_SOURCE_CONFLICT`。其他 metadata 仅作有界 opaque 输入参与指纹，不能成为凭据、路由、权限或默认传给成员的指令。未来来源合同必须另行版本化。当前 source-agent/v2 只是来源分类，不可冒用为调用方身份协议。

## 6. 受理、幂等与 context 占用

```text
认证 + wire 校验 → 去重命中 ───────────────────────→ 原 Task
                  ↓ 新请求
        群资格 + context 归属／占用／配置校验
                  ↓
    原子受理（Session + Task + 预留 + 占用 + 启动意图）
                  ↓ 提交后
    精确 execution_id → ManagerWorker / StateMachine
                  ↓
    完成／取消意图 → 关闭派发 → 停止确认与公开投影
                  ↓
        原子结果／终态 → 可安全释放 或 FAILED + 隔离
```

### 6.1 受理顺序

1. 检查传输限制、版本、认证、结构和扩展；不可信数据不进入执行。
2. 按可信主体、group_id、messageId 查去重；验证已有请求的归属和指纹。
3. 命中则返回／等待／订阅原 Task，即使群已关闭，也不重新执行。
4. 对新请求验证当前群资格、输出模式及 context 归属、兼容性、未占用且未隔离。
5. 单一数据库事务保存新 Session 或 CAS reactivation、context 占用、新 Task、messageId 预留、输入、执行配置和持久化启动意图；同时 fencing 群资格 revision。
6. 提交后执行器领取启动意图，通过固定 execution_id 启动并关联 activation／Run／delivery。

执行占用容量（未终态 Task＋仍隔离的 context）随受理／安全释放在同一事务更新，不靠 COUNT 后自增；重复请求复用原容量，FAILED 但仍隔离不能释放该容量，避免连续创建新 context 累积未知执行。

新首轮并发重复请求只能生成一个 context；不能先调用会立即投递的 create_or_reactivate 再补 Task。采用 store-owned 组合事务；不能用多个分别提交的 repo 调用冒充原子受理。

### 6.2 请求分类

| 请求 | 结果 |
| --- | --- |
| 无 contextId/taskId | 创建 context 与 Task |
| 有 contextId，无 taskId，空闲且兼容 | 同 context 创建新 Task、activation／Run |
| context 已占用，包括启动／完成／取消收尾 | `CONTEXT_BUSY`；不排队、不生成新记录 |
| context 隔离 | `CONTEXT_UNAVAILABLE`；旧 Task 仍可查询 |
| 带已存在 taskId 的新消息 | 首期不支持轮内追加，返回 UnsupportedOperationError |
| taskId 未知／跨主体／跨群 | TaskNotFoundError |
| taskId/contextId 同时存在但不匹配 | 归属验证后返回 INVALID_ARGUMENT |
| context 非 A2A 创建、未知／越权 | `CONTEXT_NOT_FOUND`，不替换成新 context |
| 上轮结果已清理 | `CONTEXT_HISTORY_EXPIRED`，要求新 context 并显式提供所需背景 |

占用覆盖受理、尚未派发、执行、完成确认、取消和失败收尾；不能仅检查 A2A state=WORKING。原 Session 的 Completed 只说明内部生命周期结束，不授予绕过占用开启下一轮的权利。

### 6.3 去重键与指纹

去重键为 `(可信部署作用域, issuer_namespace, subject_id, group_id, messageId)`；messageId 采用区分大小写的精确比较，MySQL collation 与 SQLite 行为必须一致。

指纹使用 SHA-256，`fingerprint_version=1`：先按 schema 补等价默认值，再按 [RFC 8785 JCS](https://www.rfc-editor.org/rfc/rfc8785) 生成 UTF-8 规范 JSON；对象键排序，数组／Parts 保序，激活扩展 URI 集合排序去重。拒绝重复 JSON 键、非法 Unicode 和非有限数；高精度业务数字用字符串传入，避免 IEEE-754 转换丢失精度。缺失与 0 的 historyLength 不等价。覆盖整个语义 SendMessageRequest，包括客户端提供的 context/task ID、message 字段、configuration、metadata、已激活扩展集合；不含 token、HTTP request_id、send/stream 路由区别及服务器生成的 contextId。客户端 task_id 与服务器 Task ID 不混用；服务器 Task ID 使用随机 UUID，永不重用，不能靠其不可猜测性替代授权。

配置中的 returnImmediately/historyLength/acceptedOutputModes 即使不改变执行内容，也按原 PR 决策参与指纹。只切换 send/stream 可复用；扩展集合变化或请求字段变化报 `MESSAGE_ID_CONFLICT`。拒绝未知业务配置字段，避免客户端以为字段生效但服务端忽略。

| 同一去重键 | 行为 |
| --- | --- |
| 同指纹，记录仍在 | 复用原 Task；阻塞 send 仍按等待合同，stream 先快照 |
| 异指纹 | 冲突，绝不派发 |
| Task 已清理但墓碑在 | `MESSAGE_RESULT_EXPIRED`，绝不再执行 |

原终态 Task 的重复 stream 可以发一帧 Task 后关闭；这与对终态 Task 新建 subscribe 被拒绝不同。所有受理前拒绝均不预留 messageId。

本期不宣告既有 message-dedup/v1：其 Bot endpoint 与启动失败恢复合同不同。BCS 自有 Group 调用合同支持以上重试，但通用出站 A2A 客户端不能据此自动假定该扩展已经协商。

## 7. 群执行与多轮输入

### 7.1 统一输入和执行身份

每轮持久化原始输入，构造有界执行 envelope，包含 initialMessage、currentMessage、previousTask（上一轮 ID／终态／保留的 Artifact）。初始消息固定，本轮新输入不能只更新 opening_message 而未进入执行节点。

状态机使用 `Run.input.a2a` 保存上述 envelope，`Run.input.text` 保存本轮文本 Parts 的顺序拼接（以换行分隔），`Run.input.data` 保存唯一 data object，缺失时为 `{}`。新开放的流程显式绑定这些字段；原始 Parts 仍在 envelope 中，拼接不替代原始输入。主从群使用等价的结构化上下文，不将 JSON 直接混入系统指令。

previousTask 指同 context 上一轮，而不是“最后一次成功”；FAILED/CANCELED 也携带其状态与可用结果（首期不提交部分 Artifact），避免把失败旧结果当成本轮成功。首轮省略 previousTask；续聊所需前轮结果过期则在受理前拒绝，不静默丢背景。首期不自动读取所有历史轮次或做 LLM 压缩。

验证后的 Caller 仅用于资源归属／审计。来源文本不获得执行权限，凭据不进入 envelope；各 Bot 继续使用其已有执行身份策略。重新运行工作流可能产生新业务副作用，调用方应使用业务单号实现业务幂等。

### 7.2 ManagerWorker 完成协议

1. 本轮启动登记 manager 和 Worker 派发关联，绑定 context、activation、A2A task_id 与内部执行 ID。
2. 只有当前 manager 的受信关联能够提交本轮 `task.complete`。不能仅相信模型 payload 中的 activation，也不能回退到群“最近 Running Session”。
3. 原子检查 Worker、回送、重试等未完成工作，关闭本轮新派发，固定非空最终 summary、完成意图和事件边界。
4. 给 manager 返回完成意图已接收的 ack；这不是 A2A COMPLETED，也不是允许下一轮。
5. 等待 manager 当前执行退出和其他已关联工作停止／收尾。
6. 完成公开投影、结果校验并执行 §9 的终态事务。

“先 ack，再等退出”防止 manager 等待工具返回而 BCS 同时等待 manager 退出的死锁。相同完成关联与相同 summary 幂等；冲突完成、旧 activation 或取消意图已胜出的完成拒绝。聊天最后一句、Worker 完成、空闲都不是成功信号。

manager 无运行／排队工作、无回送、无重试、无不确定工作时才计空闲。建议空闲 60 秒提醒一次，提醒退出后再给 120 秒实际空闲宽限；每 Task 最多一次提醒。宽限计时仅在真正空闲时累计，实际新工作不因消息静默失败，但仍受整轮绝对截止时间约束。提醒标记和累计时间持久化，重启不重新获得完整预算。

### 7.3 StateMachine 完成协议

每个新 Task 从入口新建 Run，固定 definition 版本、成员绑定、结果选择和本轮输入；不借用 Failed Run rerun 来续聊。原生 rerun 对 A2A 托管 Run 不开放，避免一次外部 Task 启动第二个独立执行。

开启时的结果选择需要固定为 `a2a_output` 执行配置：命名一个产生成功结果的节点及 text/data 投影；支持多产物时配置有序的选择列表，每项有固定 artifact key/name。必须覆盖所有允许的成功结束分支；缺少选定输出或类型不匹配使本轮失败。顺序由配置决定，不靠节点完成时间或遍历顺序猜测。输出选择存于 Group runtime binding 的独立、版本化 A2A 配置中，Wire 使用 `a2aOutput`，不覆盖原始流程 DSL 或在 Card 上存执行规则。

Run 显示成功不直接等于 A2A 成功：必须确认关联投递收尾，投影到边界，再写 Artifact 和终态。意外进入 HumanInput／缺少执行身份按失败收尾，不伪造 INPUT_REQUIRED 后永久挂起。

## 8. 终态、取消与恢复

### 8.1 两层状态

对外使用标准 TASK_STATE_SUBMITTED、WORKING、COMPLETED、FAILED、CANCELED；本期不主动生成 REJECTED，但识别其终态。内部另存阶段：admitted、starting、running、completing、cancelling、failing、terminal；context 存 idle、occupied、isolated。

| 内部事实 | 对外状态／context |
| --- | --- |
| 原子受理、尚未开始 | SUBMITTED／occupied |
| 已启动或正在收尾 | WORKING；尚未启动取消时可仍 SUBMITTED／occupied |
| 成功意图、执行停止、产物事务成功 | COMPLETED／idle |
| 失败且停止已确认 | FAILED／idle |
| 用户取消且停止已确认 | CANCELED／idle |
| 收尾截止仍未知 | FAILED／isolated |
| 后续确认安全 | 原 FAILED 不变；context isolated → idle |

Session 原有 Running/Completed 不扩充为 A2A 状态机；内部完成仅在精确 activation 上操作。Task 的成功与 Session.Completed 不能互相替代。A2A 托管标记要求 MessageFlow／Runtime 将完成候选交给调用生命周期，不再走其原有的独立 Session 完成写入；Session CAS 归 §9 终态组合事务所有。非 A2A 路径保持原行为，避免 Runtime 提前写 Completed 后组合事务失去原子性。

### 8.2 取消和完成的竞争

取消先 CAS 持久化意图和收尾 deadline，关闭新派发，再停止 manager、Worker、状态机节点、队列项、回送和重试。所有投递必须携带可核对的精确范围；不能对共享 Bot 执行无差别全局 abort。

完成与取消以同一 Task revision 仲裁。完成意图已提交并进入 completing 时，本轮不再可取消，返回 TaskNotCancelableError；取消先提交时完成回调不能再提交成功。重复处理中取消复用原意图、原 deadline；已终态再次取消报标准不可取消错误。

CancelTask 可返回仍非终态 Task 和安全的取消进度说明。只有底层对精确关联工作返回终态／停止确认，或能够证明启动前被撤销，才算 stopped。取消 HTTP 请求成功、Run 标为 Aborted、租约到期、断连均不能单独证明 Bot 已停止。

收尾预算内无法证明全部停止，提交 FAILED、原因 STOP_UNCONFIRMED 并持久化 context 隔离及未核实关联。旧代次禁止新派发和公开结果写入；已经发生的业务写入不自动回滚。

### 8.3 重启与持久化启动

启动意图由有租约、版本 fencing 的 worker 领取。持久化的 execution_id 在调用 Runtime／MessageFlow 前确定，它们必须能按 ID 查询、幂等确认同一执行，而不是每次调用随机创建新的 Run。

| 故障位置 | 恢复行为 |
| --- | --- |
| 受理事务前／回滚 | 无执行，无预留 |
| 提交后尚未启动 | 通过同一 execution_id 核实未启动后启动 |
| 派发后关联写入／响应丢失 | 先查询已有执行；不能确定则失败收尾／隔离，不盲目重发 |
| 执行中 | 恢复原 activation／Run／投递；新 owner 的 CAS fencing 拒绝旧 owner 写入 |
| 完成或取消中 | 恢复原意图、固定结果、消费位置与绝对截止时间 |
| 终态事务前崩溃 | 不公开部分产物；继续核实和提交 |
| 终态事务后响应丢失 | 重试／GetTask 返回已提交结果，不再次执行 |
| FAILED 但 context 隔离 | 继续核实关联，不因 Task 终态释放占用 |

数据库不可用时停止新派发与成功响应，不得只在内存释放 context／宣称终态已提交；恢复存储后依据已保存的绝对期限完成失败／隔离事务，运维告警保留故障窗口。

A2A deadline 是新调用生命周期的责任，不恢复已废弃的 service_spec、旧 Session timeout scanner 或 callback 机制。各节点现有更短执行期限仍有效，不能被 A2A 延长。

### 8.4 隔离恢复和运维

恢复扫描必须按 `next_check_at`、稳定游标有界执行；失败指数退避并有每轮预算。超过自动尝试预算后进入可查询的待人工核实状态并告警，不高速反复扫描。

恢复只在全部关联停止且旧代次写入已 fence 时解封。管理入口提供查看关联、触发重新核实、提交停止证据后解封；不能提供无证据“直接置 idle”。人工介入记录操作者、原因与关联证据 ID，不写凭据或生产聊天正文。切换 owner／重启均不能清空隔离记录。

## 9. 持久化、原子性与清理

### 9.1 建议的逻辑存储

具体列类型按双数据库规范实现，以下表名和唯一性是本稿建议。所有归属列来自可信上下文，外部 ID 字符串区分大小写。

| 记录 | 核心字段／约束 |
| --- | --- |
| `bcs_groups` 扩展 | support_a2a 默认 false、a2a_revision；不复用标签或 GroupStatus 代替开关 |
| `bcs_a2a_clients` / `bcs_a2a_tokens` | 稳定 client_id、启停；凭据摘要唯一、用途、有效期、撤销；无原文 |
| `bcs_a2a_contexts` | session_id 唯一、group/Caller scope、创建来源、初始输入、执行配置指纹、active_task_id、generation、isolation 状态 |
| `bcs_a2a_tasks` | task_id、context、Caller scope、messageId、输入、输出模式、状态／阶段／revision、执行关联、绝对期限、完成／取消意图、大小与保留时间 |
| `bcs_a2a_message_reservations` | 去重键唯一、fingerprint_version/hash、task_id、结果清理标记；保留墓碑 |
| `bcs_a2a_artifacts` | `(task_id, artifact_id)` 唯一；position 唯一、context_id、name、description、parts JSON、公开 metadata/extensions、created_at |
| `bcs_a2a_public_events` | `(task_id, sequence)` 唯一；稳定 event_id、公开 payload、来源去重键；供 history 与 SSE 读取 |
| `bcs_a2a_execution_intents` | task/generation、execution_id、启动状态、lease/CAS、next_check_at、关联工作／停止证据与扫描索引 |
| `bcs_group_a2a_publications` | group_id 唯一、当前期望 revision、已确认 revision、下一重试时间；历史撤销保留最小墓碑 |

Task 对外 metadata 只允许已声明的安全字段，不回显内部执行 envelope、任意输入 metadata 或凭据标识。公开 Message 历史是 public_events 的有序 Message 投影，不复制一套无法对齐的 transcript。查询使用适当 kind／sequence 索引；内部 `bcs_events` 保持执行事实来源，公开投影额外保存 `(source_stream, source_sequence, event_id)` 消费位置，不能假定不同 stream 全局有序。

Task／Artifact 的外键归属必须在组合写中校验。ContextID 不单独构成授权凭据；不在每个 Artifact 复制凭据。Task 状态时间使用 UTC，只有状态／公开 status.message 更新时改变，不因查询和保活改变。

索引至少覆盖：Task 的 `(scope, Caller, group_id, status_timestamp, task_id)` 和 context 查询；预留唯一键；Artifact `(task_id, position)`；公开事件 `(task_id, sequence)`；恢复／清理／发布的到期时间与 key。组合身份键超过数据库索引字节限制时使用固定宽摘要索引并比较保存的完整键，不裁剪主体或 messageId 来规避限制；摘要碰撞不得复用不同请求。

### 9.2 事务边界

- **受理事务**：群 revision 检查＋Session 创建／精确 reactivation＋context 占用＋Task＋去重预留＋启动意图，一起提交或回滚。
- **成功事务**：验证 Task revision/generation 和完成边界，插入全部最终 Artifact／最终公开事件，写 Task.COMPLETED，精确完成 Session，释放 context，一起提交。
- **失败／取消事务**：写公开终态与事件；停止已确认则完成 Session 并释放，否则完成失败记录且保留 context 隔离和恢复关联。
- **投影事务**：推进 source cursor、插入去重后的公开事件与相关状态一起提交。消费 source 时不能只在内存记 cursor。
- **清理事务**：在无恢复／续聊依赖时原子设置 Task 的结果逻辑删除标记和预留墓碑；所有读取只看未删除结果。提交后按有界分片物理回收内容，失败可重试，读者不看到半清理快照。

复用 DbPlugin 的组合事务和已有 Store-owned transaction fragments 模式。组合 Repo 必须提供原子语义，Core／application 不拼 SQL；如果同库对象不在同一逻辑事务能力内，先补齐合同，不用补偿假装已经原子。

GetTask 的状态与 Artifact 在一致读中加载；ListTasks 采用一致读及页内批量加载。读失败返回服务错误，不能当作空结果。Memory 实现使用同一组合锁证明合同，SQLite／MySQL 证明真实事务语义。

### 9.3 Artifact 与内容边界

Artifact 至少有一个 Part；artifactId 在 Task 内稳定、position 有序。首期最终产物一次提交，SSE 使用 `append=false, lastChunk=true`，不实现逐 token Artifact 更新；首期 FAILED/CANCELED 不提交部分 Artifact，只保存安全失败原因和已有公开进度。manager summary 生成文本 Artifact；状态机按固定输出选择生成文本或 JSON。

Parts 存储保留类型、顺序、mediaType、filename、公开 metadata，可表达 raw/url 的未来引用变体；首期不实现文件摄取、对象存储或下载，也不允许 Group 通过 metadata 绕过文本／JSON 限制。不能把未定义的内部对象引用作为 A2A wire 字段返回。

公开进度使用确定的阶段模板；内部身份、系统提示、模型思考、工具原始输出、内部 URL、堆栈不直接进入 history/status.message/Artifact。manager summary 和配置选中的输出是显式交付面，仍需类型／大小／敏感字段校验；不能承诺简单字符串过滤能证明任意自然语言绝对不含敏感信息。检查不通过则失败，不能边发结果边发现不可公开。

### 9.4 保留与过期

建议终态结果（Task 公开内容、history、events、Artifact）保留 7 天，自终态提交起算，读取不续期。未终态、隔离／恢复依赖、正在用作已受理下一轮背景的记录不清理。下一轮受理与前轮清理通过锁／版本串行化，不能受理后才发现背景已消失。

续聊输入使用前轮 Artifact 的有界快照；其复制内容属于下一轮输入，按下一轮的保留期限处理。原始目标属于 context；最后一轮终态满结果保留期且无活动／隔离依赖后，将 context 标为 history-expired，并清理初始输入和执行快照等大字段；不再允许续聊。最小来源、Caller 归属和隔离状态保留到安全删除 Session。

到达 TTL 不等于已清理；以清理事务提交的逻辑删除为唯一可见切换点，物理回收可以滞后但不得再次暴露内容。清理后 GetTask 返回 TASK_NOT_FOUND，相同 messageId 返回 MESSAGE_RESULT_EXPIRED；context 续聊返回 CONTEXT_HISTORY_EXPIRED。ID 预留不跟随结果 TTL 删除，至少保留到 Group 调用存储整体退役；Group ID／Caller ID 不重新分配。长期墓碑增长必须在容量与运维指标中可见，不能用短 TTL 换取可能的重复执行。

## 10. 公开事件、等待与 SSE

`includeProgress=false` 只关闭可选阶段进度，不影响 Task 状态、输入历史、最终产物和失败原因。投影在持久化公开出口之前执行；GetTask、ListTasks、SSE 共用相同公开数据，不能一条路径直接透出内部事件。

订阅在一致快照中取得 Task 和公开 sequence 高水位，首帧发该 Task，随后从高水位之后消费持久化事件。连接通知只用于唤醒，不是事实来源；快照与订阅建立间发生完成也不能丢终态。

公开事件顺序：已纳入完成边界的过程消息 → 最终 Artifact → 终态。最终事务提交后才发送 Artifact；终态后不追加事件。多源边界、重复 source event 与迟到 callback 都按 Task generation 处理。

- message:stream／subscribe 每帧都是标准 StreamResponse；不沿用 A2A 0.3 的 kind、final 等字段。
- 新建 subscribe 时 Task 已终态，返回 UnsupportedOperationError；建连后才终态，则发终态并关闭。
- 重连先取当前快照；首期不承诺按 Last-Event-ID 重放断线期间全部事件，客户端使用 Task／history／Artifact 恢复。
- 无业务事件每 15 秒发送 `: keep-alive` 注释并 flush；不写事件表、不延长执行预算。网关禁用缓冲，空闲超时大于保活间隔。
- 连接断开不取消任务；慢客户端超过有界发送队列则关闭，让其重连，不无限占内存、不阻塞执行。
- 阻塞 send 使用相同持久化状态与通知机制，不每请求无限轮询；SSE 重连和轮询有服务级并发／速率预算。
- 认证到期或撤销导致流关闭是连接事件，不改变 Task 终态；换有效凭据后重新认证及校验归属。

## 11. 错误合同

HTTP+JSON 使用 `{ "error": { "code", "status", "message", "details" } }`；code 为实际 HTTP 状态。A2A 标准错误使用 ErrorInfo 的 UPPER_SNAKE_CASE reason 和 `domain=a2a-protocol.org`。BCS 扩展 reason 使用稳定 domain `inclusionai.avernet.bcs`。metadata 值使用字符串，不携带原始 SQL、凭据或内部异常。

| 场景 | HTTP / status | reason |
| --- | --- | --- |
| 缺失／无效／过期／撤销调用凭据 | 401 / UNAUTHENTICATED | AUTHENTICATION_REQUIRED / INVALID_TOKEN |
| 认证依赖不可用 | 503 / UNAVAILABLE | AUTH_DEPENDENCY_UNAVAILABLE |
| 不存在／private／未开放群的新调用或 Card | 404 / NOT_FOUND | GROUP_A2A_NOT_AVAILABLE |
| 已开启但执行配置损坏／不再合格 | 400 / FAILED_PRECONDITION | GROUP_A2A_NOT_READY |
| 管理面开启校验失败 | 管理接口原 envelope、400 | GROUP_A2A_INELIGIBLE，含安全字段级原因 |
| 群改动违反已开启不变量 | 管理接口原 envelope、409 | GROUP_A2A_CONFIGURATION_CONFLICT |
| Task 未知／不可访问 | 404 / NOT_FOUND | TASK_NOT_FOUND（标准） |
| context 未知／非 A2A／不可访问 | 404 / NOT_FOUND | CONTEXT_NOT_FOUND |
| context 占用 | 409 / ABORTED | CONTEXT_BUSY |
| context 隔离 | 400 / FAILED_PRECONDITION | CONTEXT_UNAVAILABLE |
| context 配置不兼容／背景已清理 | 400 / FAILED_PRECONDITION | CONTEXT_CONFIGURATION_CHANGED / CONTEXT_HISTORY_EXPIRED |
| 重复 ID 内容冲突／结果已清理 | 400 / INVALID_ARGUMENT；404 / NOT_FOUND | MESSAGE_ID_CONFLICT / MESSAGE_RESULT_EXPIRED |
| 已存在 Task 的新输入／终态新 subscribe | 400 / FAILED_PRECONDITION | UNSUPPORTED_OPERATION（标准） |
| 终态或 completing 取消 | 400 / FAILED_PRECONDITION | TASK_NOT_CANCELABLE（标准） |
| 输入／输出媒体不支持 | 400 / INVALID_ARGUMENT | CONTENT_TYPE_NOT_SUPPORTED（标准） |
| Push 配置／协议版本不支持 | 400 / FAILED_PRECONDITION | PUSH_NOTIFICATION_NOT_SUPPORTED / VERSION_NOT_SUPPORTED（标准） |
| 扩展未激活／参数无效 | 400 / INVALID_ARGUMENT | CALL_METADATA_NOT_ACTIVATED / INVALID_ARGUMENT |
| 阻塞 send 等待超时 | 504 / DEADLINE_EXCEEDED | SEND_WAIT_TIMEOUT |
| 请求体超限／入站容量超限 | 413 / RESOURCE_EXHAUSTED；429 / RESOURCE_EXHAUSTED | REQUEST_TOO_LARGE / ADMISSION_LIMIT_REACHED |
| 持久化依赖失败 | 503 / UNAVAILABLE | STORAGE_UNAVAILABLE |

Task 执行失败通过成功查询返回 TASK_STATE_FAILED 和安全业务原因，不把已受理失败伪装成未受理 HTTP 400。执行错误类别至少覆盖 START_FAILED、EXECUTION_FAILED、EXECUTION_DEADLINE_EXCEEDED、COMPLETION_SIGNAL_MISSING、OUTPUT_INVALID、STOP_UNCONFIRMED。隔离只能通过恢复证据解除。

错误中的 activeTaskId/contextId 仅在已确认归属后出现；跨主体请求不能借错误响应探测其他人的任务。

## 12. 默认参数与访问成本

### 12.1 建议默认值

以下为可实施的初始配置建议，**尚非负载测试结论**；书面评审可修改数值，不改变上面的语义。配置由 bootstrap 验证并注入，任务受理时固化其截止时间及生效预算。

| 参数 | 建议默认／边界 |
| --- | --- |
| send 同步等待 | 60 秒；网关等待至少多留 10 秒 |
| Task 整轮执行 | 30 分钟，从受理提交起算，含启动等待 |
| 完成／取消／失败收尾 | 60 秒；终态预算最长执行期限＋一次收尾，不因重试重置 |
| manager 空闲提醒／提醒后空闲宽限 | 60 秒／120 秒，每 Task 最多一次提醒 |
| 结果保留 | 7 天；恢复／背景依赖不清理 |
| SSE 心跳／撤销复验 | 15 秒；发送队列至多 128 条或 1 MiB，先达到者生效 |
| JSON 请求体 | 1 MiB，包含 message/configuration/metadata，解析前限制 |
| 最终 Artifact | 每 Task 至多 16 个，总序列化内容至多 256 KiB |
| 公开 history | 每 Message 至多 4 KiB；每 Task 持久化至多 256 条／1 MiB；省略 historyLength 返回最多 20 条，显式请求最多 100 条 |
| 进度超额 | 停止新增可选进度并留一次截断说明；不挤占终态事件与 Artifact 配额 |
| ListTasks | 默认 50，最大 100；单响应最多 8 MiB，可少于请求数量，不截断单个 Artifact |
| 恢复／清理／目录迁移扫描 | 每批最多 100 个轻量 key；下一批释放连接并允许让出执行 |
| 单轮关联工作 | 至多 256 个未终结关联；超额拒绝派发并由本轮失败收尾，不丢关联记录 |
| 新任务容量 | 每 Caller 最多 16 个执行占用、每部署最多 128 个，均含未终态与隔离 context；超额 429，不预留 ID |
| SSE／同步等待连接 | 每 Caller 最多 32 条、每实例最多 512 条；建连超额 429，已提交 Task 不取消 |
| 认证检查 | 单请求默认 3 秒上限；故障返回 503，不能等同于凭据无效 |
| 新签发 token | 建议默认 30 天、最大 90 天；到期前通过管理面轮换 |

原始输入可超过公开单条 history 上限：输入原文作为私有 Task 输入完整保存，history 使用明确标识的有界摘要，不宣称等于完整输入。公开输出超限必须 OUTPUT_INVALID，不悄悄截断为成功结果。历史容量计数不含输入原文、终态和最终 Artifact。

### 12.2 数据库与连接预算

| 热路径 | 访问要求 |
| --- | --- |
| 认证 | 一次按 token 摘要索引的 token/client 联查；不扫描 Bot 或所有凭据 |
| 新受理 | 一次精确去重查找＋有界资格／context 读取＋单个短组合事务；不列举历史 Session／Task |
| 幂等命中 | 精确预留查找＋Task 一致读取，不再触发执行配置／外部派发 |
| GetTask | 一条带 Caller 条件的 Task 读取、一次 Artifact 批量读、一次有界 history 读；不读取内部全量消息 |
| ListTasks | 轻量索引页＋同条件 COUNT；根据大小元数据选择 8 MiB 内的前缀，再批量读取选中页产物和历史，不能逐 Task N+1 |
| Catalog | group_id 精确快照一次；资格基于事务维护的群状态，不每次扫描所有 Session |
| SSE | 合并共享 Task 通知与有界 cursor 消费；心跳不写 DB，凭据复验成本随活跃连接数计入容量 |
| 恢复／取消 | 有界关联分页、CAS claim、退避；事务或锁外请求下游，不能每轮扫描所有工作 |
| 清理 | 一次 claim 至多 100 个 key，先原子切换 Task／墓碑逻辑可见性，再按每 Task 有界分片物理回收 |

List 的 history 批量查询采用 partition 内 limit 或等价实现，不能查询整页 Task 的全部事件后在内存丢弃。验证上述请求／连接并发上限并记录压测结果；持久化批处理不代表无限事务廉价。COUNT 可能随可见任务总量增长，须验证索引及超时，不能换成加载全行计数。

验收需要普通、最大页／产物／关联规模以及持续失败三类 query-count、batch-size 测试。真实 MySQL 执行计划和负载测试未运行前不宣称规模性能达标。

## 13. 模块边界与变更传播

| 拥有者 | 责任／不得承担的责任 |
| --- | --- |
| `bcs-api-http` | 新 A2A DTO、路由、头、标准错误、SSE；只调用 application，不直连 Store／Runtime 实现 |
| 认证 Plugin API／实现 | 统一验证、稳定主体、凭据用途／生命周期；不执行群业务，不复用 Bot 身份冒充应用 |
| 新 Group A2A application | 能力与归属、受理、等待／订阅、执行端口编排、取消和恢复；不持有 HTTP 类型或 SQL |
| Group／Session Core | 开关资格及持续不变量、A2A Session 托管边界、精确代次；不直接调用外部投递 |
| A2A lifecycle Core／Repo | Task、context、去重、版本状态及组合原子提交；Core 只消费声明的 Repo |
| MessageFlow application／执行端口 | manager/Worker 精确派发、完成 ack、退出确认、整轮取消、停止证据 |
| Collaboration Runtime | 新 Run 输入、输出选择、执行 ID 幂等、完整停止确认；保留非 A2A 既有行为 |
| Eventing／Event Store | 原子事实与公开投影关联；不把所有内部事件直接开放 |
| Group／A2A Store | 跨记录事务、索引、方言、缓存失效、结果清理及目录同步意图；不在事务内发 HTTP |
| Bootstrap | 认证链、端口、worker、时钟、预算、公开 URL 和存储装配；host 只接线，不复制业务策略 |

具体新增 crate 名在实施计划确定，合同先于实现：`GroupA2aService`（入站用例）、`GroupInvocationCoreService`（状态转换）、`GroupInvocationRepo`（组合持久化）、`GroupExecutionPort`（启动／查询／停止）需要共享 conformance suite。这里的名字表示拟新增合同，不是当前已存在的类型。

控制面 Group create/update、所有成员写路径、Session 管理、Runtime、Card snapshot、Fuse 同步、bootstrap 和嵌入 host 共同传播。HTTP camelCase `supportA2A` 与 Rust snake_case 映射显式测试。新增语义不通过放宽跨层依赖或共享可变全局单例实现。

## 14. 迁移、交付与回退

1. 先交付公开配置／wire／Service API／Plugin API 合同和 conformance；新 A2A 默认关闭，历史 support_a2a=false。
2. 新增同批发布的一组 SQLite／MySQL 迁移，包含开关及必需调用／凭据／同步存储。按实施时最新版本各自新增编号；不预占当前编号，不修改已提交的 baseline 或历史迁移。
3. 完成最小凭据签发、开关资格、Card 及“单轮受理→执行→持久化结果”。本阶段不向用户宣布可上线，直至取消恢复同样达标。
4. 完成多轮、幂等竞争、失败／取消／重启恢复、SSE、清理和目录核对；更新相应当前合同与知识导航。
5. 公共 Singlebox 在 SQLite 跑 Agent／应用系统两种调用示例；真实 MySQL 跑事务、升级链、竞争和执行计划；嵌入 host 验证装配和依赖版本。
6. 上线先保持所有群关闭，管理员显式开启经过资格检查的群。核对旧目录，停用不识别 support_a2a 的旧发布 worker；不允许新旧写策略并存。

兼容性：新增开关为默认 false，但目录旧 Card 被撤销是有意的行为改变，必须给出迁移说明。既有非 A2A 服务 Session、原生聊天和 Agent 注册接口不改变；A2A 托管资源的旁路限制是新增保护。

回退优先关闭群开关，停止受理，保留查询／取消／恢复 worker 直到所有任务停止；仅关闭总入口会暂时失去查询能力，不能作为默认有损回退。保留新表和墓碑，不回滚数据结构。旧二进制不理解新开关、同步规则和托管边界，不可直接恢复为混跑状态；需维护窗口或保持新保护层。Card 撤销与后台未完成任务分别核对。

## 15. 验收矩阵

下表是**未来实现必须通过的测试**，不是本次文档已运行的测试。

| ID | 场景与判定 |
| --- | --- |
| AC-01 | 应用 client 不注册 Bot，持 BCS token 完成调用；Agent client 使用相同协议 |
| AC-02 | 缺失／错误／过期／撤销 token 返回 401；依赖失败 503；无匿名或 local mock 兜底 |
| AC-03 | 空链、未知／无鉴权插件启动失败；配置的插件确实被所有七类入口调用 |
| AC-04 | token 轮换保持主体与原 Task 可见；另一主体无法 get/list/subscribe/cancel/续聊／去重窥探 |
| AC-05 | 对任意合格 Group 均可调用，不要求 Group grant；调用 token 不能修改开关或签发凭据 |
| AC-06 | 旧群默认 supportA2A=false；public 单独不足；private+开启失败；同事务改 public 并开启成功 |
| AC-07 | Chat/DM、Human（含 absent）、无 manager、HumanInput、无结果选择拒绝开启；owner 是 Human 不被误判为成员 |
| AC-08 | 所有邀请／成员／visibility／流程修改入口守住开启不变量；并发开启和加 Human 最多一个成功 |
| AC-09 | supportA2A 显式 JSON 名、PATCH 省略／null／非布尔行为，以及创建／读取／更新回归 |
| AC-10 | 关闭与受理竞争按事务顺序判定；关闭后新轮次失败，旧任务读取／取消和幂等命中仍可用 |
| AC-11 | Card GET 与 Fuse 同一资格／能力／安全声明；关闭后 DELETE，重启恢复未完成意图，旧目录有界核对 |
| AC-12 | 目录写超时／旧 PUT 迟到不会绕过入站资格；最终核对到最新版本，失败可见且可重试 |
| AC-13 | 新 context 并发同 messageId 只创建一个 Session/Task；事务失败不投递 |
| AC-14 | 同 context 两个新请求只有一个受理；busy 不预留 messageId，稍后可重发 |
| AC-15 | 重启、send/stream 切换、JSON key 顺序、默认值等价均正确去重；改 configuration/metadata/扩展冲突 |
| AC-16 | 两种群完成两轮，同 context、不同 Task，各轮结果稳定；新输入确实到达 manager／Run 节点，Runtime 不提前独立完成 Session |
| AC-17 | manager 完成先 ack 再等退出；Worker 未结束、回送未完成、旧 activation／伪造完成不能提前成功 |
| AC-18 | 无完成信号只提醒一次并有界失败；正常长 Worker 无消息不触发空闲超时 |
| AC-19 | 状态机多分支结果选择确定；缺结果、类型错、超限、HumanInput 意外进入按失败收尾 |
| AC-20 | 完成／取消／迟到回调竞争只有一个终态；取消覆盖队列、活动节点、Worker 和回送 |
| AC-21 | Run.Aborted／下游 abort 请求成功但停止未确认：保持占用，超时 FAILED+isolated，不能新执行 |
| AC-22 | 每个恢复崩溃点、主从切换、数据库错误不重置期限、不重发未知执行、不丢恢复关联；隔离仍占容量，禁止无限累积未知执行 |
| AC-23 | 停止确认后只解除 context 隔离，不改原 FAILED；没有证据的人工解封拒绝 |
| AC-24 | Artifact／终态事务注入失败无部分成功；GetTask 和 SSE 重启前后结果相同，ID／顺序稳定 |
| AC-25 | 公开投影重复事件不重复历史；snapshot/subscribe 竞争不丢终态；终态后无公开追加 |
| AC-26 | 默认 send 终态返回／60 秒 504；断线任务继续；终态新 subscribe/cancel 与重复 stream 分别按合同 |
| AC-27 | SSE 保活、代理 flush、慢客户端、有界缓冲、持续业务事件时的凭据到期／撤销复验、重连隔离及并发限额 |
| AC-28 | 页内授权及精确总数、游标篡改／跨主体、时间筛选 >=、最大页产物和 history 无 N+1／全量扫描 |
| AC-29 | 文件／重复 data／超限／不支持输出／缺扩展头等受理前拒绝且不占 messageId |
| AC-30 | 清理与下一轮受理竞争安全；逻辑删除后即使物理回收失败，墓碑仍阻止再执行，context 背景过期明确拒绝 |
| AC-31 | 普通 Session reactivation、聊天、成员管理和原生 rerun 不能绕过 A2A 托管边界 |
| AC-32 | SQLite／真实 MySQL 的组合事务、collation、迁移新装／升级和完整性一致；恢复批次与失败退避有断言 |
| AC-33 | 日志／错误／history／SSE／Card 不含凭据或内部工具原文；首期不声称自由文本过滤绝对防泄漏 |
| AC-34 | 旧非 A2A 群调用、管理权限、Agent Card 注册、Bot catalog 行为回归；嵌入 host 接线验证 |

## 16. 文档验证与交付状态

本次只交付规范、示例、证据和导航。协议字段以固定版本官方 proto／HTTP+JSON 章节校对；当前实现依据及其限制见 [research.md](research.md)。具体检查结果在该页记录，不将未来 AC 表写成通过。

本稿的凭据实现建议、参数默认值、物理存储布局、结果选择字段属于书面评审内容；对应拆解见 [实施计划](plan.md)；编写计划不等于这些建议已最终批准，也不等于实现已经开始。发布扩展规范、修改正式 API、编写迁移、实际压力验证和部署均留到实现交付。没有功能实现、合并、host 集成或上线结论。
