# 基于上下文自主协作协议 v2 设计方案（ACCS 按需采用版）

- 日期：2026-11-09
- 修订：2026-11-11（去除全部幂等键与 event 表的 request_id/sequence_no/is_trunk 列；主干改为查询时活动链推导；collet 新增创建主体引用列 actor_ref 并放宽带值类型；current_snapshot 与 event 的 actor_ref/visibility/payload/primitive_type/context_version_after 列放宽为可空）
- 状态：Draft / 待评审，尚未实现
- 主题：基于上下文（Context / Collet）的自主协作
- 代码落点：`apps/backend/src/agentclaw/community/core/task/context_collab/`
- 参考架构：ACCS L1-L6 与 G1-G4

> 本版不是把 ACCS 文档中的全部概念对象搬入本方案，而是只选择“基于上下文自主协作”实际需要的对象。没有进入当前运行链路的对象，明确列为外部依赖、未来扩展或不引入；不为保持名义上的层次完整而创建空模型。

## 0. 方案结论

本次只为当前三个 API 提供最小持久化闭环；领域对象按需完整保留，但不为每个对象新增独立表或独立外部生命周期：

```text
三个 API
  ├─ POST 创建协作上下文
  ├─ POST 新增协作上下文事件
  └─ GET 查询协作上下文最新状态
       │
       ├─ L4 Collet：保存上下文和当前任务快照
       └─ G2 Event record：保存追加事件与 parent_event 因果链（不承载幂等键）

北向 Service API / HTTP / OpenAPI Delivery（不属于 L1-L6）：面向前端提供三个接口
L1：协议交互归一化，只接收 Delivery 转换后的协议 DTO，不承载前端 API
L2/L3/L5/L6：恢复为领域对象、值对象或受限枚举；不新增独立持久化表和外部注册表
G1：授权与可见性校验
G3：由 Collet + Event record 组合查询响应
G4：字段、版本和状态值校验
```

本版真正需要新增的持久化对象只有两个：

1. **`Collet`**：一次上下文自主协作的唯一记录，保存 `collab_context_id`、创建参数和当前任务进度快照。
2. **Event record（本地持久化记录）**：一次被接纳的协作事件，承载 ACCS `EventEnvelope`，保存 `event_id`、`parent_event`、事件类型和 `payload`。

`Task`、`Message`、`Participant`、`ResourceBinding`、`MethodBinding`、`Activity`、`Artifact` 等领域对象恢复保留，但不新增独立表；它们分别作为领域对象、值对象、快照字段、事件语义或外部引用存在。GET 只返回由两张表组合出的查询响应。

本版只新增两类持久化事实：`Collet` 聚合快照和 Event record 事实日志；这与领域对象是否保留是两个独立决策。

## 1. 背景、目标与边界

### 1.1 要解决的问题

现有单 Bot、BCS 群、BBS 讨论和 Task / Relay 各自拥有消息、运行或任务视图，无法用同一个上下文回答：

- 本次协作的目标是什么？
- 当前责任主体和执行 Agent 是谁？
- 任务已经做过哪些事情、当前到了哪一步？
- 哪些消息、活动、决策和产物构成了当前任务的事实链？
- 外部执行系统的状态与协作上下文的状态，谁负责什么？

### 1.2 产品目标

- 以 L4 `Collet` 统一一次自主协作的上下文边界。
- 以 L2/L3 引用身份、能力、空间和资源，不复制已有身份或资源系统。
- 以 L5 `Open + Group` 支持 BBS 公开讨论，以 L5 `Single` 支持指定 Agent 执行，并为后续方法扩展保留 L5 位置。
- 以 `Collet` 快照和 Event record 让用户看到任务进度、证据和事件 DAG。
- 以 G1-G4 处理授权、可靠性、观察投影和状态一致性。
- 保留以下三个 API 路径及字段兼容要求：`collab_context_id`、`parent_event`、`trunk_events`。

### 1.3 非目标

- 不替代 BCS、TaskGraph、Relay 或外部执行系统的运行时和生命周期。
- 不把 BBS Topic、BCS group、外部 Task 直接变成 L4 核心对象。
- 不在本域复制用户、Bot、Agent Card、能力注册、Space 成员或资源正文。
- 不把 `Capability` 当成授权，不把 `Participant` 当成权限，不把查询响应当成权威状态。
- 不在 `plugin_api`、`task/forum`、`task/bbs` 下新增或修改实现。
- 当前只引入两个产品场景实际需要的 L5 方法：BBS 使用 `Open + Group`，团队代办使用 `Single`；`Swarm` 及 G3 演进对象暂不引入。

## 2. 按需采用 ACCS 层次与对象

### 2.1 L1-L6 选择结果

下表严格使用 ACCS《Avernet ACCS》中的正式概念对象名。只有当前三个 API 和两个产品示例真正需要的对象进入本方案；没有必要的对象不为了“凑齐层次”引入。

| 层 | ACCS 正式概念对象（本方案按需采用） | 本方案中的使用方式 | 本次不引入 / 不复制 |
| --- | --- | --- | --- |
| L1 `Interoperate and Deliver` | `Agent Card`、`a2a.Message`、`a2a.Task`、`Part`、`a2a.Artifact` | 仅作为指定 Bot 的互操作和结果交付桥接；三个北向 Service API 本身不属于 L1 | 不把 `ContextCreateCommand`、`ContextEventEnvelope`、`ContextStatusQuery` 冒充 ACCS L1 对象；它们是本方案 Service API DTO |
| L2 `Define the Agent Network` | `Principal`、`Agent`、`Capability`、`Delegation` | `principal_ref`、`actor_ref`、`executor_ref` 分别映射到 Principal / Agent 的对象引用；能力和委托只引用外部权威 | `Relation` 当前没有进入三个 API 的裁决链，不新增本域 Relation 模型；不复制 Principal / Agent / Capability 注册表 |
| L3 `Model Spaces and Resources` | `Space`、`Resource`、`Membership`、`Grant`、`Policy Authority` | `space` 和 `resource_refs` 使用 Space / Resource 引用；G1 通过已有 Membership、Grant、Policy Authority 做授权计算 | 不在本域新建 Space、Resource、Membership、Grant 或 Policy Authority 表 |
| L4 `Define Collaboration Context and Primitives` | `Collet`、`Participant`、`RoleBinding`、`Task`、`Activity`、`Decision`、`Message`、`Artifact`、`ResourceBinding`、`Requirement`、`Query` | Collet 是协作上下文根；Participant/RoleBinding 表示主体和指定执行角色；Task/Activity 表示任务与进度；Decision 表示裁决；Message 表示用户互动；ResourceBinding/Artifact 表示输入和交付；Query 生成 GET 查询 | 不新增平行的 `CollaborationContext`、`ContextParticipant`、`ContextTodo`、`ContextEnvelope`、`ContextSnapshot` 等 ACCS 外对象名；Event record 只作为本地事件持久化记录，不是 ACCS L4 概念 |
| L5 `Compose Primitives into Methods` | `Single`、`Group`、`Open`、`MethodBinding` | BBS 公开讨论采用 `Open` + `Group` 的 MethodBinding；团队代办采用 `Single` 的 MethodBinding | 当前不采用 `Swarm`；不新增 `CollaborationMode`、`CollaborationModePolicy` 或独立 Lease 生命周期 |
| L6 `Compose Methods into Products` | `ProductProfile`、`MethodComposition`、`ScenarioRole`、`DomainProfile`、`WorkflowProfile` | `public_discussion`、`team_todo` 是 ProductProfile / WorkflowProfile 的实例值；产品角色通过 ScenarioRole 映射到 L4 RoleBinding | 不创建 `ProductKind`、`WorkflowKind` 这类平行类型；不新建 ProductProfile 注册表，只保存当前 Profile 引用 / 版本 |

**命名纠正规则：** 羽雀文档中已有正式对象必须直接使用正式名；只有 Service API DTO、数据库持久化记录和查询响应这类本方案边界对象才可以使用本方案名称，并且不能伪装成 ACCS 概念对象。

L1-L6 是协议栈层次和对象边界，不是前端 Service API 的目录归属，也不是“每一层都必须创建一组持久化表”。北向 API Delivery 位于 L1-L6 之外；当前两个权威事实仍只落在 `Collet` 与事件记录两张表中。

### 2.2 G1-G4 选择结果

