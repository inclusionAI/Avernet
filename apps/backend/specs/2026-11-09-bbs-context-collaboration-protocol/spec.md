# BBS Context Collaboration Protocol v1 设计方案

- 日期：2026-11-09
- 状态：Draft / 待评审，尚未实现
- 归属：Backend Community / Task 主导的跨端目标设计
- 参与模块：Backend Collaboration Context、BBS、Task / Relay、BCS、公开 API consumers
- 本次交付：方案文档，不包含代码、数据库迁移或运行接口变更
- 文档位置：按用户要求放在 Backend specs，说明一个由 Backend 拥有、可跨 BBS / Task / API consumers 使用的上下文协作协议

## 0. 一句话方案

> 在单 Bot 和 BCN 协作群之外，新增一种以持久、可投影、可演进的 **Collaboration Context** 为中心的自主协作模式：上下文拥有目标、可见范围、材料和业务状态；一个可版本化、可扩展、但不能执行任意代码的状态机决定下一步；由事件驱动公开讨论、任务接力、代办清单等不同协作方式推进同一目标。

该模式与现有单 Bot 和 BCN 协作群并存，不是将二者升级或替换；也不是新建第二套执行调度系统。Collaboration Context 是这类新模式自己的协作中心，现有会话、群聊、任务图和帖子是它在不同阶段可绑定的协作表面或执行通道。

## 1. 背景与问题

现有协作入口各有中心：

| 现有形态 | 中心 | 优点 | 限制 |
| --- | --- | --- | --- |
| 单 Bot 会话 | 一个 Bot 会话 | 体验直接，启动快 | 会话之外的人难以接力，目标和状态不持久 |
| BCN 协作群 | 群 / session | 多 Bot 可对话，BCS 可路由 | 目标被群生命周期绑定，换协作方式往往要换群或重新复述上下文 |
| BBS 帖子 | Topic / Post | 公开讨论、时间线、社区可见 | 没有统一业务状态，无法可靠表达谁在做、下一步、是否完成 |
| Task / Relay | 执行任务 / 图 | 目标驱动、可恢复、可接力 | 强执行语义，公开讨论和轻量代办无法自然承载 |
| 自定义协作群 | 一个群的一次 StateMachine Run | 能表达结构化流程 | 流程和群生命周期耦合，不适合作为跨表面长期对象 |

在 Context 模式中，协作可以随业务阶段变化：先在 BBS 公开讨论一个问题；讨论清楚后同一 Context 升级为任务接力；执行过程中公开讨论仍可继续；结果可以是 PR、文档、决议，也可以只是一组已完成的待办。用户中途把接力改为代办清单，目标和材料仍保留。

## 2. 关键定义

### 2.1 Collaboration Context

`CollaborationContext` 是新的持久聚合根。它既不是现有 `TaskSpec.Context`，也不是传给 LLM 的一段 prompt 文本。现有 `agentclaw.community.core.task.domain.models.Context` 只是任务规格里的上下文数据，因此新概念必须使用 `CollaborationContext`，避免命名冲突。

它拥有：

1. 业务身份与作用域。
2. 目标、背景、约束、交付物和验收口径。
3. 参与者、可见性、授权和责任人。
4. 可引用的工作材料，不保存字节。
5. 业务状态与状态机定义版本。
6. 事件日志与版本号。
7. 一个或多个协作表面绑定，例如 BBS Topic、公开 API consumer、BCN 群、Task / Relay 执行。
8. 轻量代办清单和可升级为正式 Work 的事项。

### 2.2 Command / Event / State / Transition

Context 由事件推进，不由任何 Bot 直接改状态：

- `ContextCommand`：人类、系统或受信适配器提交的意图，例如“发布回复”“启动接力”“完成待办”“验收通过”。
- `ContextEvent`：服务端接纳后的不可变事实，例如 `discussion.posted`、`work.completed`、`todo.completed`。
- `State`：Context 当前业务阶段，例如 `draft`、`discussing`、`executing`、`reviewing`、`waiting`、`done`。
- `Transition`：显式状态迁移记录，包含来源状态、触发事件、命中规则、目标状态、守卫结果、操作者和迁移证据。
- `StateMachineDefinition`：版本化状态机合同；Context 绑定后冻结其 revision。

LLM 可以输出执行结果、摘要或建议，但不能直接改写 Context 状态。状态只能由服务端状态机根据已接纳事件确定地转换。

### 2.3 Context State

Context 只有唯一聚合级状态。该状态描述业务阶段和下一步协作方式，例如 `collecting_info`、`handoff_running`、`reviewing`、`done`，由状态机根据已接纳的 `ContextEvent` 推进。

外部系统或子实体的事实不构成本协议的第二类 Context State。适配器可以引用外部事实，但必须先由外部系统接纳并转换成 ContextEvent；Context 不能代表、覆盖或反向修改任何外部系统自己的运行事实。

## 3. 目标与范围

### 3.1 产品目标

- **G1** 新增基于 Context 的自主协作模式，并与单 Bot、BCN 协作群和 BBS 帖子并行，而不是把其中任何一种升级成 Context 模式。
- **G2** Context 具备可持久、可观察、可调试的业务状态。
- **G3** 状态机可自定义扩展，但校验、授权、边界和审计由服务端治理。
- **G4** Context 状态作为驱动层，可按阶段选择协作方式，而不是提前把业务固化成一种运行时。
- **G5** 统一支持 BBS 公开讨论、Task / Relay 任务接力、以及人类与 Bot 都可参与的代办清单；必要时可按阶段挂接单 Bot、BCN 群或 BCS 自定义协作。
- **G6** BBS、任务图、Bot 和公开 API consumers 读取同一 Context 投影，避免同一 Context 对外出现多套业务进度。
- **G7** 状态推进幂等、可恢复、可审计，可回放事实重建状态。

### 3.2 非目标

- 不替代 TaskGraph、Relay、BCS 群、BCS StateMachine Run 和 BCS 路由。
- 不新建另一套工作流执行器或全局调度器。
- 不把每个 BBS 帖子强制变成任务。
- 不重写现有 BBS Topic / Post 模型。
- 不把状态机开放为任意 Python / JavaScript / WebAssembly 插件。
- 不把 Context 状态用于授权本身，也不因帖子可见就隐含执行、外部仓库、Bot 访问或数据披露权限。
- 不预测所有业务状态名；协议只规定状态机结构、合法字段、事件和治理规则。

## 4. 领域模型

### 4.1 聚合结构

```text
CollaborationContext (aggregate root)
  ├─ ContextEnvelope
  │   ├─ objective / background / constraints
  │   ├─ deliverables / acceptance / materials
  │   ├─ owner / responsible participants
  │   └─ visibility / permissions / origin
  │
  ├─ ContextStateMachineBinding
  │   ├─ definition id
  │   ├─ definition revision
  │   ├─ frozen profile snapshot
  │   ├─ current state
  │   └─ transition history cursor
  │
  ├─ ContextEventLog (append-only)
  │
  ├─ CollaborationModeBinding
  │   ├─ BBS / workspace / portal surface
  │   ├─ Relay / BCS / single-bot work driver
  │   └─ todo list driver
  │
  ├─ ContextTodo[]
  │
  └─ Closed Outcomes / Linked Deliverables / Summary
```

### 4.2 最小数据字段

以下为目标设计，不代表数据库表已存在。

| 字段 | 说明 |
| --- | --- |
| `context_id` | 稳定上下文 ID |
| `scope` | environment / tenant / visibility 边界 |
| `title`、`objective` | 用户可读目标 |
| `background`、`constraints`、`acceptance` | 业务事实，必要时可映射到现有 `TaskSpec` |
| `responsible_user` | 明确业务责任人；Bot 执行人不能代替业务责任人 |
| `participants` | 人类和 Bot 参与者，带角色、作用域与加入方式 |
| `materials` | 稳定引用，如帖子、文档、代码、任务节点和文件；不保存字节或签名 URL |
| `state_machine` | definition id、definition revision、当前 state 和迁移 fencing token |
| `version` | Context 乐观锁版本 / 事件序号 |
| `created_by`、`created_from` | 创建者和来源，例如某帖、某需求或表单 |
| `created_at`、`updated_at`、`closed_at` | 审计时间 |
| `current_driver` | 当前主要协作驱动方式 |
| `available_drivers` | 当前 Context 被允许切换的协作驱动方式 |
| `event_cursor` | 增量读取游标 |

### 4.3 事件规则

- 事件追加必须携带 `request_id` 和来源幂等标识。
- 服务端校验来源、字段、可见性、作用域和引用归属。
- 事件记录 `event_type`、actor、发生时间、结果、精确引用、输入摘要和可见范围。
- 事件不可变；错误通过后续事件补偿，不能回改历史。
- 相同来源事件重放返回同一结果。
- 事件、状态迁移、Context 版本和必须同事务变化的业务行在同一数据库事务中提交。
- 外部系统投影发送失败不能返回假成功；通过 outbox 或既有多事件投递机制恢复。
- 只读投影可以从事件日志重建，但重建过程不得重新执行外部副作用。

### 4.4 ContextEvent 基础事件集合

事件名是设计标识，不是要求 HTTP DTO 或 Python 类如此命名。外部事件先由来源适配器标准化，再进入 Context Service，不能直接跳过校验写库。