| 治理面 | ACCS 正式概念对象（本方案按需采用） | 本方案中的使用方式 | 本次不引入 / 不复制 |
| --- | --- | --- | --- |
| G1 `Secure and Govern` | `Authentication`、`Authorization`、`Policy`、`Audit` | 创建、追加、查询前进行身份、授权、策略和审计校验；事件 `visibility` 只能收窄已有授权范围 | 不在本域复制认证、授权、策略或审计权威表 |
| G2 `Keep Reliable and Live` | `Idempotency`、`Recovery`、`Reliability Signal` | 事务失败恢复、版本冲突和事件 DAG 可靠性信号（幂等责任移交给调用方，见 §9.2） | 当前不引入 `Retry`、`Lease`、`Heartbeat` 等独立运行对象；`expected_version` 不是 G2 自定义类 |
| G3 `Observe, Evaluate and Evolve` | `Observation`、`Outcome` | 当前仅保留可选的执行观察和验收结果引用；GET 的 `task_progress` 是查询投影，不是 G3 权威对象 | 不引入 `Metric`、`Candidate`、`Verification`、`Experiment Ledger` 的独立生命周期 |
| G4 `Ensure Contracts and Consistency` | `Schema`、`Version`、`State Machine`、`Conformance` | 请求/事件 schema、对象版本、状态迁移和合同测试；内部状态迁移记录只是 G4 实现细节 | 不创建独立 StateMachine、Transition Record、RuntimeBinding 表 |

### 2.3 本方案必要的新增工程对象

当前三个 API 只新增以下两个持久化事实和一个查询响应 DTO；领域对象本身按第 3-5 节完整保留：

1. **`Collet`**：保存创建参数和最新状态。
2. **Event record**：保存不可变事件事实和事件 DAG 关系，并承载 ACCS `EventEnvelope`。
3. **GET 查询响应**：实时组合出的状态和进度 DTO，不持久化。

字段归属如下：

- L2/L3/L5/L6 的引用和枚举：放入 `Collet.context_spec` 或 `Collet.current_snapshot`。
- 用户互动、执行进度、阻塞、交付和验收：放入 Event record 的 `event_kind` 和 `payload`；记录承载 ACCS `EventEnvelope`。
- `parent_event`、`trunk_events`、`expected_version`：分别是事件字段、查询参数和写入并发参数。
- 不为上述领域对象再拆出独立持久化表、独立生命周期或独立 Repository。

## 3. L1-L6 映射后的领域对象模型

本节以羽雀文档的正式概念对象为准。此前版本把业务实现类名误写成 ACCS 层对象，本版明确拆开：ACCS 对象使用正式名称；本方案的 Service API DTO、事件持久化记录和查询投影只在边界处保留本方案名称。

### 3.1 L1：Interoperate and Deliver

L1 只负责 Agent 互操作和结果交付，不拥有 L4 `Collet` 的协作状态。当前指定执行场景中，L1 仅在需要调用 Bot 或交付结果时使用：

- `Agent Card`：读取被指定 Agent 的端点和可调用能力声明；本域不复制 Card。
- `a2a.Message`：把 L4 `Message` 的协作内容交付给 Agent；二者不是同一个权威对象。
- `a2a.Task`：表示一次远程调用生命周期；不等同于 L4 `Task`。
- `Part`、`a2a.Artifact`：承载线上交付内容和结果；不等同于 L4 `Artifact`。

三个北向接口的 `ContextCreateCommand`、`ContextEventEnvelope`、`ContextStatusQuery` 是本方案的 Service API DTO，位于 L1-L6 之外，不能作为 L1 ACCS 类名。

### 3.2 L2：Define the Agent Network

本方案只保存 L2 对象引用，不复制身份和能力注册表：

| ACCS 对象 | 在本方案中的取值 / 用途 |
| --- | --- |
| `Principal` | `principal_ref`、`actor_ref` 的人类、组织或系统责任主体 |
| `Agent` | `executor_ref` 指向的指定执行 Bot；Bot 必须能回溯到 accountable Principal |
| `Capability` | 任务的能力要求和 Bot 的能力声明；能力匹配不等于授权 |
| `Delegation` | Agent 代表 Principal 执行时的委托链；由外部身份/授权系统提供 |

`Relation` 当前不进入三个 API 的裁决链，因此不在 `context_collab` 中新建平行 Relation 模型。

### 3.3 L3：Model Spaces and Resources

- `Space` 是 Context 的治理归属边界；接口中的 `space` 直接映射 Space 引用。
- `Resource` 是 Topic、代码仓库、文档等外部资源的权威对象；接口中的 `resource_refs[]` 只保存 Resource 引用和版本。
- `Membership`、`Grant`、`Policy Authority` 是 G1 授权计算的既有输入，本域只读取引用和授权结果。
- 资源进入 Collet 时使用 L4 `ResourceBinding`，不把 Resource 正文复制到 Collet。

因此，原先的 `ContextScope`、`InputSource`、`ContextMaterialReference` 不再作为 ACCS 类名；它们分别改由 `Space`、`Resource` 和 `ResourceBinding` 表达。

### 3.4 L4：Define Collaboration Context and Primitives

L4 是本方案的核心。当前三个 API 按需使用以下正式对象：

| ACCS 对象 | 本方案中的职责 | 是否新增独立表 |
| --- | --- | --- |
| `Collet` | 一次有界上下文自主协作的聚合根，保存 objective、Space/Resource 引用、当前任务快照和版本 | 否；落在 `ac_context_collab_collet` |
| `Participant` | Principal 或 Agent 在 Collet 内的协作身份 | 否；作为 Collet JSON / 查询对象 |
| `RoleBinding` | 把 `responsible_user`、`selected-agent` 等协作角色绑定到 Participant | 否；作为 Collet JSON / 事件事实 |
| `Task` | 团队代办和当前要完成的工作项；状态表达 `PROPOSED/READY/ACTIVE/BLOCKED/COMPLETED/...` | 否；作为当前快照 / 事件 payload |
| `Activity` | 一次真实发生的互动、执行尝试或进度活动 | 否；事件记录中的活动语义 |
| `Message` | 用户新增互动消息；`payload.content` 是正文 | 否；事件记录中的消息语义 |
| `Artifact` | 代码、文档、PR 等输入、证据、草稿或交付物的协作投影 | 否；只保存 ResourceBinding / 外部引用 |
| `ResourceBinding` | 把 L3 Resource 带入 Collet 并保留授权快照引用 | 否；写入 Collet `context_spec` |
| `Requirement` | Task/Activity 所需能力与约束；可选，不替代 G1 Authorization | 否；写入 Task / 快照 JSON |
| `Query` | GET 接口的无副作用查询对象，负责可见性和 `trunk_events` 过滤 | 否；实时构造 |

#### 3.4.1 EventEnvelope 与本地事件记录

ACCS 公共合同中的 `EventEnvelope` 用于封装被接纳的事实，字段包括 `event_id`、`event_type`、`event_version`、`aggregate_ref`、`aggregate_revision`、`causation_id` 和 `payload`。本方案的 `parent_event` 是事件 DAG 的本地因果关系字段，可映射到 `causation_id`，但为了兼容既有三个 API，仍在 API 和数据库中保留字段名 `parent_event`。

`ac_context_collab_event` 是本地 append-only 事件记录表，承载 `EventEnvelope` 以及 L4 `Task`、`Activity`、`Message`、`Artifact` 等对象引用。`ColletEvent` 不是 ACCS 正式对象名，只是此前错误恢复后留下的持久化俗名；目录和领域代码不再以 `ColletEvent` 作为类名。

#### 3.4.2 当前状态与用户进度

`current_snapshot` 是 `Collet` 的读写快照，不是 ACCS 的新对象；`task_progress` 是 GET 的查询投影，不是第二权威状态。查询返回：

- 当前 L4 `Task` 的状态、目标、负责人和指定执行 Agent；
- 已接纳的 `Activity`、`Message`、`Artifact` 相关事实；
- 当前事件和事件 DAG；每个 event 都有 `parent_event`；
- `trunk_events=true` 时仅返回主事件（查询时由活动链推导：自 `current_event_id` 沿 `parent_event` 回溯到根），默认返回全部可见事件。

### 3.5 L5：Compose Primitives into Methods

L5 不再定义 `CollaborationMode` 或 `CollaborationModePolicy`。使用正式的 `MethodBinding` 组合 L4 原语：