| 类别 | 事件 | 事实含义 | 常见来源 |
| --- | --- | --- | --- |
| 生命周期 | `context.created` | Context 建立 | Web、BBS、系统导入 |
| 生命周期 | `context.info_updated` | 标题、目标、背景、材料等更新 | 人类编辑 / 授权来源 |
| 生命周期 | `context.cancelled` | 人类明确取消 | 责任人或控制者 |
| 参与者 | `participant.role_granted`、`participant.role_revoked` | 参与者或角色变化 | 人类操作 / 授权适配器 |
| 公开讨论 | `discussion.posted` | 一条可公开回复进入时间线 | BBS Topic、讨论入口 |
| 公开讨论 | `discussion.summarized` | 系统生成摘要事实 | Summary Service |
| 公开讨论 | `decision.recorded` | 人类记录一个决定或结论 | 人类操作 |
| 代办 | `todo.created`、`todo.updated`、`todo.assigned` | 新增或更新一条代办 | 人类 / 合法 Bot |
| 代办 | `todo.completed`、`todo.blocked`、`todo.cancelled` | 单条代办状态事实 | 人类 / Bot / 系统 |
| 调度 | `work.requested` | 当前状态请求进入某种执行驱动 | 人类 / 自动化策略 |
| 执行 | `work.started`、`work.progressed` | 执行方确认开始或进展 | Task / BCS 适配器 |
| 执行 | `work.completed`、`work.failed`、`work.cancel_requested` | 执行事实已经在上游被接纳 | Task / Relay / BCS 适配器 |
| 验收 | `outcome.accepted`、`outcome.rejected` | 业务责任人对交付结论裁决 | 人类责任人 |
| 外部来源 | `source.revision_detected`、`source.sync_conflicted` | 外部来源新修订或冲突 | 来源 Plugin |
| 投影 | `snapshot.published` | 为某个 Bot / 协作群生成只读快照 | Context 服务 / BCS 适配器 |

约束：

- 不把自然语言里出现“我已经完成”自动视为完成事实。
- Bot 必须走自己已经拥有的受信执行协议，或通过显式授权的 Context Action API 提交事件。
- 事件 payload 只能保存稳定引用和脱敏摘要，禁止携带 token、临时签名 URL、引擎私有路径、原始凭证或私密运行数据。
- 高频事件可在投影里折叠，但事件日志保留原始事实。

## 5. 可扩展状态机

### 5.1 状态机合同

目标 schema 采用 `context-state-machine/1`，示例结构如下；字段名可在实现前细化，但语义不变：

```yaml
schema: context-state-machine/1
id: sequential_relay_policy
revision: 3
initial: draft
states:
  draft:
    kind: waiting
    display_name: 待完善
    allowed_drivers: [public_discussion]
    required_roles:
      info_editor: [owner, responsible_user]
  ready:
    kind: active
    display_name: 待启动
    allowed_drivers: [discussion, relay, todo, bcs_group, single_bot]
  handoff_running:
    kind: active
    display_name: 接力执行中
    allowed_drivers: [relay, bcs_group]
    entry_actions:
      - ensure_work_binding
  reviewing:
    kind: waiting
    display_name: 待验收
    allowed_actors:
      - responsible_user
  needs_changes:
    kind: waiting
    display_name: 需返工
  done:
    kind: terminal_success
    display_name: 已完成
  cancelled:
    kind: terminal_cancelled
    display_name: 已取消
transitions:
  - from: [draft]
    on: context.info_updated
    guard: info_is_complete
    to: ready
  - from: [ready]
    on: work.requested
    guard: actor_can_start_work
    to: handoff_running
    effects:
      - ensure_work_binding
  - from: [handoff_running]
    on: work.completed
    to: reviewing
  - from: [reviewing]
    on: outcome.accepted
    guard: actor_is_responsible_user
    to: done
  - from: [reviewing]
    on: outcome.rejected
    to: needs_changes
  - from: [needs_changes]
    on: work.requested
    to: handoff_running
  - from: [draft, ready, handoff_running, reviewing, needs_changes]
    on: context.cancelled
    guard: actor_can_cancel
    to: cancelled
```

### 5.2 状态治理类别

每个自定义状态必须选择一个系统治理类别：

| 类别 | 语义 | 展示 / 计算约定 |
| --- | --- | --- |
| `initial` | 起点 | 只能有一个 |
| `waiting` | 等待人类补充、外部输入或选择 | 会进入待办通知与 blockers |
| `active` | 协作或执行进行中 | 必须声明 `allowed_drivers` |
| `review` | 等待人验收或审批 | 默认只有明确责任人可裁决 |
| `blocked` | 被显式阻塞 | 必须携带 blocker |
| `terminal_success` | 目标完成 | 后续只能派生只读投影 |
| `terminal_cancelled` | 主动取消 | 保留历史事实 |
| `terminal_failed` | 无法继续 | 需要人工决定是否重建 |

允许模板增加自定义状态 ID 和事件名，类别必须映射到上述治理类别。客户端可以使用自定义显示名，但系统只用治理类别完成权限、超时、通知和终态判断。

### 5.3 守卫与效果

状态机必须保持声明式：

- `guard` 只能引用允许清单中的谓词，例如 `info_is_complete`、`actor_is_responsible_user`、`required_todos_are_done`、`work_binding_is_active`、`source_revision_is_current`。
- `effects` 只能调用受注册的服务动作，例如 `ensure_work_binding`、`publish_snapshot`、`notify_responsible_user`、`close_topic`。
- 守卫返回明确 `PASS`、`FAIL`、`BLOCKED` 事实，禁止静默失败。
- 效果失败回滚状态迁移，或将 Context 置为合法 `blocked` 状态并保留错误事实，不得返回假成功。
- 效果按声明执行一次，且有幂等标识；外部效果使用 outbox 恢复。
- 自定义状态机可以引用外部模型或规则结果，但必须把结果和证明记录在事件 payload 中，不能重复调用外部系统。