- `Single`：团队代办指定一个 Agent 执行；`executor_ref` 映射到 selected Agent。
- `Group`：多个可寻址参与者持续协作；BBS 公开讨论的协作组使用该方法。
- `Open`：允许外部贡献或自主申请，经 G1 认证、授权和录取后参与；BBS 公开讨论使用 `Open + Group`。
- `Swarm`：当前两个产品示例不需要，暂不引入。

`MethodBinding` 保存方法类型、作用域、Participant/RoleBinding 引用、配置和状态。方法不自动授予 Resource 权限，也不拥有外部 Task / A2A Task 生命周期。

### 3.6 L6：Compose Methods into Products

L6 使用正式的 Product Profile 概念，不新增 `ProductKind` 或 `WorkflowKind` 平行类型：

- `ProductProfile`：当前场景的协议组合 Profile。
- `MethodComposition`：声明 `Open + Group` 或 `Single` 的组合关系。
- `ScenarioRole`：把“主题发起人、讨论参与者、责任人、指定执行 Bot”等产品角色映射到 L4 `RoleBinding`。
- `WorkflowProfile`：声明状态、任务、事件和验收规则的版本化流程 Profile。

`public_discussion`、`team_todo`、`forum_public_discussion`、`team_todo_execution` 是当前两个产品 Profile / Workflow Profile 的实例值和版本键，不是新的 ACCS 类。

### 3.7 G1-G4 与领域对象的边界

- G1 使用 `Authentication`、`Authorization`、`Policy`、`Audit`，但不把 `Participant` 或 `Capability` 当作权限。
- G2 使用 `Recovery` 约束事务失败与重复提交（重复提交由 `expected_version` 乐观并发拒绝）；防重责任由调用方承担，本域不设幂等键；不为 `Retry`、`Lease`、`Heartbeat` 建当前首版对象。
- G3 的 `Observation`、`Outcome` 可作为执行观察和验收结果引用；它们不替代 L4 `Task` 或 `Activity` 状态。
- G4 使用 `Schema`、`Version`、`State Machine`、`Conformance` 约束事件和状态；状态迁移记录是实现细节，不是新增 ACCS L4 对象。

因此，领域对象恢复与数据库表最小化仍是两个独立决策：L2-L6/G1-G4 的必要正式对象保留在领域模型中；数据库只新增 `Collet` 和事件记录两张表。

## 4. G4 State Machine：可扩展状态机

状态机属于 ACCS G4 `State Machine`，不是 L4 新增的上下文类。它以 G4 `Schema`、`Version` 进行版本化，以 `Conformance` 合同校验；运行时绑定只是 `Collet` 快照中的状态机引用和当前状态，不单独建立领域表。

### 4.1 状态机合同

状态机定义采用 `context-state-machine/1` schema。`states`、`transitions`、`guard_refs` 和 `effect_refs` 是 G4 State Machine 的声明式内容；`StateDefinition`、`TransitionDefinition`、`StateMachineBinding` 等名称仅是实现内部的结构类型，不是 ACCS L4 概念对象。

```yaml
schema_version: context-state-machine/1
profile_ref: team_todo_execution
profile_version: 1
compatibility_policy: new_contexts_only
initial_state: ready
states:
  ready:
    governance_kind: active
    allowed_methods: [Single]
  active:
    governance_kind: active
    allowed_methods: [Single]
  reviewing:
    governance_kind: review
    allowed_methods: [Single]
  blocked:
    governance_kind: blocked
    allowed_methods: [Single]
  done:
    governance_kind: terminal_success
    allowed_methods: [Single]
  cancelled:
    governance_kind: terminal_cancelled
    allowed_methods: [Single]
transitions:
  - rule_id: start_task
    from_states: [ready]
    event_name: task.started
    guard_refs: [actor_can_start_task]
    to_state: active
  - rule_id: update_progress
    from_states: [active]
    event_name: task.progress.updated
    guard_refs: [event_matches_task]
    to_state: active
  - rule_id: submit_for_review
    from_states: [active]
    event_name: task.submitted
    guard_refs: [required_artifacts_present]
    to_state: reviewing
  - rule_id: accept_task
    from_states: [reviewing]
    event_name: task.accepted
    guard_refs: [actor_is_responsible_principal]
    to_state: done
  - rule_id: reject_task
    from_states: [reviewing]
    event_name: task.rejected
    guard_refs: [actor_is_responsible_principal]
    to_state: active
  - rule_id: cancel_task
    from_states: [ready, active, reviewing, blocked]
    event_name: task.cancelled
    guard_refs: [actor_can_cancel]
    to_state: cancelled
```

### 4.2 状态治理类别

每个自定义状态必须映射到 G4 State Machine 的治理类别：

| 类别 | 语义 | 展示 / 计算约定 |
| --- | --- | --- |
| `waiting` | 等待人类补充、外部输入或选择 | 进入待办通知与 blockers |
| `active` | 协作或执行进行中 | 必须声明允许的 Method |
| `review` | 等待责任主体验收或审批 | 默认只有明确责任主体可裁决 |
| `blocked` | 被显式阻塞 | 必须携带 blocker |
| `terminal_success` | 目标完成 | 后续只能派生只读投影 |
| `terminal_cancelled` | 主动取消 | 保留历史事实 |
| `terminal_failed` | 无法继续 | 需要人工决定是否重建 |

`initial_state`、状态可达性、终态出边和迁移唯一性由 G4 `Conformance` 校验。`allowed_methods` 只引用 L5 `Single`、`Group`、`Open` 或 `Swarm`，不再使用平行的 `mode_kind` / `mode_policy` 类型。

### 4.3 守卫与效果

- `guard_refs` 只能引用已注册的纯谓词，例如 `actor_can_start_task`、`actor_is_responsible_principal`、`required_artifacts_present`、`event_matches_task`。
- `effect_refs` 只能引用已注册的领域动作 / 投递意图，例如 `ensure_a2a_task_request`、`publish_snapshot`、`notify_responsible_principal`。
- 守卫返回明确 `PASS`、`FAIL`、`BLOCKED` 事实，禁止静默失败。
- Collet、Event record、状态引用和 outbox 意图同事务提交；任何写失败均回滚并向上传播。外部效果在提交后投递，失败只追加可靠性事实，不回滚已经接纳的事实。
- 投递采用至少一次重试及消费端幂等；只有明确失败事件命中合法规则时才进入 `blocked`，不能由 worker 直接改状态。

禁止脚本、内联 SQL、网络调用、文件访问或框架 import 进入 Core。状态机合同只定义业务阶段；外部执行系统的生命周期通过 L1 `a2a.Task` 或外部 Work 事件映射回来。

### 4.4 定义校验

状态机注册或绑定时必须校验：

1. `initial_state` 恰好指定一个已定义起点。
2. 所有状态从起点可达；每个非终态至少有一条可触发迁移或显式等待策略。
3. 每个 `terminal_*` 状态不能有出边。
4. `from_states` / `to_state` 引用已定义状态；`event_name` 来自标准事件集或当前 Profile 声明。
5. 每条规则有唯一 `rule_id`；同一 state / event 命中多个 guard 时拒绝裁决。
6. 所有 guard / effect 已注册，Method 与目标状态兼容。
7. 不允许未知字段；schema、节点数、字符串和扩展 payload 有明确上限。
8. `Schema`、`Version` 和 content digest 一起保存；已绑定版本不可原位修改。
9. 升级必须显式声明兼容策略；不声明时只能影响之后创建的 Collet。

## 5. L5 Methods 与 L6 Products

### 5.1 MethodBinding

L5 使用 ACCS 正式对象 `MethodBinding` 将 L4 原语组合成协作方法。`Single`、`Group`、`Open`、`Swarm` 是可组合的方法对象，不是互斥的“模式枚举”；当前两个产品只采用以下组合：

| 产品场景 | MethodBinding | L5 对象取值 | 语义 |
| --- | --- | --- | --- |
| BBS 公开讨论 | `Open + Group` | `Open` 允许公开范围内的获授权主体参与；`Group` 表示多人协作组 | 参与者可持续追加 L4 `Message` / `Activity`，由责任主体汇总和验收 |
| 团队任务代办 | `Single` | `Single` 绑定一个指定 `Agent` | 只有指定 Agent 作为当前执行主体，其他主体可观察或验收 |

`MethodBinding` 记录方法组合、参与者 / `RoleBinding` 引用、执行主体和完成规则。它不授予 Resource 权限，也不拥有外部 `a2a.Task` 或 Work 生命周期。当前不启用 `Swarm`。

### 5.2 ProductProfile 与 WorkflowProfile

L6 使用正式对象：

- `ProductProfile`：产品场景采用的协议组合 Profile。
- `MethodComposition`：声明 `Open + Group` 或 `Single` 等方法组合。
- `ScenarioRole`：把产品角色映射到 L4 `RoleBinding`，例如主题发起人、讨论参与者、责任人、指定执行 Agent。
- `DomainProfile`：声明 BBS 社区或团队空间的领域约束。
- `WorkflowProfile`：声明 Task、Activity、Message、Artifact 的状态、事件和验收规则。

`public_discussion`、`team_todo`、`forum_public_discussion`、`team_todo_execution` 是 Profile 的实例键 / 版本值，不是新的 ACCS 类。API 中继续保留 `product_kind`、`workflow_kind` 字段作为兼容字段，但它们在领域模型中分别映射为 `ProductProfile`、`WorkflowProfile`。

### 5.3 方法选择规则

1. 每个 State Machine 状态必须声明允许的 L5 Method；取值只来自 `Single`、`Group`、`Open`、`Swarm`。
2. `MethodBinding` 的变化必须追加事件事实，不能只更新 Collet 快照。
3. `Open + Group` 允许多个通过 G1 认证和授权的参与者提交 L4 `Message` / `Activity`；加入方法本身不扩大 Resource 可见性。
4. `Single` 必须绑定一个通过 G1 校验的 L2 `Agent`，并保留其 `Delegation` / `Capability` 引用；Capability 不等于 Authorization。
5. 外部执行调用可通过 L1 `a2a.Message`、`a2a.Task`、`Part`、`a2a.Artifact` 交付，但 L4 `Task`、`Activity`、`Message`、`Artifact` 仍是协作上下文内的事实语义。
6. 外部系统回报的启动、进度、阻塞、完成和交付事实，转换为 Event record 中的 L4 `Activity` / `Artifact` 语义，再由 G4 State Machine 推进 Collet 快照。

## 6. 三个 API 合同

### 6.1 路径

北向 Service API（面向前端）位于 ACCS L1-L6 之外，由 HTTP / OpenAPI Delivery 暴露：

```text
POST /api/v1/collaboration/task/context_collab
POST /api/v1/collaboration/task/context_collab/{collab_context_id}/events
GET  /api/v1/collaboration/task/context_collab/{collab_context_id}
```

OpenAPI delivery adapter：

```text
POST /openapi/v1/collaboration/task/context_collab
POST /openapi/v1/collaboration/task/context_collab/{collab_context_id}/events
GET  /openapi/v1/collaboration/task/context_collab/{collab_context_id}
```

两个 adapter 只做解析、认证上下文转换、schema 校验、序列化和错误映射；随后把请求转换为 L1 协议 DTO，再调用核心 Service API。Service API contract 与 ACCS L1-L6 协议层分开。

### 6.2 新增协作上下文

该接口只创建一行 `Collet` 和一行初始 Event record（`task.created`）。创建请求只保留三个 API 后续会用到的最小字段；L2/L3/L5/L6 信息作为 `context_spec` 内的引用或枚举保存。

| 参数 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- | --- |
| `space` | body | object | 是 | L3 Space 引用 |
| `principal_ref` | body | object | 是 | L2 责任主体引用 |
| `product_kind` | body | string | 否 | L6 产品类型，如 `team_todo`、`public_discussion` |
| `workflow_kind` | body | string | 否 | L6 流程类型；仅保存字符串，不建 Profile 表 |
| `method_kind` | body | string | 是 | L5 方法；当前为 `Single` 或 `Open + Group` |
| `executor_ref` | body | object | 条件必填 | `method_kind=Single` 时必填；指定执行 Bot 引用 |
| `objective` | body | string | 是 | 当前协作目标 |
| `resource_refs[]` | body | array | 否 | L3 外部资源引用，不复制资源正文 |

#### 指定执行请求示例

```http
POST /api/v1/collaboration/task/context_collab
Content-Type: application/json
```

```json
{
  "space": {
    "space_id": "space_workspace_001",
    "environment": "prod"
  },
  "principal_ref": {
    "principal_type": "human",
    "principal_id": "u_1001"
  },
  "product_kind": "team_todo",
  "workflow_kind": "team_todo_execution",
  "method_kind": "Single",
  "executor_ref": {
    "agent_type": "bot",
    "agent_id": "bot_coding_001"
  },
  "objective": "整理 Context 协作协议评审结论并提交可评审 PR",
  "resource_refs": [
    {
      "resource_type": "forum_topic",
      "resource_id": "topic_001",
      "revision": "3"
    }
  ]
}
```

#### 响应示例

```json
{
  "collab_context_id": "collet_001",
  "context_version": 1,
  "current_snapshot": {
    "objective": "整理 Context 协作协议评审结论并提交可评审 PR",
    "status": "created",
    "principal_ref": "u_1001",
    "executor_ref": "bot_coding_001",
    "method_kind": "Single",
    "progress": {
      "percent": 0,
      "current_event_id": "evt_001"
    }
  },
  "current_event": {
    "event_id": "evt_001",
    "parent_event": null,
    "event_kind": "task.created",
    "primitive_type": "Task"
  }
}
```

### 6.3 新增协作上下文事件

该接口接收一个事件事实，由服务端执行 G1/G2/G4 校验并追加一行 Event record，再更新同一 `Collet` 的当前快照。

| 参数 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- | --- |
| `collab_context_id` | path | string | 是 | L4 Collet ID 的 API 兼容名 |
| `expected_version` | body | integer | 是 | 乐观并发版本 |
| `event_kind` | body | string | 是 | 例如 `user_interaction.posted` |
| `primitive_type` | body | string | 是 | `Message`、`Task` 或 `Activity` |
| `actor_ref` | body | object | 是 | L2 Principal / Agent 引用 |
| `parent_event` | body | string/null | 是 | 主事件为 `null`，派生事件引用父事件 |
| `visibility` | body | string | 是 | 只能落在调用者已有授权范围内 |
| `payload` | body | object | 是 | 按 G4 Schema 校验；Message 必须直接包含 `content` |

#### 用户新增互动消息示例

```http
POST /api/v1/collaboration/task/context_collab/collet_001/events
Content-Type: application/json
```

```json
{
  "expected_version": 1,
  "event_kind": "user_interaction.posted",
  "primitive_type": "Message",
  "actor_ref": {
    "principal_type": "human",
    "principal_id": "u_1001"
  },
  "parent_event": "evt_001",
  "visibility": "workspace",
  "payload": {
    "message_kind": "user_interaction",
    "content": "上下文做一下补充xxx"
  }
}
```

#### 响应示例

```json
{
  "accepted": true,
  "collab_context_id": "collet_001",
  "event_id": "evt_002",
  "parent_event": "evt_001",
  "event_kind": "user_interaction.posted",
  "primitive_type": "Message",
  "context_version": 2,
  "current_status": "in_progress"
}
```

### 6.4 查询协作上下文最新状态与任务进度

```http
GET /api/v1/collaboration/task/context_collab/{collab_context_id}?trunk_events=true
```

| 参数 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- | --- |
| `collab_context_id` | path | string | 是 | L4 Collet ID |
| `trunk_events` | query | boolean | 否 | 默认 `false`；`true` 只返回主事件（活动链推导） |

#### 响应示例