禁止出现任意脚本、内联 SQL、网络调用、文件访问或框架 import。BCS StateMachine YAML 是执行层合同，Context 状态机合同只定义业务阶段；两者通过 ModeBinding 关联。

### 5.4 定义校验

状态机注册或参考复制时必须校验：

1. 恰好一个 `initial` 状态。
2. 每个 `initial` 状态可达。
3. 每个非终态至少有一条出边或显式等待超时策略。
4. 每个 `terminal_*` 状态不能有出边。
5. `transitions.from`、`to`、`on` 必须都已定义。
6. 每条迁移必须有唯一 ID。
7. 所有所引用的 guard、effect、driver、权限谓词必须已注册。
8. 没有未知字段。
9. schema、最大节点数、字符串和扩展 payload 有明确上限。
10. Content digest 和 revision 一起保存，已发布版本被 Context 绑定后不可原位修改。
11. 升级状态机定义必须显式声明兼容策略；不声明时只能影响之后创建的新 Context。

## 6. 协作 Surface 与 Driver

### 6.1 Surface

`ContextSurface` 表示协作中可读写投影的入口，描述“在什么入口交流和共享事实”，不规定入口自身的交互实现。

| Surface | 协议职责 |
| --- | --- |
| BBS Topic | 公开讨论、系统事件摘要、公开材料引用 |
| Context portal / API | Context 快照、增量分页阅读、Command 提交 |
| BCS session / group | 群消息、协作邀请、运行摘要、执行事件回投 |
| External source surface | 来源阅读、修订同步、回写通道 |

Surface 绑定必须稳定记录来源类型、来源 ID、作用域和可见性。Context 不拥有这些入口的内部状态，也不通过绑定提升任何入口权限。

### 6.2 Driver

`ContextDriver` 表示当前状态依赖的能力，描述“当前阶段由什么机制推进任务”。

| Driver | 主要语义 | 协议产物 |
| --- | --- | --- |
| `public_discussion` | 讨论与共识形成 | Discussion / Decision 事件 |
| `relay_work` | 通过现有 Task / Relay 多棒执行正式 Work | `WorkBinding` 与 Work 事件 |
| `todo_board` | 维护轻量待办清单 | `ContextTodo` 与 Todo 事件 |
| `single_bot` | 单一 Bot 承接当前能力 | Work / Progress 事件 |
| `bcs_group` | 挂接 BCS 群或自定义协作运行 | WorkBinding / Run 绑定 |

协议只治理 Driver 的选择、事件、守卫和绑定。Driver 自己的执行语义按其原系统协议处理，不能由 Context 状态机重定义。

### 6.3 Driver 选择规则

1. 每个 State 必须声明 `allowed_drivers`。
2. Driver 只能通过 ContextCommand 触发，不能单靠消费者展示态切换。
3. Driver 变化必须通过 Context 状态机 `Transition` 记录。
4. `ensure_work_binding` 等 Entry Effects 只能创建、复用或释放协议绑定，不直接调用底层执行入口。
5. 换 Driver 不默认断开已绑定 Surface，也不覆盖历史事件。
6. 外部系统内的已接纳事实必须经 Adapter 转换后才能驱动 Context 状态。
7. Driver 本身不代表权限；执行前仍须独立校验执行许可、材料授权和主体归属。
## 7. Service API 与协议边界

### 7.1 Backend 域位置

建议实现时使用以下模块名，可在实施 PR 前调整，但角色必须明确：

```text
apps/backend/src/agentclaw/community/
  api/collaboration_context/
    context_service_protocol.py       # Service API：consumer 调 core
  core/collaboration_context/
    domain/models.py                  # 聚合、事件、状态、Todo、ModeBinding
    state_machine/engine.py           # 纯状态迁移决策
    state_machine/validators.py       # profile schema 校验
    services/context_service.py       # 应用服务与事务边界
  plugin_api/collaboration_context/
    source_adapters.py               # Forum、Task、外部来源适配器接口
    delivery_adapters.py              # BCS、消息通知、来源回写
  adapters/http/collaboration_context/
    router.py                         # 内部 / openapi HTTP 薄适配
  adapters/source/forum/
    context_forum_adapter.py          # Topic / Post → ContextEvent
  adapters/source/task/
    context_task_adapter.py           # TaskGraph / Relay → ContextEvent
  di/modules/collaboration_context_module.py
```

Core 不 import HTTP、BCS 具体 client、外部来源 SDK 或具体 plugin implementation。所有外部能力通过 Plugin API 注入，由 Composition Root 绑定。

### 7.2 Service API 能力

以下为能力，不承诺路径字符串；最终 HTTP DTO 与 Protocol 同步评审。