```json
{
  "collab_context_id": "collet_001",
  "context_version": 4,
  "current_snapshot": {
    "objective": "整理 Context 协作协议评审结论并提交可评审 PR",
    "status": "in_progress",
    "principal_ref": "u_1001",
    "method_kind": "Single",
    "executor_ref": "bot_coding_001",
    "progress": {
      "percent": 40,
      "completed_count": 2,
      "active_count": 1,
      "blocked_count": 0,
      "current_event_id": "evt_004"
    }
  },
  "current_event": {
    "event_id": "evt_004",
    "parent_event": "evt_003",
    "event_kind": "task.progress.updated",
    "primitive_type": "Activity",
    "summary": "开始整理协议设计",
    "occurred_at": "2026-11-10T08:30:00Z"
  },
  "task_progress": {
    "total_events": 5,
    "events": [
      {
        "event_id": "evt_001",
        "parent_event": null,
        "event_kind": "task.created",
        "primitive_type": "Task",
        "summary": "创建协作任务",
        "status": "completed"
      },
      {
        "event_id": "evt_002",
        "parent_event": "evt_001",
        "event_kind": "user_interaction.posted",
        "primitive_type": "Message",
        "content": "上下文做一下补充xxx",
        "status": "completed"
      },
      {
        "event_id": "evt_003",
        "parent_event": "evt_002",
        "event_kind": "task.assigned",
        "primitive_type": "Task",
        "summary": "指定 bot_coding_001 执行",
        "status": "completed"
      },
      {
        "event_id": "evt_004",
        "parent_event": "evt_003",
        "event_kind": "task.progress.updated",
        "primitive_type": "Activity",
        "summary": "开始整理协议设计",
        "status": "active"
      },
      {
        "event_id": "evt_005",
        "parent_event": "evt_004",
        "event_kind": "task.progress.updated",
        "primitive_type": "Activity",
        "summary": "完成设计并准备提交 PR",
        "status": "pending"
      }
    ]
  }
}
```

该返回直接回答：

- **做了哪些事情**：读取 `task_progress.events[]` 中已经接纳的事件。
- **当前到了哪一步**：读取 `current_snapshot.status`、`progress.current_event_id` 和 `current_event`。
- **主事件或全部事件**：`trunk_events=true` 只保留当前活动链（自 `current_event_id` 沿 `parent_event` 回溯到根的事件），不改变 `current_snapshot`、统计或当前事件；`is_trunk` 不落库。

### 6.5 OpenAPI 合同

OpenAPI 面必须暴露与内部 API 相同语义的三个路径，并复用以下 schema：

- `ColletCreateRequest`
- `EventCreateRequest`
- `ContextCollabStatusResponse`
- `TaskProgress`
- `Activity` / `Event record` 投影：必须包含 `event_id`、`parent_event`、`event_kind`、`primitive_type`、`status`
- `ContextSpec`、`EventPayload`、`TaskProgress` 和 L2/L3 引用 DTO

OpenAPI schema 不应暴露未被本方案采用的 Agent Card、A2A Task、Metric、Candidate 等对象。

## 7. 两个产品支持示例

本节以 ACCS 正式对象为准，给出 BBS 公开讨论和团队任务代办的 L1-L6/G1-G4 取值。API 字段中的兼容名称会在表中明确对应到正式对象。

### 7.1 BBS 社区公开讨论

| 层次 | 正式对象及当前取值 |
| --- | --- |
| L1 | 需要跨 Agent 交付时使用 `Agent Card`、`a2a.Message`、`Part`、`a2a.Artifact`；三个北向 Service API 不是 L1 对象 |
| L2 | `Principal=u_topic_owner`；可选 `Agent=bot_forum_summary_001`；需要的 `Capability` 和 `Delegation` 由外部系统提供 |
| L3 | `Space=bbs_community_space`；`Resource=forum_topic:topic_001@rev3`；由 `Membership`、`Grant`、`Policy Authority` 提供授权输入 |
| L4 | `Collet=collet_forum_topic_001`；`Participant` / `RoleBinding` 表示主题发起人和讨论参与者；用户消息是 `Message`，活动是 `Activity`，资源带入使用 `ResourceBinding`，查询使用 `Query` |
| L5 | `MethodBinding=Open + Group`；不使用 `Swarm` |
| L6 | `ProductProfile=public_discussion`；`MethodComposition=Open + Group`；`DomainProfile=bbs_community`；`WorkflowProfile=forum_public_discussion`；产品角色通过 `ScenarioRole` 映射到 `RoleBinding` |
| G1 | `Authentication`、`Authorization`、`Policy`、`Audit` 校验 `topic.read`、`message.post` 和事件 visibility |
| G2 | `Recovery` 处理事务失败与重复提交（`expected_version` 并发拒绝）；事件记录保留 `parent_event`，查询使用 `trunk_events` |
| G3 | `Observation` 可记录讨论活动；`Outcome` 可记录责任人汇总结果；GET 只是查询投影 |
| G4 | `Schema` / `Version` / `State Machine` / `Conformance` 约束公开讨论事件和状态 |

流程：

```text
已有 BBS Topic（L3 Resource）
  → POST 创建 L4 Collet，并绑定 Space、ResourceBinding、Participant、RoleBinding
  → 绑定 L5 MethodBinding（Open + Group）和 L6 ProductProfile / WorkflowProfile
  → 用户 POST L4 Message 事件（user_interaction.posted + payload.content）
  → 服务端追加 Event record，更新 Collet current_snapshot
  → 后续用户 / Bot 追加 Message、Activity、Artifact 事实，parent_event 形成 DAG
  → GET 通过 Query 返回 Collet、当前状态和 task_progress.events[]
```

### 7.2 团队空间内创建任务代办并指定 Bot 执行

| 层次 | 正式对象及当前取值 |
| --- | --- |
| L1 | 使用指定 Agent 的 `Agent Card`；通过 `a2a.Task` / `a2a.Message` / `Part` 交付执行请求和结果；北向三个 API 仍在 L1-L6 之外 |
| L2 | `Principal=u_1001`；`Agent=bot_coding_001`；`Capability=code.change`；`Delegation=u_1001→bot_coding_001`（由外部权威确认） |
| L3 | `Space=team_space_001`；可选 `Resource=repo:repo_001@rev7`；访问由 `Membership`、`Grant`、`Policy Authority` 决定 |
| L4 | `Collet=collet_todo_001`；`Participant` / `RoleBinding` 绑定责任主体和指定执行 Agent；`Task` 表示代办；`Requirement` 表示能力约束；`Activity` 表示进度；`Artifact` 表示 PR / 文档交付 |
| L5 | `MethodBinding=Single`，绑定 `Agent=bot_coding_001` |
| L6 | `ProductProfile=team_todo`；`MethodComposition=Single`；`DomainProfile=team_space`；`WorkflowProfile=team_todo_execution`；`ScenarioRole` 映射责任人和执行 Agent |
| G1 | `Authentication`、`Authorization`、`Policy`、`Audit` 校验 `todo.create`、`todo.execute`、`todo.accept` 和 Resource 访问权限 |
| G2 | `Recovery` 处理失败；`parent_event` 建立任务事件 DAG |
| G3 | `Observation` 记录 Bot 进度；`Outcome` 记录验收结果；不创建额外事实表 |
| G4 | `Schema` / `Version` / `State Machine` / `Conformance` 约束 `Task`、`Activity`、`Artifact` 事件和状态 |

流程：

```text
用户创建指定执行代办
  → POST 创建 L4 Collet、Task、Participant、RoleBinding 和 Requirement
  → 绑定 L5 MethodBinding（Single）以及 L6 ProductProfile / WorkflowProfile
  → 服务端接纳 task.created Event record
  → 通过 L1 Agent Card / a2a.Task 将执行请求交付给指定 Agent
  → Bot / Adapter 追加 Activity 进度事件和 Artifact 交付事件
  → 用户追加 task.accepted 或 task.rejected 事件，G4 State Machine 推进状态
  → GET Query 返回当前 Task、完成了哪些 Activity、当前状态和 task_progress.events[]
```

### 7.3 两个产品共用的最小边界

```text
L4 Collet（context_spec + current_snapshot）
Event record（EventEnvelope + parent_event + payload）
GET Query / task_progress.events[]
expected_version / trunk_events
```

BBS 的 `Open + Group` 和团队代办的 `Single` 只是 `MethodBinding` 及 Profile 取值，不因为产品不同再新增领域表。

## 8. 目录设计：按 L1-L6、G1-G4 分组

### 8.1 目录约束

新增核心代码统一放在：

```text
apps/backend/src/agentclaw/community/core/task/context_collab/
```

不放到：

```text
apps/backend/src/agentclaw/community/core/task/forum/
apps/backend/src/agentclaw/community/core/task/bbs/
apps/backend/src/agentclaw/community/plugin_api/
apps/backend/src/agentclaw/community/api/
```

`context_collab/` 下不创建 `source/`。目录只表达 ACCS 层次和治理面；北向 Service API、HTTP/OpenAPI Delivery、持久化适配器不冒充 L1。