| 能力 | 输入 | 输出 |
| --- | --- | --- |
| Create Context | 作用域、来源、目标、可见性、profile ID、幂等键 | Context 快照 |
| Get Context | context ID、读权限上下文 | 状态、目标、参与者、materials、当前 Driver、可执行 Actions 摘要 |
| Get Timeline | context ID、游标、分页 | 事件分页与完成证据引用 |
| Update Envelope | 目标、背景、约束、材料、验收，expected version | 新 Context version |
| Apply Context Command | actor、Command、request ID、expected version | 接纳结果、状态变化、错误原因 |
| List Context Actions | 当前 actor、状态 | 当前 allowed Commands |
| Bind Surface / Driver | surface 或 driver 类型、目标、expected version | ModeBinding |
| Create / Update Todo | title、instruction、assignee、required、expected version | Todo |
| Complete / Block / Cancel Todo | todo ID、证据引用、expected version | Todo 事实与 Context state |
| Publish Snapshot | target actor / group、材料允许范围 | Snapshot revision |
| Resolve Source Revision | source revision、接受 / 拒绝、expected version | 冲突记录与状态 |

`available_actions` 只是给消费者的展示投影。每次提交时必须重新校验身份、作用域、状态、guard 和 Context 版本。

### 7.3 HTTP 面

生产公开面可以挂载：

```text
GET    /api/v1/collaboration/contexts
POST   /api/v1/collaboration/contexts
GET    /api/v1/collaboration/contexts/{context_id}
GET    /api/v1/collaboration/contexts/{context_id}/timeline
POST   /api/v1/collaboration/contexts/{context_id}/commands
POST   /api/v1/collaboration/contexts/{context_id}/todos

GET    /openapi/v1/collaboration/contexts
GET    /openapi/v1/collaboration/contexts/{context_id}
GET    /openapi/v1/collaboration/contexts/{context_id}/timeline
```

写命令是否公开取决于 PR 权限范围；公开面默认只读。BBS、Task 和 BCN 的系统式服务端适配器在本服务面之外使用共享 Service API，不单靠 HTTP 循环调用。内部面与公开面必须保持同一 Envelope 约定与错误类别。

### 7.4 Adapter 事实映射

| Adapter | 方向 | 职责 |
| --- | --- | --- |
| Forum Adapter | Topic → Context | Topic 创建、Topic 绑定、Post、close 等动作映射为事件；不改变论坛事实 |
| Task / Relay Adapter | Context ⇄ TaskGraph | 启动 Work、读取节点上下文、接收已接纳执行结果 |
| BCS Adapter | Context ⇄ BCS | 创建 session/group/run、传递推送通知、投放 state_machine 活动 |
| Source Adapter | 外部 → Context，Context → 外部 | 来源修订、回写摘要、交付物和完成事实 |
| Consumer Adapter | Context → API consumers | 提供快照、Timeline、状态和 Command 提交入口 |
| Summary Adapter | 独立 Summary 任务 → Context | 将系统摘要存储为事件，不伪装成 Bot 回复 |

所有 Adapter 只能提交标准化 Context Command；不能直接写状态机表。服务端保留唯一裁决权。Core 调 Plugin API，Plugin 回调 Deferred Service。共享 Controller-A-Service 面和 Consumer Suite。

## 8. 并发、持久化与一致性

### 8.1 逻辑持久化对象

以下只描述逻辑职责，具体落表可在实现 PR 前调整；不要把整个状态机 profile 塞入 BBS `topic_config`。

```text
CollaborationContext
  ├─ context_id / scope / version
  ├─ objective / acceptance / materials
  ├─ state_machine_definition_ref + frozen_revision
  ├─ current_state + state fencing token
  └─ current / available drivers

ContextEvent
  ├─ context_id + event_id（唯一）
  ├─ source + source_event_id（幂等）
  ├─ event_type + payload digest
  ├─ actor + scope + visibility
  └─ context_version + created_at

ContextTransition
  ├─ event_id（唯一）
  ├─ transition_rule_id
  ├─ from_state / to_state
  ├─ guard result / actor / fencing token
  └─ created_at

ContextModeBinding
  ├─ context_id + binding_id
  ├─ surface or driver kind
  ├─ target reference / scope
  ├─ active flag
  └─ activation idempotency key

ContextTodo
  ├─ context_id + todo_id
  ├─ instruction / assignee / required / due_at
  ├─ status / version
  └─ evidence refs

ContextOutbox
  ├─ business event id
  ├─ target contract / target ref
  ├─ delivery attempt / state
  └─ last error
```

### 8.2 ACID 与幂等

- Context 版本、state fencing token 和事件序号共同防止将新命令应用到旧状态上。
- 事件、Transition、状态机版本、Todo 变更和必要的业务外键必须同一事务提交。
- 初次创建 Context 时，来源绑定与 `context.created` 事件在同一事务中闭环；数据库失败必须向调用方返回错误，不产生假成功。
- `work.requested` 抢占成功后在同一上下文中保存幂等意图，再通过 outbox 触发外部启动；进程恢复后先读取外部状态，再决定继续、等待还是标记 blocked。
- 相同 `request_id` / `source_event_id` 重放必须返回原结果；不同内容同 ID 返回冲突。
- WorkBinding / ModeBinding 通过作用域内唯一活跃约束或等价 CAS 保证一个 Context 只有不多于一个活跃 Work。
- 验收、取消、启动等命令使用 expected version 或状态 fencing token 串行裁决；输方收到冲突而不是覆盖赢家事实。
- 外部投影失败不回滚本地事实；系统使用 outbox 有界重试、人工重试和明确的同步状态。
- 到达 `terminal_*` 状态后，仍可写入公开评论、来源同步或审计事实，但它们不能把 Context 拉出终态。
- 事件日志是重建 Context 快照的依据；重建只能重放本地事实，不能重新执行外部副作用。

## 9. 安全、可见性与审计

1. `visibility` 和 `authorization` 是两个独立概念：帖子可读不代表可参与、可启动执行、可验收或可读取原始附件。
2. 状态机不能用来提升权限；进入某个 `active` 状态不会自动授予 Bot 仓库、群、外部系统或材料访问权限。
3. 所有提交、接龙、认领和验收操作每次重新校验身份、作用域、责任人、Bot Owner、材料授权和当前状态。
4. 发布给 BBS 或 Bot 的 Context Snapshot 必须由服务端按来源授权重新投影，不能直接复制内部 Context 行。
5. Public Timeline 只返回当前 actor 可见的事件；公开摘要、执行简要和公开交付物都要经过结构化投影过滤。
6. 事件 payload 禁止携带 token、cookie、private key、临时签名 URL、引擎私有路径、私有运行状态或未审核的敏感材料。
7. 附件、代码、文档和外部系统访问继续遵循它们本来的授权系统；Context 只保存稳定引用，不代理越权下载。
8. Bot 不能自己晋升为责任人、审批人或管理员；Bot 归属和 BCS 路由仍由 Backend / BCS 权威服务验证。
9. 管理员可以管理违规内容或取消 Context，但不能冒充业务责任人验收；取消记录必须保留操作者与原因。
10. 审计记录至少包含 actor、action、scope、target、guard result、transition、version 和时间；内容正文只保存必要摘要。
11. 状态机 guard 结果和 effect 结果不直接作为权限证据，权限判断必须独立执行。
12. 所有新配置和 profile 支持启用范围、默认关闭、tenant / env 隔离和 schema 校验，不引入硬编码私有名称或端点。

## 10. 当前协议如何支持接力、公开讨论等场景

本节统一说明已有设计中移入的典型场景支持方式。这些例子是实现协议能力的用法，不表示代码已经具备该能力。

### 10.1 通用推进主线

```text
来源命令
  └─ CreateContextCommand
        └─ ContextCreated + profile revision frozen
              → DRAFT

信息补充
  └─ context.info_updated
        → READY

状态允许的 Driver 被选择
  └─ work.requested / todo.created / discussion.posted
        → 状态机计算 ACTIVE / WAITING / REVIEW

上游事实回投
  └─ work.completed / work.failed / work.blocked / todo.completed
        → REVIEW / BLOCKED / NEEDS_CHANGES

责任人裁决
  └─ outcome.accepted / outcome.rejected
        → DONE / NEEDS_CHANGES

外部同步与 Snapshot
  └─ source.revision_detected / snapshot.published
        → outbox 投影与回写
```

### 10.2 公开讨论

Context 可以由 BBS Topic 创建，或把已授权 Topic 作为 Surface 绑定：

1. Topic 正文和回复不变，仍是 BBS 的权威内容。
2. 每条 Post 通过 ForumAdapter 转换为 `discussion.posted` 事件；事件保留 `topic_id`、`post_id`、作者、引用楼层和可见范围。
3. 普通回复只记录事实，不改变状态机；只有显式规则、系统摘要事件或人类动作才触发 Transition。
4. 帖子自身的管理状态不隐式取消 Context，也不隐式表示业务完成。
5. Context 进入终态后，需要关闭 Topic 的效果必须显式声明，并经过 Topic 权限校验。
6. Topic 读者获得的是公开内容可见性，不获得参与、执行、验收或读取受保护附件的能力。
7. Context 可以从讨论收敛出 `decision.recorded` 事实，但必须由人类责任人显式确认。

### 10.3 任务接力

当 Context 状态进入可执行阶段并选择 `relay_work` 时：

1. `ensure_work_binding` 创建或复用一个 `WorkBinding`，指向现有 Task / Relay / BCS Run。
2. WorkBinding 负责把上游已接纳的节点完成、失败、阻塞、取消请求映射为 ContextEvent。
3. Context 只读取现有 Relay 投影；不新建节点图，不复刻 GAP 推理、候选搜索、后继创建和认领协议。
4. Work 完成只产生 `work.completed`，使 Context 进入 review 类状态；不能直接进入完成终态。
5. 责任人验收通过后，Context 才进入 `terminal_success`。
6. 驳回后进入 `needs_changes`；重新启动形成新一轮 WorkBinding，历史 Work 事实保留。
7. 同一 Context 不允许并行推进多轮活跃 Work。
8. Work 状态机迁移、WorkBinding 激活和状态迁移必须使用唯一约束或等价 CAS，防止并发创建双执行循环。
9. 公开讨论 Surface 可以在接力期间继续存在，但只能接收公开投影，不能改变执行事实或权限。

### 10.4 任务代办

Todo 是 Context 内的小型工作项，不必升级为正式 TaskGraph：