### 8.2 目录结构

```text
apps/backend/src/agentclaw/community/
├── core/task/context_collab/
│   ├── service_api.py                              # 北向 Service API 合同；位于 L1-L6 之外
│   ├── l1_interoperate_deliver/
│   │   └── a2a_bridge.py                           # Agent Card / a2a.Message / a2a.Task / Part / a2a.Artifact
│   ├── l2_agent_network/
│   │   └── network_refs.py                          # Principal / Agent / Capability / Delegation 引用
│   ├── l3_spaces_resources/
│   │   └── space_resource_refs.py                   # Space / Membership / Resource / Grant / Policy Authority 引用
│   ├── l4_context_primitives/
│   │   ├── collet.py                                # Collet
│   │   ├── participants.py                          # Participant / RoleBinding
│   │   ├── task.py                                   # Task / Requirement
│   │   ├── activity.py                               # Activity
│   │   ├── decision.py                               # Decision
│   │   ├── message.py                                # accs.collaboration.Message
│   │   ├── artifact.py                               # Artifact / ResourceBinding
│   │   ├── query.py                                  # Query
│   │   └── event_envelope.py                         # EventEnvelope；本地 Event record 的协议适配
│   ├── l5_methods/
│   │   └── method_binding.py                         # Single / Group / Open / MethodBinding
│   ├── l6_products/
│   │   └── profiles.py                               # ProductProfile / MethodComposition / ScenarioRole / DomainProfile / WorkflowProfile
│   ├── g1_secure_govern/
│   │   └── authorization.py                          # Authentication / Authorization / Policy / Audit
│   ├── g2_keep_reliable_live/
│   │   └── reliability.py                            # Idempotency / Recovery / Reliability Signal（Retry / Lease / Heartbeat 为扩展合同）
│   ├── g3_observe_evaluate_evolve/
│   │   └── observation.py                            # Observation / Outcome（当前只启用必要引用）
│   ├── g4_contracts_consistency/
│   │   ├── schema.py                                 # Schema / Version / Conformance
│   │   └── state_machine.py                          # State Machine；内部 binding / transition 结构
│   ├── persistence/
│   │   └── repository.py                             # 两张表的持久化协议
│   └── services/
│       └── context_collab_service.py                # 创建、追加事件、查询三个用例
├── adapters/http/task/context_collab.py              # /api/v1 三个 Service API 路由
└── adapters/http/openapi_v1/task/context_collab.py  # /openapi/v1 三个 API 路由
```

目录中的 ACCS 概念对象与本方案工程对象边界如下：

| 目录 | 正式对象 / 本地对象 | 说明 |
| --- | --- | --- |
| `l1_interoperate_deliver/` | `Agent Card`、`a2a.Message`、`a2a.Task`、`Part`、`a2a.Artifact` | 只在需要指定 Agent 交付时桥接；不是前端 Service API |
| `l2_agent_network/` | `Principal`、`Agent`、`Capability`、`Delegation` | 只保存稳定 ObjectRef；不复制外部注册表 |
| `l3_spaces_resources/` | `Space`、`Membership`、`Resource`、`Grant`、`PolicyAuthority` | 只保存作用域和资源引用；授权权威在外部系统 |
| `l4_context_primitives/` | `Collet`、`Participant`、`RoleBinding`、`Task`、`Activity`、`Decision`、`Message`、`Artifact`、`ResourceBinding`、`Requirement`、`Query`、`EventEnvelope` | 领域对象完整保留；Event record 是本地持久化记录，不冒充 ACCS 概念 |
| `l5_methods/` | `Single`、`Group`、`Open`、`MethodBinding` | 方法可组合；当前产品采用 `Open + Group` 和 `Single`；不引入 `Swarm` |
| `l6_products/` | `ProductProfile`、`MethodComposition`、`ScenarioRole`、`DomainProfile`、`WorkflowProfile` | Profile 只作为版本化引用 / 值对象，不建 Profile 表 |
| `g1...` | `Authentication`、`Authorization`、`Policy`、`Audit` | 横切校验与审计引用 |
| `g2...` | `Idempotency`、`Retry`、`Lease`、`Heartbeat`、`Recovery`、`Reliability Signal` | 当前 API 最小启用 Idempotency / Recovery，其他为可扩展合同类型 |
| `g3...` | `Observation`、`Outcome`、`Metric`、`Candidate`、`Verification`、`Experiment Ledger` | 当前只启用 Observation / Outcome 引用，不建立独立生命周期 |
| `g4...` | `Schema`、`Version`、`State Machine`、`Conformance` | 状态机与合同校验；不建独立 StateMachine 表 |
| `persistence/` | `Collet` 快照 + Event record 事实 | 只对应两张表，不删除上层领域对象 |

这里的 `service_api.py`、`Event record`、`TaskProgress` 等是本方案边界对象；它们是实现合同或查询投影，不是羽雀文档新增的 ACCS 概念对象。

### 8.3 领域对象文件与职责

- `service_api.py`：定义面向前端消费者的 transport-agnostic Service API 合同，协调三个用例；它位于 L1-L6 之外。
- `l1_interoperate_deliver/`：定义 A2A 互操作和交付桥接；不承载 HTTP 路由。
- `l2_agent_network/`：定义 Principal / Agent / Capability / Delegation 的稳定引用和值对象。
- `l3_spaces_resources/`：定义 Space / Membership / Resource / Grant / Policy Authority 的引用和值对象。
- `l4_context_primitives/`：定义 Collet 及其 Participant、RoleBinding、Task、Activity、Decision、Message、Artifact、ResourceBinding、Requirement、Query 和 EventEnvelope。
- `l5_methods/`：定义 MethodBinding 及 Single / Group / Open / Swarm 的组合规则。
- `l6_products/`：定义 ProductProfile、MethodComposition、ScenarioRole、DomainProfile、WorkflowProfile 的引用和值对象。
- `g1_secure_govern/`：执行 Authentication、Authorization、Policy、Audit 相关校验。
- `g2_keep_reliable_live/`：执行 Idempotency、Recovery、版本冲突和事件 DAG 规则；Retry / Lease / Heartbeat 只保留扩展合同，不创建当前生命周期。
- `g3_observe_evaluate_evolve/`：从 Event record 生成 Observation / Outcome 引用和 GET 查询投影。
- `g4_contracts_consistency/`：执行 Schema、Version、State Machine、Conformance 校验。
- `persistence/`：只定义两张表的持久化协议，不拆出 TaskRepository、MessageRepository 或 ParticipantRepository。

### 8.4 明确不创建的目录 / 文件

本版不创建的是独立持久化表、独立生命周期和当前产品不需要的实现，而不是删除 ACCS 领域对象：

```text
agent_card_registry.py       # Agent Card 由外部权威提供
profile_registry.py          # ProductProfile / WorkflowProfile 不在本域注册
lease_runtime.py             # 当前三个 API 不拥有 Lease 生命周期
metric_store.py              # G3 Metric 不在当前范围
experiment_ledger.py         # G3 演进实验不在当前范围
```

### 8.5 依赖方向

- Core 不 import HTTP、FastAPI、OpenAPI、BCS 具体 client 或外部来源 SDK。
- `service_api.py` 位于 L1-L6 之外；HTTP/OpenAPI Delivery 只能调用这份 Service API 合同。
- L1 只负责 A2A 协议交付桥接，不接管前端 API。
- L2/L3 只保存外部身份、授权和资源的稳定引用，不复制其实现。
- L4 负责 Collet 和协作事实，不直接调用外部执行系统；L5/L6 只提供方法和 Profile 组合。
- G1 在写入和读取前提供授权判断；G2 负责并发恢复（`expected_version`）与事件 DAG（防重责任由调用方承担）；G3 只生成查询投影；G4 负责 schema、版本、状态机和 conformance。
- `plugin_api`、`task/forum`、`task/bbs` 不新增或修改文件；`context_collab/` 下不创建 `source/` 子目录。

## 9. 数据库表与持久化层设计

### 9.1 结论：只新增两张表

当前三个 API 的持久化闭环只有两类事实；这不代表领域对象只有两个。领域对象仍按 L1-L6/G1-G4 定义，多个对象序列化在两张权威表的 JSON 字段中：

1. **Collet 当前快照**：创建时写入，追加事件时更新，查询时读取。
2. **Event record 事实日志**：创建时写入初始事件，事件接口追加，查询时按 `trunk_events` 过滤。

因此只新增：

| 表 | 必要原因 | 不再单独建表的内容 |
| --- | --- | --- |
| `ac_context_collab_collet` | 保存 `collab_context_id`、创建主体引用 `actor_ref`、创建参数、当前任务/方法/运行/进度快照和 `context_version` | Task、方法、执行者、资源引用和当前进度都保存在 JSON 字段；`current_snapshot` 可空 |
| `ac_context_collab_event` | 保存事件、`parent_event` 和 payload | 用户互动、进度、交付和验收信息都只是事件 payload，不是表；主干预算不落库 |

明确不新增：独立任务表、消息表、参与者表、角色表、方法表、状态迁移表、租约表、审计表、幂等表、产物表、Outcome 表。

### 9.2 两张表的最小字段

- `ac_context_collab_collet.context_spec`：创建请求中需要长期保留的 space、principal、method、executor、resource_refs 和 objective。
- `ac_context_collab_collet.current_snapshot`：当前 objective、status、executor、method 和 progress 的查询快照。
- `ac_context_collab_event.payload`：事件业务数据；用户互动消息直接放 `payload.content`。

三个接口都不设幂等键：调用方无需传入防重号，防重责任由调用方承担（逐字重复提交由 `expected_version` 乐观并发拒绝）；`trunk_events` 是查询参数，不落表。

### 9.3 持久化层目录与职责

```text
apps/backend/src/agentclaw/community/core/task/context_collab/persistence/repository.py
```

只有一个 `ContextCollabRepositoryProtocol`，只覆盖三个操作：

```text
create_context(request) -> created context + initial event
append_event(collab_context_id, request) -> accepted event + new snapshot
get_current_status(collab_context_id) -> current status   # 全量返回，trunk 过滤在服务投影层
```

不拆分 ColletRepository、TaskRepository、MessageRepository、EventRepository 等没有独立 API 消费者的 Repository。

### 9.4 三个 API 的事务规则

1. **创建**：同一事务插入一行 `ac_context_collab_collet`（含创建主体引用 `actor_ref`，与初始事件同值）和一行初始 `task.created` 事件；创建不设幂等键，重复调用即重复创建。
2. **追加事件**：锁定 Collet 行，检查 `expected_version`、`parent_event` 和权限；同一事务插入 Event 并更新 Collet 的 `current_snapshot`、`current_event_id`、`context_version`；`current_snapshot` 为 NULL 的聚合行保持 NULL，仅推进版本与指针。
3. **查询**：读取一行 Collet 和事件列表；按授权和 `trunk_events` 过滤，组合 `task_progress.events[]`；不写数据库。`current_snapshot` 为 NULL 的聚合行返回 `current_snapshot: null`，事件链投影照常；事件行中可为 NULL 的 `actor_ref`/`visibility`/`payload`/`primitive_type`/`context_version_after` 解码为 null，`visibility=NULL` 按 private 处理；`primitive_type=NULL` 仍参与进度计数（统计仅排除 `Message`）。
4. **事件 DAG**：主事件 `parent_event=null`，派生事件引用同一上下文内已存在事件；事件顺序为落库顺序（自增 id）；主事件在查询时由活动链推导（自 `current_event_id` 沿 `parent_event` 回溯），`trunk_events=true` 仅在投影层过滤事件列表。
5. **并发**：追加不设幂等键，重复提交即重复事件（由调用方防重）；`expected_version` 不匹配直接返回冲突。
6. **错误传播**：唯一键、数据库写入和版本冲突必须向上返回，不能吞错。

### 9.5 DDL

以下 DDL 仅为当前三个 API 提供最小持久化，不为 ACCS 概念对象逐一建表。

```sql
-- OceanBase / MySQL mode. SQLite tests mirror the JSON columns as TEXT.

CREATE TABLE `ac_context_collab_collet` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `avernet_tenant` varchar(64) NOT NULL DEFAULT 'teamclaw',
  `env` varchar(20) NOT NULL,
  `collab_context_id` varchar(128) NOT NULL,
  `actor_ref` varchar(2048) DEFAULT NULL COMMENT 'JSON creating-principal reference; same value as the initial task.created event',
  `context_spec` mediumtext NOT NULL COMMENT 'JSON: create request references and method fields',
  `current_snapshot` mediumtext DEFAULT NULL COMMENT 'JSON: current objective, status, executor and progress; nullable for spec-only rows',
  `current_event_id` varchar(128) DEFAULT NULL,
  `context_version` bigint(20) NOT NULL DEFAULT 1,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_context_collab_collet_context` (`avernet_tenant`, `env`, `collab_context_id`) GLOBAL,
  KEY `idx_context_collab_collet_modified` (`avernet_tenant`, `env`, `gmt_modified`) GLOBAL
) AUTO_INCREMENT_MODE = 'ORDER' DEFAULT CHARSET = utf8mb4
  COMMENT = 'Minimal context collaboration aggregate snapshot';

CREATE TABLE `ac_context_collab_event` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `avernet_tenant` varchar(64) NOT NULL DEFAULT 'teamclaw',
  `env` varchar(20) NOT NULL,
  `collab_context_id` varchar(128) NOT NULL,
  `event_id` varchar(128) NOT NULL,
  `parent_event` varchar(128) DEFAULT NULL,
  `event_kind` varchar(128) NOT NULL,
  `primitive_type` varchar(64) DEFAULT NULL COMMENT 'L4 primitive (Message/Task/Activity); nullable for partial rows',
  `actor_ref` varchar(2048) DEFAULT NULL COMMENT 'JSON actor reference; nullable for partial rows',
  `visibility` varchar(32) DEFAULT NULL,
  `payload` mediumtext DEFAULT NULL COMMENT 'JSON event payload; user interaction uses payload.content; nullable for partial rows',
  `context_version_after` bigint(20) DEFAULT NULL,
  `occurred_at` timestamp NOT NULL,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_context_collab_event` (`avernet_tenant`, `env`, `collab_context_id`, `event_id`) GLOBAL,
  KEY `idx_context_collab_event_parent` (`avernet_tenant`, `env`, `collab_context_id`, `parent_event`) GLOBAL
) AUTO_INCREMENT_MODE = 'ORDER' DEFAULT CHARSET = utf8mb4
  COMMENT = 'Minimal append-only context collaboration event DAG';
```

### 9.6 三个 API 的最小读写路径

| API | 最小数据库动作 |
| --- | --- |
| `POST /api/v1/collaboration/task/context_collab` | 插入 1 行 Collet + 1 行初始 Event |
| `POST /api/v1/collaboration/task/context_collab/{collab_context_id}/events` | 插入 1 行 Event + 更新 1 行 Collet |
| `GET /api/v1/collaboration/task/context_collab/{collab_context_id}` | 读取 1 行 Collet + 读取 Event 列表，不写入 |

两个产品都使用这两张表；产品差异只体现在 `context_spec`、`current_snapshot`、`event_kind` 和 `payload` 的取值。

### 9.7 后续拆表条件

只有出现以下真实需求，才允许新增表：独立租约接口、跨上下文审计检索、参与者生命周期管理、独立大文本/产物检索、复杂迁移分析或事件归档分片。当前三个 API 不满足这些条件；领域对象已经保留，不因不拆表而删除。

## 10. 外部系统映射

| 外部系统 | 本方案引用方式 | 本方案接纳的事实 | 权威边界 |
| --- | --- | --- | --- |
| BBS Topic / Post | L3 resource_refs + L4 event payload | 用户互动、主题或帖子引用 | BBS 拥有正文、Topic / Post 生命周期 |
| Task / Relay | L1 delivery contract + event payload | 启动、运行、阻塞、完成等外部事实 | Task / Relay 拥有 Work 生命周期 |
| BCS group / run | L1 delivery contract + event payload | 群互动或运行事实 | BCS 拥有群、路由和 Run 生命周期 |
| Bot / Service | L2 Agent / Capability；外部授权引用 | 指定 executor、执行能力和必要的委托引用 | Agent / BCS / 授权系统拥有身份与能力注册 |
| 代码仓库 / 文档 / PR | L3 Resource + event payload | 输入版本、交付引用和证据 | 原资源系统拥有正文、版本和访问授权 |

## 11. 安全、可靠性与一致性

### 11.1 G1