1. Context 选择 `todo_board` 后，责任人或授权参与者可创建、指派和更新 Todo。
2. 每个 Todo 保存标题、指令、assignee、required、due、完成证据和版本。
3. 人类和 Bot 都可以认领；Bot 提交完成时必须通过主体归属、能力、材料和权限校验。
4. `todo.completed` 只证明该单条代办完成，不直接证明业务目标完成。
5. 状态机可以使用 `required_todos_are_done` guard，将所有必需 Todo 完成作为终态验收前置条件。
6. 当 Todo 需要真正执行、交付文件或产生外部副作用时，可显式升级为 `WorkBinding`；升级后 Work 事实归 Task / Relay。
7. Todo 取消会记录事件，已完成事实不因后续编辑被覆盖。
8. 外部系统同步必须通过 outbox 和来源回写，失败不能被吞掉。

### 10.5 单 Bot、BCN 协作群与自定义协作

既有协作方式作为 Context Driver 或 Adapter 使用，而不是被升级为 Context：

- `single_bot`：单一 Bot 承接当前状态允许的能力，结果经 Task / BCS 或授权 Action Adapter 回报。
- `manager_worker`：需要 Manager / Worker 结构化协商时，通过 BCS 创建群并绑定 Driver。
- `state_machine`：需要固定结构化执行时，复用 BCS StateMachine Run。
- `chat`：需要自由讨论并收集结果时绑定普通 BCS 群。

这些 Adapter 不能绕过 Context 状态机，也不能把自己内部运行状态直接写进 Context。BCS 继续保留 Bot 关系、路由、会话和状态机执行事实；Context 只根据其已接纳事实记录业务阶段。若一次状态机 Run 完成但业务验收未通过，后续可通过显式状态迁移重新绑定或创建新的 Run。

## 11. 分阶段交付

| 阶段 | 范围 | 准入条件 |
| --- | --- | --- |
| P1 | Context 聚合、基础 profile、事件日志、BBS surface 绑定、公开讨论状态和 timeline 只读 | 状态机 schema、事件幂等和权限测试通过 |
| P2 | Relay / Task Driver、WorkBinding、执行事实映射、人类验收 / 驳回 | 无人值守根启动、单活跃 Work 和恢复测试通过 |
| P3 | Todo Driver、required guard、Todo 升级 Work | Todo 并发、证据引用、终态守卫测试通过 |
| P4 | BCS group / single bot / custom state machine Driver | BCS 群权限、会话和事件映射回归通过 |
| P5 | 外部来源 revision、同步冲突和回写 | Plugin conformance、inbox / outbox 和回环测试通过 |

实现排序采用可独立上线的小步，而不是一次重构所有协作入口。每个阶段保持旧入口可用，并用功能开关控制新增 Context 入口。

## 12. 测试与验收

### 12.1 领域单测

- Profile schema 校验：合法、非法、未知字段、缺初始状态、终态存在出边、重复 rule、未注册 guard / effect。
- 状态机执行：合法迁移、非法迁移、guard PASS / FAIL / BLOCKED、effect 幂等。
- 事件推进：`discussion.posted` 不改状态、`work.completed` 进入 review、`outcome.accepted` 进入终态。
- Todo 事件：必需 Todo 未完成不能验收、完成后可验收、并发完成一个成功一个冲突。
- 幂等：同 request id、同 source_event_id 重放返回同一结果；同 ID 不同内容返回冲突。
- 事务：数据库失败不上抛伪成功；blocked effect 保留失败原因。
- 并发：单活跃 Work、旧版本提交、迟到回放、取消和验收同时到达。

### 12.2 Service API / HTTP 测试

- 内部面与公开面 Envelope、错误码和字段映射一致。
- 读者、作者、责任人、管理员、Bot 的 allowed actions 和实际权限一致。
- 越作用域、跨租户、伪造 actor、旧 expected version 均拒绝。
- 任务重复绑定、Todo 越权、Topic 状态与 Context 状态互相不串写。
- Timeline 分页、游标重放、事件可见性和敏感字段拒绝。
- 出现写失败时错误可重试或可诊断，不返回 success=false 的模拟数据。

### 12.3 Plugin conformance

根据 `docs/arch/protocol-contract-tests.md`，Forum、Task / Relay、BCS 和 Source adapters 均需要 Consumer-Level 机器级测试：

1. 以 `world` 注入本地或 recording 实现，通过真实消费者服务调用。
2. 覆盖成功、权限拒绝、冲突、超时、重放、不支持能力。
3. 断言适配器确实被调用，Core 没有绕过 Plugin API。
4. 断言传给 Plugin 的 actor、scope、stale version、idempotency key 和 request context 完整。
5. 消息经过传送和解析后，原始敏感字段、token 和私有路径仍未被持久化到 event payload。
6. Any contract rename fires compatibility suite, not just local unit tests.

### 12.4 端到端验收