- Authentication 在 delivery / identity 边界完成，核心只接收已解析的 actor context。
- Authorization 校验创建 Collet、追加互动/进度事件和查询最新状态的权限。
- Capability 只表示 Agent 能力；Membership / Grant / Policy 由外部权威提供。
- 现有 Audit 能力记录 actor、action、space、collet、version 和时间；本域不建 Audit 表。
- 查询响应按当前授权和事件 visibility 过滤，不因加入 Collet 自动扩大资源权限。

### 11.2 G2

- 事件通过 `parent_event` 保留派生关系；防重由调用方负责，`expected_version` 并发拒绝兜底。
- `expected_version` 与 Collet version 防止并发请求覆盖当前快照。
- 外部回报必须先转换为已校验的 Event record；本方案不负责启动或恢复外部执行。

### 11.3 G3

- 查询响应可重建、可按权限过滤、可过期，不反向写权威对象。
- 首版不引入 Metric、Candidate、Verification 和 Experiment Ledger；质量指标和自动演进属于后续独立方案。

### 11.4 G4

- Collet、Event record 和 API schema 使用显式版本字段；不创建 Profile 对象。
- `current_snapshot.status` 只允许协议定义的有限状态值；追加事件时在同一事务内更新。
- 不创建独立 Transition Record 或 State Machine 表；状态定义、迁移规则和运行绑定作为 G4 State Machine 的内部结构，序列化到 Collet 快照或由事件语义重建。
- 未知字段、未知方法值、未注册 event_kind 或不允许的 payload 组合必须拒绝。
- Conformance 是 Service API / Plugin API 的测试要求，不在运行时创建对象。

## 12. 测试与验收

### 12.1 对象与层次合同测试

- 北向 API Delivery：Service API 与 HTTP/OpenAPI adapter 的输入、错误和响应映射。
- L1：协议 DTO 的归一化和映射；不为当前首版增加 A2A 对象测试。
- L2：Principal、Agent、Capability 的引用和指定执行校验；委托由外部授权合同覆盖。
- L3：Space / Resource 引用、版本和授权边界；不复制外部正文。
- L4：Collet 的创建、快照更新和事件 payload 语义；不测试独立 L4 对象表。
- L5：Single/Open/Group 方法配置和 executor 引用；不测试独立 lease。
- L6：`product_kind` / `workflow_kind` 字段值合法性。
- G1-G4：Authorization、并发 (`expected_version` 冲突)、查询投影、Schema、Version 和有限状态值；治理规则不新增表，状态相关领域对象仍保留在 L4。

### 12.2 三个接口验收

1. 创建接口创建 1 行 Collet 和 1 行初始 Event record；任务目标、方法、执行者和进度都在 Collet JSON 快照中。
2. 创建接口拒绝没有 executor、没有 responsible、没有 Capability / Authorization 的指定执行请求。
3. 事件接口接收用户互动事件，事件名为 `user_interaction.posted`，正文直接在 `payload.content`，不出现 `content_ref`。
4. 每个事件都带 `parent_event`；`trunk_events=true` 只筛选主干，不改变统计和状态。
5. 查询返回 current_snapshot、current_event 和 `task_progress.events[]`，不使用 `steps[]`。
6. 查询响应不会反向改写 Collet 快照。

### 12.3 参考端到端场景

| 编号 | 场景 | 判定 |
| --- | --- | --- |
| A01 | 创建指定执行 Collet | 生成 1 行 Collet（context_spec/current_snapshot 包含上述信息）和 1 行初始 Event record |
| A02 | 用户新增互动消息 | 生成 1 行 Event record，payload 直接使用 content，并更新 Collet 快照 |
| A03 | 指定 Agent 开始工作 | Capability 和外部授权均通过，追加 `task.progress.updated` 进度事件 |
| A04 | 外部 Task 完成 | 追加 `task.completed` 事件并更新 Collet 快照 |
| A05 | 提交 PR | 在事件 payload 中保存 PR / repository 引用 |
| A06 | 责任人验收 | 追加 `task.accepted` / `task.rejected` 事件，允许状态值合法迁移 |
| A07 | 查询主事件 | `trunk_events=true` 仅返回 DAG 主事件 |
| A08 | 查询全部事件 | 默认返回全部可见 Event record |
| A09 | 并发写入 | 旧 `expected_version` 重复提交被正确拒绝（409），不落半态 |
| A10 | 权限降级 | 查询响应过滤受限 Resource 和历史事件，不泄露内容 |

## 13. 分阶段交付

| 阶段 | 内容 | 明确不包含 |
| --- | --- | --- |
| P1 | 两张表、一个持久化协议、三个 API、完整领域对象模型、最小授权/并发/事件 DAG 校验和查询投影 | 任何独立 Task/Message/Participant/Method/Profile/StateMachine 表 |
| P2 | 根据真实 API 消费者补充外部资源或执行系统适配 | 复制外部身份、授权、资源和执行生命周期 |

除非三个 API 已经证明存在独立查询、独立生命周期或独立吞吐需求，否则不再拆表或新增 ACCS 对象。

## 14. 兼容、迁移与风险

### 14.1 兼容策略

- 保留 `/api/v1/collaboration/task/context_collab` 和 `/openapi/v1/collaboration/task/context_collab` 三个接口。
- 保留 `collab_context_id` 作为 Collet ID 的 API 兼容字段。
- 保留 `parent_event`、`trunk_events` 和 `task_progress.events[]`。
- `user_interaction.posted` 作为用户互动消息事件名，payload 直接使用 `content`。
- 当前接口只接受本版定义的字段，不新增旧字段兼容转换逻辑。

### 14.2 主要风险与控制

| 风险 | 控制 |
| --- | --- |
| ACCS 对象过度迁移导致模型膨胀 | 采用清单明确“当前采用 / 外部引用 / 不引入”；新增对象必须有消费者和验收场景 |
| Space、Collet、Resource 混淆 | L3 / L4 分离；Collet 只引用 Space 和 Resource |
| Capability 被误当成授权 | 强制 G1 Authorization + 外部 Membership / Grant + RoleBinding |
| Event 成为第二套领域模型 | Event record 只保存 event_kind、payload 和必要关系字段，不创建独立 primitive 生命周期 |
| 查询响应变成第二权威 | 只允许由已接纳 L4 事实重建，不允许反向写入 |
| 目录按层但依赖倒置 | 依赖合同而非目录顺序；适配器和 Composition Root 维持独立 |
| 未来一次性迁移全部 ACCS 对象 | 按实际产品需求分阶段增加，并补齐 schema、API 和测试 |

## 15. 评审问题

1. BBS 公开讨论是否确认采用 `Open + Group`，团队任务代办是否确认采用 `Single`，以及各自的准入、退出和关闭规则是否满足产品要求？
2. 当前 Agent、Capability、Space、Resource 和授权 / 委托事实的权威服务分别是什么，本域是否只读引用？
3. 外部 Task / Relay 的启动是否已有可复用 Service API；如果没有，L1 只保留 delivery contract，不新增 A2A 对象。
4. `ForumPublicDiscussionWorkflow` 与 `TeamTodoExecutionWorkflow` 的状态、迁移和责任主体是否需要补充产品特有状态？
5. BBS Topic / Post 的正文、版本和可见性是否继续由 BBS 权威持有，并仅将用户互动映射为 L4 `Message` / `Activity` 事件（`user_interaction.posted`）？

## 16. 参考文档

- [Avernet ACCS：Agent Collaboration & Coordination Stack（Yuque）](https://yuque.antfin.com/securitytec/otbct4/kgqllm98liao649f)
- [架构宪章](../../../../docs/arch/arch.rules.md)
- [CI 门禁](../../../../docs/arch/ci.enforce.md)
- [Context Boundary 格式](../../../../docs/arch/context-boundary-format.md)
- [Plugin Protocol conformance](../../../../docs/arch/protocol-contract-tests.md)
- [BBS 求助任务化方案](../2026-11-09-bbs-help-task-upgrade/spec.md)
- [BBS 任务类型](../../../../docs/specs/2026-10-27-bbs-v4-topic-types/spec.md)
- [BCS 自定义协作 YAML](../../../../apps/bcs/crates/tools/bcs-cli/bcs-coordination/references/custom-collaboration-schema.md)

本方案只记录目标设计和实施准入，不表示上述能力或接口已经存在于代码中。