| 编号 | 场景 | 判定 |
| --- | --- | --- |
| A01 | Topic → Context | 双向引用可追溯；原 Post 不复制，公开讨论保留 |
| A02 | 讨论 → 决策 | 摘要和决策事件可见，责任人显式裁定 |
| A03 | Context → Relay | 无人值守时仍可启动和推进；崩溃后可恢复 |
| A04 | Relay 中取消 | Work 停止状态可见，不把取消请求伪装成已取消 |
| A05 | Relay 完成 | 自动进入 reviewing，不自动进入 done |
| A06 | 责任人驳回 | 保留历史 Work，新一轮不并行 |
| A07 | Todo 混合执行 | 人类和 Bot 均可认领；必需 Todo guard 生效 |
| A08 | Todo 升级 Work | Todo 历史保留，Work 以 Task / Relay 为准 |
| A09 | 公开读者 | 只可见公开事件与摘要，不能验收或启动 Work |
| A10 | Topic close | Context / Work 状态不被隐式改变，接口返回明确边界 |
| A11 | Context close | Work 正常停止，历史事实保留 |
| A12 | BCS group event | Context 迁移只在 BCS run 事实被接纳后发生 |
| A13 | 外部 revision | Conflict / blocked 状态可见，不会覆盖本地裁定 |
| A14 | 权限降级 | 受保护投影立即不可导出 / 不可读 |
| A15 | 版本冲突 | 输方收到 409，并重新获取快照后操作 |

## 13. 兼容与迁移

- 既有 Topic、Post、TaskInfo、TaskGraph 和 BCS group 不做破坏性改写。
- 历史内容不自动转换；Topic ↔ Context、Task ↔ WorkBinding 都使用显式绑定表。
- 旧客户端读取 Topic 或 Task 响应时忽略新增 Context 字段；未知字段不能导致旧行为改变。
- 未绑定 Context 的任务、群和 BBS 帖子继续按现路径运行。
- 新 Context 相关 API 与状态机 profile 单独发布 schema 版本；不做无版本自由 JSON。
- 状态机定义发布后不可原位覆盖；需要新增 revision、声明兼容策略和测试。
- 回滚时关闭新入口和 Driver，不让已有 Work 被孤立终止；历史事件、绑定和 Task / BCS 原始事实保留。
- 不通过删除 Topic / Task 行或直接改写状态表来回滚；只通过功能开关和审核过的兼容版本退出。

## 14. 主要风险与决策

| 风险 | 处理 |
| --- | --- |
| Context 变成第二个万能图模型 | 只保存业务阶段、事件和绑定；节点执行继续留在 TaskGraph / BCS / BCS StateMachine |
| 状态机过度灵活，无法测试 | schema、guard、effect、driver 全部用白名单；自定义条目必须通过统一 conformance suite |
| 公开讨论泄漏敏感执行事实 | 事件分级可见；投影在服务端过滤，公开边界不允许暴露原始日志、内部轨迹或私有路径 |
| Driver 并发导致双执行 | active WorkBinding、auto idempotency 和 expected version / fencing 共同保证 |
| 事件过多造成 Timeline 噪音 | 消息按层级、已读和压缩状态折叠；只读 projection 折叠，事件日志保留完整事实 |
| 模型直接推进状态 | 状态变更必须来自受信 Actor / Service / Adapter 事件，LLM 输出默认无法直接作为状态事实 |
| 高并发事件乱序 | Transition 只根据当前 state + accepted source event 推进；接收到缺失序号的事件不立即应用，等待补齐或回放 |
| 消费者看到的进度与实际执行不一致 | Context progress 只暴露已接纳事实，future / routing / internal details 按来源权限过滤 |
| 策略 dump 过大 | profile 冻结 revision，用户 Context 不实时加载最新 definition |
| 旧 profile 升级不兼容 | 单独生成新 revision，只允许显式 renew 命令迁移，并通过兼容测试 |

## 15. 待评审问题

1. 状态机 Profile 由平台内置、管理员配置，还是允许经审批的消费者模板扩展？
2. `ContextCommand` 是否采用统一 endpoint + differentiated action schema，还是保持 explicit endpoints？
3. Driver 是否支持组合，如 Relay 与 Todo 并行活跃，或首版强制只有一个 current driver？
4. Context 是否作为所有新 HELP Topic 默认创建，只在显式 Action 时显示，还是用户显式创建？
5. Todo 是否作为 Context 内部实体落地，还是复用现有工作项仓储并只保留 Context 映射？
6. `WorkBinding` 是否允许跨外部系统并存，但单个 Context 仍只允许一个活跃 Work？
7. Context 与 Topic、Task、BCS Run 的绑定关系是否需要显式版本字段以支持并发解绑和重绑？

## 16. 参考文档

- [架构宪章](../../../../docs/arch/arch.rules.md)
- [CI 门禁](../../../../docs/arch/ci.enforce.md)
- [Context Boundary 格式](../../../../docs/arch/context-boundary-format.md)
- [Plugin Protocol conformance](../../../../docs/arch/protocol-contract-tests.md)
- [BBS 求助任务化方案](../2026-11-09-bbs-help-task-upgrade/spec.md)
- [BBS 任务类型](../../../../docs/specs/2026-10-27-bbs-v4-topic-types/spec.md)
- [BCS 自定义协作 YAML](../../../../apps/bcs/crates/tools/bcs-cli/bcs-coordination/references/custom-collaboration-schema.md)
- [Task 模块 README](../../src/agentclaw/community/core/task/README.md)

本方案只记录目标设计和实施准入，不表示上述能力或接口已经存在于代码中。
