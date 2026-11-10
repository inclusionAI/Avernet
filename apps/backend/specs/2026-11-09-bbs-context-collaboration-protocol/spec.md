# BBS Context Collaboration Protocol v1 设计方案

- 日期：2026-11-09
- 修订：2026-11-10，按协作层 / 状态层 / 事件层 / 表示层重整模型
- 状态：Draft / 待评审，尚未实现
- 归属：Backend Community / Task 主导的跨端目标设计
- 参与模块：Backend Collaboration Context、BBS、Task / Relay、BCS、公开 API consumers
- 本次交付：方案文档，不包含代码、数据库迁移或运行接口变更
- 文档位置：按用户要求放在 Backend specs，说明一个由 Backend 拥有、可跨 BBS / Task / API consumers 使用的上下文协作协议

## 0. 一句话方案

> 在单 Bot 和 BCN 协作群之外，新增一种以持久、可投影、可演进的 **Collaboration Context** 为中心的自主协作模式：上下文拥有目标、可见范围、材料和业务状态；一个可版本化、可扩展、但不能执行任意代码的状态机决定下一步；由事件驱动公开讨论、自主接力、规划执行、竞价上岗、直接指派五种同一维度的协作模式推进同一目标。

该模式与现有单 Bot 和 BCN 协作群并存，不是将二者升级或替换；也不是新建第二套执行调度系统。BBS 帖子、任务图等是产品形态，可以在产品层基于这种新的协作模式组织公开讨论、自主接力、规划执行、竞价上岗或直接指派；它们不是被 Context “绑定”的协作表面，而是在产品业务中采用同一套协作模式的能力。

## 1. 背景与问题

当前已有的自主协作形态主要是单 Bot 会话和 BCN 协作群。二者都能驱动 Bot 自主推进，但自主性分别锚定在“单个 Bot 的会话推理”和“一群 Bot 的消息协商 / 运行过程”上。BCN 协作群的前提是 Bot 参与同一个群，并感知其它 Bot 的存在、消息和分工；以上下文 Context 为中心的自主协作模式则把目标、材料、状态、事件和交付事实本身提升为协作中心，Bot 之间不需要彼此感知，每个 Bot 只需要感知 Context。

| 协作模式 | 协作中心 | 优点 | 与 Context 自主协作模式的差别 |
| --- | --- | --- | --- |
| 单 Bot 会话 | 一个 Bot 对目标、材料和当前任务的理解 | 适合边界清晰、所需能力集中、协作方很少的任务；启动直接、链路短，Bot 可以基于自己看到的材料推理、调用工具并连续自主推进，编排成本最低 | 上下文主要是模型在当前会话中的工作记忆，而不是显式领域对象；目标、验收、当前阶段、下一步和执行证据缺少独立权威模型，推进高度依赖 Bot 每轮推理，难以用统一事件和状态机解释“为什么允许下一步” |
| BCN 协作群 | 一组 Bot 的群消息、路由和运行过程，Bot 彼此可见、可感知并可依赖群内协商推进 | 适合多角色协商和分工；消息、成员、路由、Manager / Worker、StateMachine 等机制能支持多 Bot 自治互动；协作过程对群内参与者可见 | 协作以“Bot 感知 Bot”为前提；目标、材料、责任边界和业务阶段仍内嵌在群消息或某次 Run 的上下文中，不是一等 Context；讨论、执行、代办和验收容易退化为群内的片段性行为，缺少贯穿全过程的显式状态和事件事实 |
| Context 自主协作模式 | 持久 `CollaborationContext` | 把目标、材料、状态、事件和证据提升为协作中心；Bot 之间不需要彼此感知，每个 Bot 只感知 Context 的目标、输入、状态、Todo、外部执行事实和结果事实；状态机决定当前业务阶段、下一步和允许的 `CollaborationMode`；单 Bot、BCN 群、Relay、Todo 等是产品形态或执行能力，可在该协议下承载协作，并统一受状态和事件治理 | 抽象层级更高，需要协议建模、状态机校验、事件幂等、权限投影和适配器测试；它不直接提升 Bot 的模型能力或工具能力，而是提供更稳定、更可治理、可跨协作方式解释的自主协作载体 |

因此，Context 模式不是否定单 Bot 会话或 BCN 协作群；在合适的任务中，它们仍然是最直接的执行方式。Context 模式的增量价值是让 Bot 的自主行为不再依赖“感知其它 Bot”或参与一次群协商，而是只依赖一个持久、显式、可审计的 Context。

本方案提出的协作结构是：

```text
Collaboration Context 承载目标、输入来源、状态机绑定与交付验收事实；
State Machine 决定当前阶段、下一步和允许的 CollaborationMode；
CollaborationMode 统一承载当前协作形态与参与者集合，取值包含公开讨论、自主接力、规划执行、竞价上岗、直接指派五种；
ContextEvent 记录被接受的协作事实并驱动状态迁移。
```

## 2. 关键定义

### 2.1 Collaboration Context

`CollaborationContext` 是新的持久聚合根。它既不是现有 `TaskSpec.Context`，也不是传给 LLM 的一段 prompt 文本。现有 `agentclaw.community.core.task.domain.models.Context` 只是任务规格里的上下文数据，因此新概念必须使用 `CollaborationContext`，避免命名冲突。

它拥有：

1. 业务身份与作用域。
2. 目标、背景、约束、交付物和验收口径。
3. 责任人，以及 active `CollaborationMode` 中的参与者与可见性；授权本身仍由授权系统校验。
4. 可引用的工作材料，不保存字节。
5. 通过 active `ContextStateMachineBinding` 关联的业务状态与状态机定义版本。
6. 事件日志与版本号。
7. 可基于该协议接入的协作产品、执行载体或公开 API consumer，例如 BBS Topic、TaskGraph / Relay 执行、BCN 群。
8. 由 active `ContextStateMachineBinding` 持有的轻量代办清单和可升级为正式 Work 的事项。

### 2.2 Command / Event / State / Transition

Context 由事件推进，不由任何 Bot 直接改状态：

- `ContextCommand`：人类、系统或受信适配器提交的意图，例如“发布回复”“启动接力”“完成待办”“验收通过”。
- `ContextEvent`：服务端接纳后的不可变事实，例如 `contribution.posted`、`stage.completed`、`work.completed`、`todo.completed`。
- `State`：Context 当前业务阶段，例如 `draft`、`discussing`、`executing`、`reviewing`、`waiting`、`done`。
- `Transition`：显式状态迁移记录，包含来源状态、触发事件、命中规则、目标状态、守卫结果、操作者和迁移证据。
- `StateMachineDefinition`：版本化状态机合同；Context 绑定后冻结其 revision。

ContextEvent 命名分两层：

1. **全局标准协议事件**
   - 事件名只表达协议原语上的通用协作动作；产品、业务场景和具体阶段差异进入 payload。
   - 事件对象只能来自协议白名单：`context`、`participant`、`mode`、`contribution`、`stage`、`todo`、`work`、`artifact`、`outcome`。
   - 初始标准集包含 `context.created`、`context.info_updated` / `context.mode_switched` / `context.cancelled`、`participant.role_granted` / `participant.role_revoked`、`contribution.posted`、`stage.completed` / `stage.failed`、`todo.completed`、`work.requested` / `work.started` / `work.blocked` / `work.completed`、`artifact.submitted`、`outcome.accepted` / `outcome.rejected`。
   - 例如 BBS Post 映射为 `contribution.posted` + `payload.contribution_kind=forum_post`；编码完成不叫 `coding.completed`，而是 `stage.completed` + `payload.stage_id=coding`。
2. **Profile 扩展事件**
   - 只有标准事件 + payload 无法表达、且状态机裁决必须依赖该业务事实时才允许声明。
   - 必须由具体 `ContextStateMachineDefinition` 声明 payload schema，并自带 `<profile_namespace>.<event_name>` 形式；只在该 Profile 内合法，不进入全局标准事件集。
   - Adapter 的默认责任是把外部事实映射为标准事件 + 结构化 payload；不能把产品事件名直接提升为协议事件。

LLM 可以输出执行结果、摘要或建议，但不能直接改写 Context 状态。状态只能由服务端状态机根据已接纳事件确定地转换。

### 2.3 Context State

Context 只有唯一聚合级状态。该状态描述业务阶段和下一步协作方式，例如 `collecting_info`、`handoff_running`、`reviewing`、`done`，由状态机根据已接纳的 `ContextEvent` 推进。

外部系统或子实体的事实不构成本协议的第二类 Context State。适配器可以引用外部事实，但必须先由外部系统接纳并转换成 ContextEvent；Context 不能代表、覆盖或反向修改任何外部系统自己的运行事实。

## 3. 目标与范围

### 3.1 产品目标

- **G1** 新增基于 Context 的自主协作模式，并与单 Bot、BCN 协作群和 BBS 帖子并行，而不是把其中任何一种升级成 Context 模式。
- **G2** Context 具备可持久、可观察、可调试的业务状态。
- **G3** 状态机可自定义扩展，但校验、授权、边界和审计由服务端治理。
- **G4** Context 状态作为模式选择层，可按阶段在“公开讨论 / 自主接力 / 规划执行 / 竞价上岗 / 直接指派”之间切换，而不是提前把业务固化成一种运行时。
- **G5** 把协作模式收敛为“公开讨论 / 自主接力 / 规划执行 / 竞价上岗 / 直接指派”五种；单 Bot、BCN 群、BBS、TaskGraph 是产品形态或执行能力，可在产品层基于这五种模式组织协作，但它们本身不是协作模式。
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

## 4. 四层架构设计


本方案按“协作—状态—事件—表示”四层组织协议对象。四层不是四套并行模型，而是同一个 `CollaborationContext` 在不同治理关注点上的分解：

配套可视化图见同目录 [architecture.html](architecture.html)。图中四层、对象卡片与连线只作为结构视图；协议语义、字段和不变量以本节文字为准。

| 层次 | 核心对象 | 回答的问题 | 允许承担的职责 | 明确不承担的职责 |
| --- | --- | --- | --- | --- |
| **协作层** | `CollaborationMode`、`CollaborationModePolicy` | 谁可以参与、如何组织、谁执行、如何完成？ | 参与规模、可见性、协作规则、执行者选择与完成判定 | 不直接推进业务状态，不保存外部 Work 生命周期 |
| **状态层** | `ContextStateMachineDefinition`、`ContextStateMachineBinding` | 当前处于哪个业务阶段，下一步是否合法？ | 版本化状态定义、当前状态、合法迁移、Todo 与并发 fencing | 不定义参与者权限，不直接调用 BCS/Task/论坛 |
| **事件层** | `ContextEvent`、`ContextTransition` | 发生了什么，服务端为何允许这次迁移？ | append-only 事实、迁移裁决、守卫/效果结果、回放依据 | 不把外部系统的内部状态复制为 Context 状态 |
| **表示层** | `ContextEnvelope`、`CollaborationContext`、`CollaborationOutcome` | 这个协作目标是什么，当前对外应展示什么？ | 目标、背景、输入材料、责任人、当前模式/状态和验收产物 | 不承载权限本身，不保存 Work 节点或材料正文 |

以下是运行时处理链路，不是代码依赖方向；四层均为领域关注点，不能据图引入 Core → Adapter 的依赖：

```text
Adapter / Service API
        │ 接纳 Command，校验身份与作用域
        ▼
ContextEvent（事件层：不可变事实）
        │ 由 frozen Definition 计算
        ▼
ContextTransition（事件层：命中合法规则时生成，普通事实可无迁移）
        │ 更新 Binding；Mode 仅在 allowed_modes 与 Policy 同时满足时可切换
        ▼
ContextStateMachineBinding（状态层运行态）
        │ 与 Context 根在同一事务提交
        ▼
CollaborationContext / Snapshot（表示层：权威聚合与投影）
```

协作层的 `CollaborationModePolicy` 是“参与与执行规则”，状态层的 `ContextStateMachineDefinition` 是“阶段与迁移规则”；二者必须分离。状态机可以限制某个阶段允许哪些 `mode_kind`，但不能把参与规则、竞价规则或完成策略复制进每条状态迁移。`ContextEvent` 是唯一事实推进入口，`ContextTransition` 是合法状态或模式迁移的证明；任何适配器都不能直接写 `current_state`、`mode_kind` 或 `outcome`。


### 4.1 协作层：模式、参与者与策略

协作层对应架构图最上方的三块内容：`CollaborationMode` 描述当前采用什么协作方式，`CollaborationModePolicy` 描述该方式的规则，五种标准模式是 Policy 的受治理组合。它只回答参与、组织、执行和完成规则，不直接改写业务阶段。

### 4.1.1 CollaborationMode

`CollaborationMode` 把当前协作模式和参与者收敛成同一个子实体。`CollaborationContext` 根只持有一个 active `collaboration_mode`，不再直接持有 `current_mode` 或 `participants[]`。

```text
CollaborationMode
  ├─ mode_id / mode_kind
  ├─ participants[]            # ContextParticipant[]
  ├─ visibility                # ContextVisibility
  ├─ mode_policy               # CollaborationModePolicy，随 mode_revision 冻结的策略值对象
  ├─ mode_revision / started_at / ended_at
  └─ mode_switched_event_refs[]
```

`visibility` 的基础取值只保留三类，作用域都在 `ContextScope` 之内：

- `public`：对 scope 内所有人可见
- `workspace`：对 scope 内某个 workspace 可见
- `participants`：仅对当前 mode 的 participants 和责任人可见

设计约束：
- 一个 Context 同一时刻只能有一个 active `CollaborationMode`。
- `mode_kind` 只能取 `public_discussion`、`autonomous_relay`、`planning_execution`、`competitive_assignment`、`direct_assignment`。
- `public_discussion` 允许 `ContextScope` 内所有已授权参与者参与。
- `autonomous_relay` 与 `planning_execution` 只允许显式加入该 mode 的部分参与者参与。
- `competitive_assignment` 与 `direct_assignment` 同时至多有一个 active executor；其余 actor 可承担发起、授权、竞价、观察或验收角色，但不能并行执行。
- mode 与 participants 变更必须通过 `context.mode_switched`、`participant.role_granted`、`participant.role_revoked` 事件进入事实日志。
- 当前 mode 受状态机 binding 的 `allowed_modes` 约束，不能绕过 `ContextTransition` 直接修改。
- visibility 属于当前 `CollaborationMode`，不是 `ContextEnvelope` 或 `scope`。
- participants 是协作关系，不是权限本身。


### 4.1.2 CollaborationModePolicy

`CollaborationModePolicy` 把协作形态展开为四个明确的策略维度，由当前 Mode 通过 `mode_policy` 持有。它不是权限系统、运行时调度器或独立策略中心；首版不新增 Policy ID、策略注册 API 或任意组合 DSL，策略快照随 `mode_revision` 冻结，避免形成另一套工作流。

```text
CollaborationModePolicy
  ├─ participation_policy     # 谁可以参与：scope 内开放 / 选定参与者 / 竞价者 / 被指派者
  ├─ coordination_policy      # 如何组织：开放贡献 / 顺序移交 / 先规划后执行 / 竞价择一 / 直接选定
  ├─ executor_policy          # 执行基数：无独占者 / 每阶段一人 / 按计划分工 / 单一中标者 / 单一受派者
  └─ completion_policy        # 完成条件：结论 / 阶段证据 / 计划交付物 / 单人交付，再经责任人裁决
```

五种 Mode 与策略的标准组合以第 6.1 节为准，不能只修改某一维而保留不相容的 `mode_kind`。例如竞价模式允许多个 bidder，但中标前没有 executor，中标后至多一个 active executor；不能把“单人执行”误解成“只有一人可以参与”。

设计约束：
- `participation_policy` 只定义协作准入条件；身份、scope、材料访问和执行授权仍由已注册的授权 hook 独立校验。
- `coordination_policy` 表达移交或分工规则，具体规划、派发、恢复与运行事实继续由 Task / Relay / BCS 拥有。
- `executor_policy` 约束当前阶段或计划分工的 executor 基数，不等于整个 Mode 的 participant 总数；只读观察者和验收人不计入执行者。
- `completion_policy` 规定可交付 / 可验收的证据条件，不自动产生 `outcome.accepted`；最终裁决仍需责任人命令和状态机合法迁移。
- 切换 Mode 时原子校验目标状态 `allowed_modes`、目标 Policy、参与者角色与未结束外部 Work；旧 Work 未完成交接或停止前，不得按新 Policy 启动冲突执行。


### 4.1.3 ContextParticipant

`ContextParticipant` 记录人类、Bot 或系统服务在当前 `CollaborationMode` 中的参与身份和角色。它不是权限模型，而是“谁在这个 Mode 中，何时被授权/撤销，以及承担哪些协作角色”的事实载体。

```text
ContextParticipant
  ├─ participant_id / actor_kind / actor_id
  ├─ roles[] / scope
  ├─ granted_by / granted_at
  ├─ revoked_by / revoked_at
  ├─ membership_events[]
  └─ visited_at
```

各字段语义如下：

| 属性 | 具体含义 |
| --- | --- |
| `participant_id` | 当前 `CollaborationMode` 内的**成员记录 ID**，不是全局用户 ID；用于定位这条参与关系 |
| `actor_kind` | 参与者身份类型，例如 `human` / `bot` / `system`；决定后续用哪套身份系统验证 |
| `actor_id` | 在对应身份系统中的稳定 actor ID，例如用户 ID、已验证 Bot ID、系统服务 ID |
| `roles[]` | 该参与者在当前 Mode 内承担的协作角色，例如 `owner`、`responsible_user`、`info_editor`、`executor`、`observer` |
| `scope` | 该参与关系的**业务作用域**，通常与当前 `ContextScope` / `CollaborationMode` 对齐；不是可见性，也不是权限本身 |
| `granted_by` | 授权该参与者的 actor |
| `granted_at` | 参与关系被授权的时间 |
| `revoked_by` | 撤销该参与者的 actor |
| `revoked_at` | 参与关系被撤销的时间 |
| `membership_events[]` | 对应的 `ContextEvent` 引用列表，例如 `participant.role_granted` / `participant.role_revoked`，用于审计与回放 |
| `visited_at` | 最近一次读取或操作时间，仅服务于未读、活跃度、在线状态等投影，不作为参与事实 |

补充说明：
- `revoked_at` 为空且当前 mode 仍有效时，该 participant 视为 active。
- `roles[]` 是协作角色，不是权限清单；它用于状态机 guard、可用动作和展示分组。
- `membership_events[]` 只保存事件引用，不直接保存可变 JSON 明细。
- `visited_at` 不参与裁决，不能作为参与权的证明。
- 每次 command 仍须独立校验身份、作用域、材料授权和执行能力，不能只依赖 participant 记录放行。

- 同一作用域内一个 actor 只有一个 active participant。
- Bot 参与者必须记录可验证的 Bot ID、Owner 或服务归属，不能由 Bot 自称。
- 参与关系不是权限本身，每次命令执行前仍须重新校验身份、材料和执行能力。


### 4.2 状态层：定义、绑定与阶段 Todo

状态层对应架构图中的 `ContextStateMachineDefinition` 和 `ContextStateMachineBinding`。前者是不可变的状态机合同，后者是某个 `CollaborationContext` 对该合同的运行绑定。状态层负责回答当前处于哪个业务阶段、下一步是否合法；它不负责决定谁有权限参与。

### 4.2.1 ContextStateMachineDefinition

`ContextStateMachineDefinition` 是可共享、声明式、可版本化的状态机合同。它是 Context 绑定的静态定义，不是运行态，也不是新的聚合根。

```text
ContextStateMachineDefinition
  ├─ definition_id
  ├─ definition_revision
  ├─ schema_version
  ├─ content_digest
  ├─ display_name / description
  ├─ initial_state
  ├─ states[]                  # StateDefinition[]
  ├─ transitions[]             # TransitionDefinition[]
  ├─ declared_guards[]         # 只允许已注册 guard 名称
  ├─ declared_effects[]        # 只允许已注册 effect 名称
  ├─ compatibility_policy
  └─ registered_by / registered_at
```

```text
StateDefinition
  ├─ state_id / governance_kind
  ├─ display_name
  ├─ allowed_modes[]
  ├─ required_roles[]
  ├─ allowed_actors[]
  └─ entry_actions[]           # 声明式 effect 名称
```

```text
TransitionDefinition
  ├─ rule_id                   # 定义内唯一；与运行记录 transition_id 区分
  ├─ from_states[] / to_state
  ├─ event_name
  ├─ guard_refs[]
  └─ effect_refs[]
```

设计约束：
- 身份与相等性由 `definition_id + definition_revision + content_digest` 决定；发布后的 revision 不可原位修改，内容变化必须创建新 revision。
- 该对象只保存状态、迁移、guard / effect 引用和兼容策略，不保存任何 Context 的运行事实。
- `current_state`、`transition_cursor`、`state_fencing_token`、Todo 等运行态事实只能由 `ContextStateMachineBinding` 保存。
- guard 和 effect 都是注册后的白名单名称，不能包含脚本、SQL、网络调用、文件访问或框架 import。
- Context 不能直接持有 mutable definition；只能通过 binding 冻结某个 `definition_id + definition_revision + content_digest`。
- `compatibility_policy` 声明该 revision 是否允许已有 Context 显式迁移，以及迁移时必须满足的校验。
- 第 5.1 节的 YAML 可以视为该对象的声明式序列化形式；字段名可在实现前细化，但语义不变。


### 4.2.2 ContextStateMachineBinding

`ContextStateMachineBinding` 是 Context 与某个 frozen state machine revision 的状态裁决绑定。它负责状态阶段、迁移并发及当前阶段的 Todo，不重复持有目标、参与者或外部 Work 的运行事实。

```text
ContextStateMachineBinding
  ├─ context_id / binding_id
  ├─ definition_id / definition_revision / content_digest
  ├─ current_state
  ├─ allowed_modes             # 从 frozen Definition + current_state 派生，只读
  ├─ transition_cursor         # 状态机已处理到的事件位置
  ├─ state_fencing_token       # 状态迁移 fencing token
  └─ todos[]                    # ContextTodo[]
```

设计约束：
- 根只保留聚合级 `version` 与 `event_cursor`。`event_cursor` 是 Context 内 append-only 事件日志的末端位置，不保存完整事件列表。
- `transition_cursor` 表示状态机已检查到的事件位置，即使事件没有触发迁移也会前进；`event_cursor` 是已接纳事实末端。首版同步裁决并同事务提交，对外可见时两者一致，不留下未裁决状态窗口。
- `state_fencing_token` 只属于 Binding，在业务状态 / 模式切换（含同状态模式切换）时递增，防止旧阶段或旧 Mode 的执行回报推进新阶段。Todo 绑定创建时的 state/token；迁移时完成、取消或显式结转，不能把未完成必需 Todo 静默丢弃。
- 变更状态必须由服务端状态机基于 ContextEvent 与本 binding 的 frozen revision 裁决。


### 4.3 事件层：事实与迁移证明

事件层对应架构图中的 `ContextEvent` 和 `ContextTransition`。`ContextEvent` 记录已经被服务端接纳的事实；`ContextTransition` 记录状态机基于事实作出的迁移证明。图中从 `ContextTransition` 指向 `ContextEvent`，表示迁移证明引用触发它的事实，而不是事件被迁移对象拥有。

### 4.3.1 ContextEvent 与 ContextTransition

事件层把“事实”和“裁决”分开：

- `ContextEvent` 是服务端已接纳的不可变事实，至少包含 `event_id`、`event_kind`、`actor_ref`、`visibility`、`payload`、`request_id`、`source_event_id`、`sequence` 和 `occurred_at`。其中 `payload` 必须按事件 schema 校验，不能携带 token、私有路径或未授权原文。
- `ContextTransition` 是状态机针对某个已接纳事件产生的不可变裁决，至少包含 `transition_id`、`event_kind`、`from_state`、`to_state`、`rule_id`、`guard_results`、`effect_results` 与 `accepted_event_id`（连同迁移前后 fencing token）。`effect_results` 只记录本地效果及已提交的 outbox 引用，外部执行结果由后续事实报告，不回写不可变 Transition。它回答“为什么状态从这里到那里”，不是第二份状态。
- `ContextStateMachineBinding.current_state` 是当前快照；`ContextTransition` 是历史证明；二者不能互相替代。
- 一个已接纳事件产生零或一条成功的 Transition：普通贡献可仅写事实；命中一条合法规则才记录迁移。guard FAIL / BLOCKED 记入命令裁决 / 审计，不伪造成功 Transition；同一事件不得触发多条竞争规则。
- 重复请求返回原结果，不再追加事件；未接纳的冲突只留拒绝审计。已接纳但过期的外部事实可保留为历史证据，不能凭旧 fencing token 推进当前阶段。

回放时按 `ContextEvent.sequence` 应用已保存的事实与迁移裁决（包括没有 Transition 的事件），不得重新授权或执行外部效果；对外读取则从 `CollaborationContext` 生成按可见性过滤的 Snapshot / Timeline。


### 4.4 表示层：输入、聚合根与结果

表示层对应架构图底部的三块内容：`ContextEnvelope` 表达协作目标和输入，`CollaborationContext` 是唯一聚合根，`CollaborationOutcome` 保存责任人的结果裁决。这里的“表示”不是前端展示层；`CollaborationContext` 仍然是领域权威对象，`ContextSnapshot` 才是面向消费者的只读投影。

### 4.4.1 CollaborationContext

`CollaborationContext` 是唯一业务权威，负责回答：

1. 这个目标是什么、边界和验收口径是什么。
2. 谁是责任人，当前协作模式里的参与者和可见范围是什么。
3. 哪些材料是协作输入，哪些交付物是协作结果。
4. 当前业务阶段为什么合法、下一步允许什么。
5. 当前协作模式是什么，以及有哪些产品或执行能力可以继续推进任务。

```text
CollaborationContext
  ├─ context_id
  ├─ scope                    # ContextScope
  ├─ title
  ├─ envelope                 # ContextEnvelope
  ├─ responsible_actor_ref
  ├─ collaboration_mode       # CollaborationMode，持有 visibility 与 mode_policy
  ├─ state_machine_binding    # ContextStateMachineBinding，持有 transition_cursor / state_fencing_token / ContextTodo[]
  ├─ outcome                  # 最近一次 CollaborationOutcome；首次裁决前允许缺失
  ├─ version                  # 聚合乐观锁版本
  ├─ event_cursor             # Context 事件日志位置：最近接纳事件的 sequence
  └─ created_by / created_from / created_at / updated_at / closed_at
```

`ContextScope` 不是可见性，而是 Context 所属的**授权与作用域边界**。

```text
ContextScope
  ├─ scope_kind             # open / workspace / personal
  ├─ scope_id               # 该 kind 下的稳定对象 ID
  └─ environment            # 运行/部署环境
```

取值说明：
- `open`：开放工作空间。
- `workspace`：项目空间、工作空间、协作空间，例如 Dima 项目空间或 TaskDiscover 事件集合所在空间。
- `personal`：个人任务、私人待办或单个用户私有的 Context。

`ContextScope` 解决的是 **归属哪儿**，不是 **谁能看见**；可见性仍由 `CollaborationMode.visibility` 与授权系统控制。

聚合根不变量：
- 一个 Context 只有一个聚合级业务状态；该状态只能由已接纳事件和状态机裁决。
- 输入、交付物、协作模式、Todo、Outcome 和来源引用等事实不能直接改状态，必须形成合法 `ContextEvent`。ContextTodo 属于 active state machine binding，不是根级独立字段。
- 子实体和值对象不能跨 Context 隐式共享，也不能绕过 `CollaborationMode.visibility` 与 `scope`。
- 终态后可继续追加公开事件，但不能自动改变终态。
- `responsible_actor_ref` 必须由授权主体和现有身份系统确认，不能由 Bot 自主声明。


### 4.4.2 ContextEnvelope

`ContextEnvelope` 是业务意图、约束、验收口径、协作输入来源和输入材料引用的统一权威值对象。`InputSource` 与 `ContextMaterialReference` 都从这里持有，不再独立建 `ContextInput` 实体。

```text
ContextEnvelope
  ├─ objective / background
  ├─ input_sources[]          # InputSource[]
  ├─ material_refs[]           # ContextMaterialReference[]
  ├─ constraints[] / deliverable_specs[]
  ├─ acceptance[] / language / locale
  ├─ origin_ref
  └─ envelope_revision
```

`InputSource` 用 `source_kind + source_id + revision` 标识一个外部来源版本，例如：

- Dima 项目空间
- TaskDiscover 模块自动挖掘的事件集合

`ContextMaterialReference` 用 `source_kind + source_id + revision / digest` 稳定引用一份具体材料；它只保存引用、版本和可见范围，不保存材料正文、token、cookie 或私有路径。

设计约束：
- `input_sources[]` 和 `material_refs[]` 共同表达“协作输入”。
- 输入信息属于 envelope revision，没有独立生命周期，也不作为新的子实体存在。
- 每次可审计变更必须产生 `context.info_updated` 事实。
- 它是业务意图和输入边界，不是 prompt，也不是授权边界。
- 字段必须经过 schema 校验，不允许无限开放 JSON。


### 4.4.3 CollaborationOutcome

`CollaborationOutcome` 是本轮协作的验收裁决对象；`ContextDeliverable` 从这里持有，不再由 `CollaborationContext` 根直接保存。

```text
CollaborationOutcome
  ├─ outcome_id
  ├─ decision                     # accepted / rejected
  ├─ judged_by / judged_at
  ├─ reason_ref
  ├─ deliverables[]              # ContextDeliverable[]
  └─ visibility
```

`ContextDeliverable` 是 `CollaborationOutcome` 内的产出事实，字段可以精简为：

```text
ContextDeliverable
  ├─ deliverable_id / spec_ref
  ├─ artifact_ref
  ├─ produced_by / produced_at
  ├─ producing_event_refs[] / evidence_refs[]
  └─ acceptance_ref
```

设计约束：
- 一个 Context 同一时刻只有一个 active `CollaborationOutcome`；一个 `CollaborationOutcome` 通过 `deliverables[]` 表达多个协作产物，不需要在根上再保存 `outcomes[]`。
- 验收通过、驳回或返工都必须通过 `outcome.accepted` / `outcome.rejected` 完成。
- 尚未裁决时 `outcome` 可以缺失，这是合法领域状态；候选产物通过 `artifact.submitted` 事件和投影展示。裁决时将所选产物引用、证据与 accepted / rejected 一并冻结进 Outcome，不伪造 judged_by / judged_at。
- `ContextDeliverable` 不再直接挂在根上，也不单独驱动状态机。
- 根只持有一个 active `outcome`，交付物由该 Outcome 持有；历史或驳回后的 Outcome 通过 append-only `ContextEvent` 与迁移/裁决事实审计，不作为根上的活跃集合。Outcome 的 visibility 只能在 CollaborationMode.visibility 基础上收窄，不能扩大。


### 4.5 跨层运行机制：一条命令如何贯穿四层

四层是领域职责的横向分解，不是“协作层调用状态层、状态层调用事件层、表示层最后被动接收”的代码调用栈。一次命令会同时受到身份、协作 Policy、状态 Definition、当前 Binding 和材料授权的约束；通过裁决后，事实、迁移、运行态和聚合快照在同一事务内形成一致结果。

一次典型的运行链路如下：

1. **表示层接收目标与输入**：`ContextEnvelope` 提供 objective、背景、材料引用和验收口径；`CollaborationContext` 记录责任人和当前聚合版本。
2. **协作层确定参与方式**：服务端校验当前 `CollaborationMode`、participants、visibility 和冻结的 `CollaborationModePolicy`，确认谁可以提交该命令、谁可以执行或验收。
3. **事件层接纳事实**：Adapter / Service API 将命令转换为结构化 `ContextEvent`。适配器不能直接写 `current_state`、`mode_kind` 或 `outcome`。
4. **状态层进行裁决**：服务端使用当前 Binding 引用的 frozen Definition，按 `current_state`、`allowed_modes`、guard 和 fencing token 判断是否产生零或一条 `ContextTransition`。普通贡献可以只有事件而没有迁移。
5. **表示层更新权威快照**：合法结果原子更新 Binding、Mode、Todo、Outcome 和聚合 `version / event_cursor`，再生成对外 Snapshot / Timeline 投影。
6. **外部系统异步承接**：Task / Relay / BCS 等系统只拥有自己的 Work 生命周期；外部事实经 Adapter 转换为后续 `ContextEvent`，不能反向直接修改 Context。

架构图中的关系因此可以这样理解：

- `CollaborationMode → CollaborationModePolicy`：当前 Mode 持有冻结的策略值对象；五种模式是标准 Policy 组合。
- `ContextStateMachineDefinition → ContextStateMachineBinding`：运行 Context 绑定某个不可变 Definition revision。
- `ContextTransition → ContextEvent`：迁移证明引用触发它的已接纳事实；一个事件可以没有迁移证明。
- `CollaborationContext → ContextEnvelope / CollaborationOutcome`：聚合根持有输入边界和当前结果裁决。

跨层不变量：

- Policy 不能复制状态迁移规则；Definition 不能复制参与者和执行授权规则。
- State 不能提升权限，Mode visibility 不能替代 authorization。
- Event 是事实唯一入口；任何状态、模式和结果变化都必须能回溯到事件。
- Snapshot 可按消费者重建，不能反过来成为 Context、Binding 或 Outcome 的权威来源。


### 4.6 对象清单与权威归属

前面按架构层解释职责，下面保留按领域对象查询的清单，方便实现和评审时快速定位身份、权威关系与生命周期。该清单是四层设计的索引，不是第五层，也不改变前述对象归属。

`CollaborationContext` 是本协议唯一的聚合根。其它对象一律不是新的聚合根，而是聚合内的子实体、值对象、版本化协议资源或只读投影。子实体 ID 都必须带 `context_id` 语义，不能脱离 Context 被单独共享；值对象没有独立生命周期，只能随所属 Context 或命令事实变更。

| 对象 | 类型 | 主要身份 / 相等性 | 权威关系 | 生命周期 |
| --- | --- | --- | --- | --- |
| `CollaborationContext` | 聚合根 | `context_id` | 唯一业务权威 | 显式创建、推进、取消或验收 |
| `ContextEnvelope` | 值对象 | 当前 envelope revision，内容相等 | 由 Context 拥有，统一承载目标、输入来源与输入材料引用 | 随 `context.info_updated` 事实演进 |
| `InputSource` | 值对象 | `source_kind + source_id + revision` | 由 ContextEnvelope 持有，标识 Dima 项目空间、TaskDiscover 模块自动挖掘的事件集合等外部来源 | 随 envelope 事实演进 |
| `ContextMaterialReference` | 值对象 | `source_kind + source_id + revision` | 由 ContextEnvelope 持有，稳定引用具体输入材料，不拥有内容 | 随 envelope 事实演进 |
| `ContextDeliverable` | 子实体 | `context_id + deliverable_id + spec_ref` | 由 CollaborationOutcome 持有，记录这一轮协作结果，与输入材料分离 | 由产出事件建立，随验收事实归档 |
| `CollaborationMode` | 子实体 | `context_id + mode_id` | 承载当前协作形态、参与者集合、visibility 与策略快照 | 随模式切换事件演进 |
| `CollaborationModePolicy` | 值对象 | 四项策略内容相等；随 mode_revision 冻结 | 由 Mode 持有，定义参与、协调、执行者和完成规则 | 无独立生命周期；随合法模式切换替换 |
| `ContextParticipant` | 子实体 | `participant_id`，作用域内 actor 唯一 | 记录参与关系，不替代权限系统 | 授权、撤销、重新授权 |
| `ContextStateMachineDefinition` | 版本化协议资源 | `definition_id + definition_revision + content_digest` | 共享状态机合同 | 注册后不可原位修改 |
| `ContextStateMachineBinding` | 子实体 | `context_id + binding_id` | 绑定 Context 与 definition revision，管理 current_state、transition_cursor、state_fencing_token，并持有当前状态下的 ContextTodo[] | 创建时绑定，显式迁移 revision |
| `ContextEvent` | 子实体 | `event_id`；来源幂等键唯一 | append-only 事实 | 永不修改，可用补偿事件 |
| `ContextTransition` | 子实体 | `context_id + transition_id`；`accepted_event_id` 唯一 | 一次状态迁移裁决证明 | 由服务端确定性生成，永不变更 |
| `ContextTodo` | 子实体 | `context_id + binding_id + todo_id` | 由 ContextStateMachineBinding 持有，表达当前状态下的轻量代办事实 | 创建、完成、阻塞或取消事件 |
| `CollaborationOutcome` | 子实体 | `context_id + outcome_id` | 保存验收裁决与该轮 deliverables[] | 责任人显式裁决 |
| `ContextSnapshot` | 只读投影 | `snapshot_id + snapshot_revision` | 非权威快照 | 按目标重新生成，可过期 |


## 5. 可扩展状态机

### 5.1 状态机合同

`ContextStateMachineDefinition` 的声明式序列化 schema 采用 `context-state-machine/1`；状态机运行绑定通过第 4.2 节定义的对象承载。以下示例将 `states[]` 编码为以 state_id 为键的映射；省略展示字段，`content_digest`、注册人和注册时间由注册服务生成。`new_contexts_only` 表示仅供新 Context 绑定，不隐式升级既有 Binding。

```yaml
schema_version: context-state-machine/1
definition_id: sequential_relay_profile
definition_revision: 1
compatibility_policy: new_contexts_only
declared_guards:
  - info_is_complete
  - target_mode_is_relay
  - mode_policy_is_valid
  - no_conflicting_work
  - actor_can_start_work
  - current_mode_is_relay
  - work_event_matches_request
  - completion_policy_is_satisfied
  - actor_is_responsible_user
  - actor_can_cancel
  - no_active_external_work
declared_effects: [ensure_work_request]
initial_state: draft
states:
  draft:
    governance_kind: waiting
    allowed_modes: [public_discussion]
  ready:
    governance_kind: active
    allowed_modes: [public_discussion, autonomous_relay]
  handoff_running:
    governance_kind: active
    allowed_modes: [autonomous_relay]
  reviewing:
    governance_kind: review
    allowed_modes: [autonomous_relay]
  needs_changes:
    governance_kind: waiting
    allowed_modes: [autonomous_relay]
  done:
    governance_kind: terminal_success
    allowed_modes: [autonomous_relay]
  cancelled:
    governance_kind: terminal_cancelled
    allowed_modes: [public_discussion, autonomous_relay]
transitions:
  - rule_id: information_ready
    from_states: [draft]
    event_name: context.info_updated
    guard_refs: [info_is_complete]
    to_state: ready
  - rule_id: select_relay
    from_states: [ready]
    event_name: context.mode_switched
    guard_refs: [target_mode_is_relay, mode_policy_is_valid, no_conflicting_work]
    to_state: ready
  - rule_id: start_work
    from_states: [ready, needs_changes]
    event_name: work.requested
    guard_refs: [actor_can_start_work, current_mode_is_relay, no_conflicting_work]
    to_state: handoff_running
    effect_refs: [ensure_work_request]
  - rule_id: work_ready_for_review
    from_states: [handoff_running]
    event_name: work.completed
    guard_refs: [work_event_matches_request, completion_policy_is_satisfied]
    to_state: reviewing
  - rule_id: accept_outcome
    from_states: [reviewing]
    event_name: outcome.accepted
    guard_refs: [actor_is_responsible_user, completion_policy_is_satisfied]
    to_state: done
  - rule_id: reject_outcome
    from_states: [reviewing]
    event_name: outcome.rejected
    guard_refs: [actor_is_responsible_user]
    to_state: needs_changes
  - rule_id: cancel_when_stopped
    from_states: [draft, ready, handoff_running, reviewing, needs_changes]
    event_name: context.cancelled
    guard_refs: [actor_can_cancel, no_active_external_work]
    to_state: cancelled
```

### 5.2 状态治理类别

每个自定义状态必须选择一个系统治理类别：

| 类别 | 语义 | 展示 / 计算约定 |
| --- | --- | --- |
| `waiting` | 等待人类补充、外部输入或选择 | 会进入待办通知与 blockers |
| `active` | 协作或执行进行中 | 必须声明 `allowed_modes` |
| `review` | 等待人验收或审批 | 默认只有明确责任人可裁决 |
| `blocked` | 被显式阻塞 | 必须携带 blocker |
| `terminal_success` | 目标完成 | 后续只能派生只读投影 |
| `terminal_cancelled` | 主动取消 | 保留历史事实 |
| `terminal_failed` | 无法继续 | 需要人工决定是否重建 |

允许模板增加自定义状态 ID 和经声明的扩展事件，类别必须映射到上述治理类别。`initial_state` 单独指定起点；治理类别只提供通知和终态等治理语义，不能替代命令授权。

### 5.3 守卫与效果

状态机必须保持声明式：

- `guard_refs` 只能引用允许清单中的纯谓词，例如 `info_is_complete`、`actor_is_responsible_user`、`required_todos_are_done`、`work_event_matches_request`、`source_revision_is_current`。
- `effect_refs` 只能引用已注册的领域动作 / 投递意图，例如 `ensure_work_request`、`publish_snapshot`、`notify_responsible_user`、`close_topic`。
- 守卫返回明确 `PASS`、`FAIL`、`BLOCKED` 事实，禁止静默失败。
- 本地领域效果、Event、Transition、Binding 和 outbox 意图同事务提交；任何写失败均回滚并向上传播。外部效果在提交后投递，失败只更新投递事实，不回滚已经接纳的状态。
- 投递采用至少一次重试及消费端幂等，不承诺网络副作用天然 exactly-once；只有明确失败事件命中合法规则时才进入 `blocked`，不能由 worker 直接改状态。
- 自定义状态机可以引用外部模型或规则结果，但必须把结果和证明记录在事件 payload 中，不能重复调用外部系统。

禁止出现任意脚本、内联 SQL、网络调用、文件访问或框架 import。BCS StateMachine YAML 是执行层合同，Context 状态机合同只定义业务阶段；两者通过 `CollaborationMode` 与 `work.*` 事件事实关联。

### 5.4 定义校验

状态机注册或参考复制时必须校验：

1. `initial_state` 恰好指定一个已定义的起点；起点不是独立 governance_kind，可同时为 waiting / active。
2. 所有状态必须从 `initial_state` 可达。
3. 每个非终态至少有一条可触发的迁移或显式等待策略。
4. 每个 `terminal_*` 状态不能有出边。
5. `from_states` / `to_state` 引用已定义状态；`event_name` 来自标准集或本 Profile 声明。
6. 每条规则有唯一 `rule_id`；同 state / event 命中多个 guard 时拒绝裁决，不能依赖列表顺序。
7. 所有 guard / effect 已注册，mode / mode_policy 组合合法；目标状态必须容纳迁移后的 active Mode。
8. 没有未知字段。
9. schema、最大节点数、字符串和扩展 payload 有明确上限。
10. Content digest 和 revision 一起保存，已发布版本被 Context 绑定后不可原位修改。
11. 升级状态机定义必须显式声明兼容策略；不声明时只能影响之后创建的新 Context。

## 6. 协作模式与产品能力

### 6.1 协作模式

`mode_kind` 是协作形态标识，`mode_policy` 是该形态的规则快照。五种形态处于同一维度，但参与人数与执行人数必须分别描述：

| 模式 | participation_policy（谁参与） | coordination_policy（如何组织） | executor_policy（谁执行） | completion_policy（如何完成） |
| --- | --- | --- | --- | --- |
| `public_discussion` 公开讨论 | scope 内所有获授权 actor 可贡献 | 开放讨论与贡献，不要求相互感知 | 无独占执行者；多人可贡献，不取得执行 Work 的独占权 | 汇总贡献与结论证据，由责任人接受 / 拒绝 |
| `autonomous_relay` 自主接力 | 选定或合法认领的部分参与者 | 顺序移交，逐阶段接力 | 每阶段一个 active executor；交接后可换人 | 前序阶段证据完整、必需阶段完成，再由责任人裁决 |
| `planning_execution` 规划执行 | 选定的部分参与者 | 先提交并确认计划，再按计划分工 | 允许计划明确的多个 executor；串并行由外部计划执行能力管理 | 计划对应必需交付物与验证证据齐备，再由责任人裁决 |
| `competitive_assignment` 竞价上岗 | 多个获授权 bidder 可竞价 | 提交方案 / 报价，由获授权主体裁定中标者 | 中标前零 executor，中标后单一 active executor | 中标者提交约定交付物，再由责任人裁决 |
| `direct_assignment` 直接指派 | 获授权主体指定执行者，其他角色可观察 / 验收 | 直接选定，不经过竞价 | 同时至多一个 active executor | 受派者提交约定交付物，再由责任人裁决 |

设计不变量：
- 单 Bot、BCN 群、BCS Run、TaskGraph、API Consumer 是产品形态或执行能力，不是新的协作模式。
- `public_discussion` 只表示公开讨论协作形态，BBS Topic 只是它的一种入口。
- `autonomous_relay` 表示顺序接力、阶段间移交；不要求一定使用名为 Relay 的实现。
- `direct_assignment` 表示“直接指定执行者”，不要求一定有多 Bot，也不要求一定有群；具体任务可由 `ContextTodo`、单条 Work 或参与者直接执行来承载。
- 一个 Context 可在状态迁移中在五种模式之间切换，但同一时刻只有一个主模式。

### 6.2 产品层与执行能力

以下产品或执行能力可以在产品层基于本协议组织协作；它们不是协作模式，也不是被 Context 绑定的“协作表面”，而是产品层采用这套协作模式后的具体承载能力。

| 产品 / 执行能力 | 协议视角的职责 |
| --- | --- |
| BBS Topic | BBS 产品可基于 `public_discussion` 模式呈现公开讨论、材料同步和结果 summaries |
| Context portal / API | 为消费者提供 Context 快照、命令提交和状态查看 |
| Task / Relay / TaskGraph | Task 产品可基于 `autonomous_relay` / `planning_execution` 模式组织执行，并回传已接纳的事实 |
| BCS session / group | 可作为 Bot 或群产品基于 `autonomous_relay` / `planning_execution` / `direct_assignment` 模式参与协作 |
| Competitive assignment adapter | 可承载 `competitive_assignment` 模式的竞价上岗、获胜与执行事实回流 |
| Direct assignee | 可承载 `direct_assignment` 模式的 Human / Bot / 代理执行者 |
| External source channel | 承载外部来源阅读、修订同步、回写能力 |

这些产品或执行能力只提交标准 Command 和 ContextEvent，不直接修改 Context 状态，也不提升权限。

Work 归属边界：

- Context 根不保存当前 Work 的反向引用。
- Task / Relay / TaskGraph / BCS 等外部 Work 自己保存 Context 引用、请求幂等键和执行生命周期；它们是 Work 事实的唯一权威。
- Adapter 只把外部 Work 已接纳的启动、阻塞、恢复、取消、完成等事实转换为 `work.*` ContextEvent；事件中的 `work_ref` 与 request 引用只作为审计和裁决输入。
- `work.completed` 等事件必须与当前 Context 已接纳的 `work.requested` request / work 引用匹配，不能因为到达即推进状态。

### 6.3 模式选择规则

1. 每个 State 必须声明 `allowed_modes`，它只负责限制阶段可用的协作形态；参与规则由当前 Mode 持有的 Policy 决定。取值只来自 `public_discussion`、`autonomous_relay`、`planning_execution`、`competitive_assignment`、`direct_assignment`。
2. 模式切换只能通过 ContextCommand 和对应 Transition 完成。
3. Context 根上的 `collaboration_mode` 统一包装当前模式和 participants；根上不保存当前 Work 的反向引用。
4. 模式切换必须留下 `context.mode_switched` 事实，禁止只更新表字段而不写事件。
5. 产品或执行能力可以并存，但不能替代协作模式；例如 BBS Topic 和 BCS group 同时存在时，仍需要明确当前是 `public_discussion`、`autonomous_relay`、`planning_execution`、`competitive_assignment` 还是 `direct_assignment`。
6. executor 基数遵循第 6.1 节 Policy：接力每阶段一人、规划按计划分工、竞价与指派同时至多一人。四者都通过 `work.requested` / `work.*` 事件推进；外部 Work 自己保存 Context 引用并维护执行生命周期。
7. `ensure_work_request` 只把已接纳的 `work.requested` 转换成幂等 outbox 意图，不重复生成同一事件或直接调用底层执行系统。
8. 外部系统接纳的执行事实必须经 Adapter 转换为标准 `ContextEvent`。
9. 模式本身不代表权限，执行前仍须独立校验身份、作用域、材料授权和主体归属。

## 7. Service API 与协议边界

### 7.1 Backend 域位置

本方案不新增顶层 `collaboration_context` 域，也不再把实现挂在 `task/forum` 或 `task/bbs` 下面；Service API、核心模块、内部 HTTP 与 OpenAPI 公开面都收敛在 `task/context_collab` 这个 `task` 直接子目录中。核心模块目录直接映射第 4 节的四层架构，外部来源相关能力统一放在 `core/task/context_collab/source` 下。

```text
apps/backend/src/agentclaw/community/api/task/context_collab/
  context_service.py               # Service API re-export，仅回引 core 内的 Protocol

apps/backend/src/agentclaw/community/core/task/context_collab/
  context_service_protocol.py       # transport-agnostic contract，由 core 拥有并在 api re-export
  collaboration/
    mode.py                         # 协作层：CollaborationMode
    mode_policy.py                  # 协作层：CollaborationModePolicy
    participant.py                  # 协作层：ContextParticipant
  state/
    definition.py                   # 状态层：ContextStateMachineDefinition
    binding.py                      # 状态层：ContextStateMachineBinding
    todo.py                         # 状态层：ContextTodo
    engine.py                       # 状态层：状态迁移裁决
    validator.py                    # 状态层：状态机 definition 校验
  event/
    context_event.py               # 事件层：ContextEvent
    context_transition.py           # 事件层：ContextTransition
  representation/
    context.py                      # 表示层：CollaborationContext
    envelope.py                     # 表示层：ContextEnvelope
    outcome.py                      # 表示层：CollaborationOutcome
    snapshot.py                     # 表示层：ContextSnapshot
  services/
    context_service.py              # 跨层应用服务与事务边界
apps/backend/src/agentclaw/community/core/task/context_collab/source/
  source_reader.py                   # Context 内部的外部来源读取契约 / 实现
  delivery_adapter.py                 # Context 内部的外部投递与 Work 启动契约 / 实现
apps/backend/src/agentclaw/community/adapters/http/task/context_collab/
  router.py                          # 内部 HTTP / OpenAPI 公开面共用同一 Service API
  schema.py                          # 内部 HTTP DTO / 域对象转换
apps/backend/src/agentclaw/community/adapters/http/openapi_v1/task/context_collab/
  router.py                          # OpenAPI 公开路由
  schemas.py                         # OpenAPI 契约 DTO
apps/backend/src/agentclaw/community/di/modules/task_context_collab_module.py
```

目录与四层架构的对应关系如下：

| 目录 | 协议层 | 只允许承担的职责 |
| --- | --- | --- |
| `core/task/context_collab/collaboration/` | 协作层 | 参与者、可见性、模式与 Policy 快照 |
| `core/task/context_collab/state/` | 状态层 | 冻结的状态机 definition、运行时 binding、Todo 与迁移裁决 |
| `core/task/context_collab/event/` | 事件层 | append-only 事实与迁移证明 |
| `core/task/context_collab/representation/` | 表示层 | Context 根、Envelope、Outcome 与 Snapshot |
| `core/task/context_collab/services/` | 跨层应用服务 | 只做命令编排、一致性校验和事务提交，不承载新的领域事实来源 |

`api/` 只放 transport-agnostic 契约，不放具体实现；`adapters/` 只做协议转换与投递，不承载领域策略；`di/modules/` 只做 composition root 绑定。外部来源、投递与 Work 启动相关的接口统一放在 `core/task/context_collab/source` 下，作为本域内部能力收敛；本方案不在 `plugin_api` 下新增或修改任何文件。Core 不 import HTTP、BCS 具体 client 或外部来源 SDK。外部来源、投递与 Work 启动统一通过 `source/` 子模块的契约收口，最终由 Composition Root 绑定。

### 7.2 Service API 能力

以下为能力，不承诺路径字符串；最终 HTTP DTO 与 Protocol 同步评审。

| 能力 | 输入 | 输出 |
| --- | --- | --- |
| Create Context | 作用域、来源、目标、可见性、profile ID、mode_kind / mode_policy、幂等键 | Context 快照 |
| Get Context | context ID、读权限上下文 | 状态、目标、参与者、输入材料、交付物、当前 Mode、最近已接纳执行事实、可执行 Actions 摘要 |
| Get Timeline | context ID、游标、分页 | 事件分页与完成证据引用 |
| Update Envelope | 目标、背景、约束、输入材料、交付物规格、验收，expected version | 新 Context version |
| Apply Context Command | actor、Command、request ID、expected version | 接纳结果、状态变化、错误原因 |
| List Context Actions | 当前 actor、状态 | 当前 allowed Commands |
| Switch Mode | 目标 mode_kind / mode_policy、参与者、expected version / fencing token | 原子校验后的 Mode revision 与切换事件 |
| Create / Update Todo | title、instruction、assignee、required、expected version | Todo |
| Complete / Block / Cancel Todo | todo ID、证据引用、expected version | Todo 事实与 Context state |
| Publish Snapshot | target actor / group、输入材料与交付物可见范围 | Snapshot revision |
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

入站 Forum / Task 回报适配器将外部事实转换为标准 Command，经共享 Service API 交由 Core 裁决，不直接写状态机表。出站来源读取、Work 启动、消息投递和回写则由 Core 调用 Plugin API；具体实现只在 Composition Root 注入。外部结果再经入站 Service API 接纳，不通过 Plugin 实现反向修改 Core。

两类边界分别测试：入站适配与命令裁决使用 Service API 测试；出站 Plugin API 使用 `world` 驱动真实 consumer 的 conformance suite，断言成功 / 失败结果及 Plugin 实际调用，不能用 HTTP 路由测试替代。

## 8. 并发、持久化与一致性

### 8.1 逻辑持久化分组

领域字段、类型和不变量以第 4 节为准；具体表、JSON schema 和索引可在实现 PR 中调整，但不要把整个状态机 definition 塞入 BBS `topic_config`。

| 持久化分组 | 必须保留的唯一 / 一致性约束 |
| --- | --- |
| Context root + state binding | `context_id` 唯一；每 Context 一个 active binding；`context_id + current_state` 由事务写入 |
| Event / Transition | `event_id` 唯一；按 Context / 来源命名空间去重；每个已接纳 Event 对应零或一条 Transition，`accepted_event_id` 唯一 |
| Source refs / collaboration_mode | 来源关联按 scope 唯一；每个 Context 一个 active Mode；四维 mode_policy 随 mode_revision 保存；Work 引用只存在于事件和外部 Work 权威数据 |
| Participant / Todo / Deliverable / Outcome | 子实体 ID 在 Context 内唯一；Actor 作用域内 active participant 唯一；交付物必须绑定规格和事实引用；裁决事实留下完整 actor 证据 |
| Snapshot / 投影投递 | Snapshot 可重建；投递记录与 dedupe key 唯一；失败状态不可丢失

### 8.2 ACID 与幂等

- Context 根用 `version` 做聚合乐观锁，用 `event_cursor` 记录最近接纳事实的位置；`ContextStateMachineBinding` 使用 `transition_cursor` 与 `state_fencing_token` 控制状态迁移并发。
- 事件、Transition、状态机版本、Todo 变更和必要的业务外键必须同一事务提交。
- 初次创建 Context 时，来源绑定与 `context.created` 事件在同一事务中闭环；数据库失败必须向调用方返回错误，不产生假成功。
- `work.requested` 抢占成功后在同一上下文中保存幂等意图，再通过 outbox 或等价机制触发外部启动；进程恢复后先读取外部状态，再决定继续、等待还是标记 blocked。
- 相同 `request_id` / `source_event_id` 重放必须返回原结果；不同内容同 ID 返回冲突。
- Context 根不保存当前 Work 的反向引用。`work.requested` 只能从状态机声明的合法前序状态发起；重复请求由状态机 guard、expected version、request id 和 `ContextStateMachineBinding.state_fencing_token` 串行裁决，避免同一 Context 重复启动冲突执行轮次。规划执行的一轮可包含计划明确的多个执行单元；其 executor 数量和调度仍遵循 Policy 与外部执行合同，不能用“单轮”否定计划内分工。
- 验收、取消、启动等命令使用 expected version 或状态 fencing token 串行裁决；输方收到冲突而不是覆盖赢家事实。
- 外部投影失败不回滚本地事实；系统使用 outbox、事务性消息队列或等价机制做有界重试、人工重试和明确的同步状态。
- 到达 `terminal_*` 状态后，仍可写入公开评论、来源同步或审计事实，但它们不能把 Context 拉出终态。
- 事件日志是重建 Context 快照的依据；重建只能重放本地事实，不能重新执行外部副作用。

## 9. 安全、可见性与审计

1. `CollaborationMode.visibility`、`ContextEvent.visibility`、`CollaborationOutcome.visibility` 是投影边界，不是授权；事件 / 结果只能在模式范围内进一步收窄，不能扩大。事件 / 结果在写入时冻结可见性上限，读取时再与当前 Mode 范围、actor 授权及材料授权取交集；后续 Mode 扩大范围或新增成员不得自动披露历史受限内容。可见性和 `authorization` 是两个独立概念：可见不代表可参与、可启动执行、可验收或可读取原始附件。
2. 状态机与 Mode Policy 都不能用来提升权限；进入某个 `active` 状态不会自动授予 Bot 仓库、群、外部系统或材料访问权限。
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

## 10. 当前协议如何支持公开讨论、任务代办

本节用两个参考 Profile 细化核心领域对象的取值。`definition_id`、`state_id`、`event_name` 是示例身份，可由产品定义；但 `mode_kind`、`visibility` 和 `governance_kind` 必须取自协议白名单。

### 10.1 公开讨论

公开讨论 Profile 的核心对象取值如下：

| 领域对象 | Profile 取值 |
| --- | --- |
| `ContextScope` | `scope_kind=open`；`scope_id=<BBS community id>`；`environment=<runtime environment>` |
| `ContextEnvelope` | `origin_ref=<forum topic ref>`；`input_sources[]` 引用 Topic 权威内容；`deliverable_spec=decision_summary` |
| `responsible_actor_ref` | 讨论结论责任人，例如 Topic 发起人或社区指定的责任人 |
| `CollaborationMode.mode_kind / mode_policy` | `public_discussion`；开放贡献、无独占执行者、责任人裁决 |
| `CollaborationMode.visibility` | `public`，表示仅在该 `ContextScope` 内公开，不是全互联网公开 |
| `CollaborationMode.participants[]` | 至少包含 `responsible_user`；其它参与者由 Forum Adapter 依据社区身份合法生成 |
| `ContextStateMachineBinding.definition` | `definition_id=public_discussion_profile`；`definition_revision=1` |
| `ContextStateMachineBinding.current_state` | 初始为 `draft`，只演进为 `discussing` / `accepted` / `refused` |
| `ContextTodo[]` | 本 Profile 不要求使用 Todo；可为空 |
| `ContextEvent` | `contribution.posted` + `payload.contribution_kind=forum_post`；`outcome.accepted` / `outcome.rejected` |
| `CollaborationOutcome` | 以 `decision_summary` 或结论文档引用作为 `ContextDeliverable.artifact_ref` |

参考状态机定义：

```text
ContextStateMachineDefinition
  ├─ definition_id: public_discussion_profile
  ├─ definition_revision: 1
  ├─ schema_version: context-state-machine/1
  ├─ initial_state: draft
  ├─ states[]
  │   ├─ draft
  │   │   ├─ governance_kind: waiting
  │   │   └─ allowed_modes: [public_discussion]
  │   ├─ discussing
  │   │   ├─ governance_kind: active
  │   │   └─ allowed_modes: [public_discussion]
  │   ├─ accepted
  │   │   └─ governance_kind: terminal_success
  │   └─ refused
  │       └─ governance_kind: terminal_failed
  └─ transitions[]
      ├─ rule_id: draft_to_discussing
      │   ├─ from_states: [draft]
      │   ├─ event_name: context.info_updated
      │   ├─ guard_refs: [info_is_complete]
      │   └─ to_state: discussing
      ├─ rule_id: discussing_to_accepted
      │   ├─ from_states: [discussing]
      │   ├─ event_name: outcome.accepted
      │   ├─ guard_refs: [actor_is_responsible_user]
      │   └─ to_state: accepted
      └─ rule_id: discussing_to_refused
          ├─ from_states: [discussing]
          ├─ event_name: outcome.rejected
          ├─ guard_refs: [actor_is_responsible_user]
          └─ to_state: refused
```

执行语义：
1. `contribution.posted` 是普通协作贡献事实，不改变业务状态；它只进入 Timeline。
2. Context 信息完备后，`context.info_updated` 使状态从 `draft` 进入 `discussing`。
3. 责任人显式裁决通过时，`outcome.accepted` 使状态从 `discussing` 直接进入 `accepted` 终态。
4. 责任人显式裁决拒绝时，`outcome.rejected` 使状态从 `discussing` 直接进入 `refused` 终态。
5. 本 Profile 不引入 `reviewing` / `needs_changes` / `cancelled` 中间状态；如业务需要暂停或取消，应由状态机定义额外状态或另行扩展。
6. Topic 的关闭或管理动作不会直接改变 Context 状态；如需关闭 Topic，必须由状态机 effect 显式声明并通过 Topic 权限校验。

### 10.2 任务委托（AI Coding 场景）

本节将任务委托具体化为 AI Coding 场景下的一个 direct_assignment 状态机 Profile，不新增协作模式。该 Profile 与 `direct_assignment` 模式保持一致：本轮 Context 只允许一个 active executor；`ContextTodo[]` 只用来拆分这同一个执行者要完成的具体步骤。业务阶段固定为 design（方案设计）、coding（编码）、verification（验证）、pr（提交 PR）。

任务委托 Profile 的核心对象取值如下：

| 领域对象 | Profile 取值 |
| --- | --- |
| `ContextScope` | `scope_kind=open`；`scope_id=<workspace / repo id>`；`environment=<runtime environment>` |
| `ContextEnvelope` | `objective=完成一次 AI Coding 变更`；`deliverable=一段可评审的 PR` |
| `responsible_actor_ref` | 变更责任人，通常是 repo / workspace owner |
| `CollaborationMode.mode_kind / mode_policy` | `direct_assignment`；直接选定、单人执行、交付证据齐备后由责任人裁决 |
| `CollaborationMode.visibility` | `workspace` 或 `participants` |
| `CollaborationMode.participants[]` | 包含 `responsible_user` 和一个 active assignee，可有 observer；Bot assignee 必须通过 Bot Owner / 服务归属验证 |
| `ContextStateMachineBinding.definition` | `definition_id=ai_coding_assignment_profile`；`definition_revision=1` |
| `ContextStateMachineBinding.current_state` | 初始为 `design`，只演进为 `coding` / `verification` / `pr` |
| `ContextTodo[]` | 保存本次变更中的一至多条具体代办；`ContextTodo.status` 仍然使用 assigned / completed / blocked / cancelled，业务阶段由状态机承载 |
| `ContextEvent` | **仅使用标准协议事件**：`context.info_updated`（含 issue / 方案输入事实）、`stage.completed` + `payload.stage_id`、`stage.failed` + `payload.stage_id`、`work.started` / `work.blocked`、`artifact.submitted` + `payload.artifact_type=pr`、`outcome.accepted` |
| `CollaborationOutcome` | 以 `pr_ref` 作为 `ContextDeliverable.artifact_ref`，表示本次变更已产出可评审 PR |

参考状态机定义：

```text
ContextStateMachineDefinition
  ├─ definition_id: ai_coding_assignment_profile
  ├─ definition_revision: 1
  ├─ schema_version: context-state-machine/1
  ├─ initial_state: design
  ├─ states[]
  │   ├─ design
  │   │   ├─ governance_kind: active
  │   │   └─ allowed_modes: [direct_assignment]
  │   ├─ coding
  │   │   ├─ governance_kind: active
  │   │   └─ allowed_modes: [direct_assignment]
  │   ├─ verification
  │   │   ├─ governance_kind: review
  │   │   └─ allowed_modes: [direct_assignment]
  │   └─ pr
  │       └─ governance_kind: terminal_success
  └─ transitions[]
      ├─ rule_id: design_to_coding
      │   ├─ from_states: [design]
      │   ├─ event_name: stage.completed
      │   ├─ guard_refs: [stage_id_is_design, info_is_complete]
      │   └─ to_state: coding
      ├─ rule_id: coding_to_verification
      │   ├─ from_states: [coding]
      │   ├─ event_name: stage.completed
      │   ├─ guard_refs: [stage_id_is_coding, required_todos_are_done]
      │   └─ to_state: verification
      ├─ rule_id: verification_to_coding
      │   ├─ from_states: [verification]
      │   ├─ event_name: stage.failed
      │   ├─ guard_refs: [stage_id_is_verification, failed_tests_are_recorded]
      │   └─ to_state: coding
      └─ rule_id: verification_to_pr
          ├─ from_states: [verification]
          ├─ event_name: outcome.accepted
          ├─ guard_refs: [actor_is_responsible_user, pr_artifact_is_submitted, verification_evidence_is_complete]
          └─ to_state: pr
```

执行语义：
1. Context 创建后直接进入 `design`，表示责任人或 Bot 正在确认问题、方案和影响面。
2. `stage.completed` + `payload.stage_id=design` 通过 `stage_id_is_design` / `info_is_complete` 后从 `design` 进入 `coding`。
3. `stage.completed` + `payload.stage_id=coding` 只有在全部必需 `ContextTodo.completed` 后，才允许从 `coding` 进入 `verification`；协议不提供 `coding.completed` 事件名。
4. `verification` 是自动或人工的验证评审阶段，不等同于 `pr`。
5. 验证通过并提交 PR 后，`artifact.submitted` 只保存候选产物事实；责任人通过 `outcome.accepted` 确认验证证据与 PR 齐备，才从 `verification` 进入 `pr`，冻结 `CollaborationOutcome.deliverables[]`。这不等于外部代码评审批准或合并。
6. 验证失败时，`stage.failed` + `payload.stage_id=verification` 使状态回到 `coding`，必须记录失败证据后继续修改。
7. `pr` 是本 Profile 的终态，业务目标是“产出可评审 PR”；PR 被 external review 通过与否不反向改写 Context 状态，除非产品层另行约定。
8. `work.started` / `work.blocked` 是通用执行事实，不一定触发状态迁移；只有对应的 `ContextTransition` 才会改变 `current_state`。

## 11. 分阶段交付

| 阶段 | 范围 | 准入条件 |
| --- | --- | --- |
| P1 | 四层模型、Mode Policy 校验、Context 聚合、基础 profile、事件日志、BBS 来源引用、公开讨论和 timeline 只读 | 状态机 schema、事件幂等和权限测试通过 |
| P2 | autonomous_relay 模式、work 事件与外部 Work 事实映射、人类验收 / 驳回 | 无人值守根启动、单轮执行并发控制和恢复测试通过 |
| P3 | direct_assignment 模式、required guard、Todo 升级 Work | Todo 并发、证据引用、终态守卫测试通过 |
| P4 | public_discussion / autonomous_relay / planning_execution / competitive_assignment / direct_assignment 五种模式接通 | BBS、Task、BCS 等通道权限、会话和事件映射回归通过 |
| P5 | 外部来源 revision、同步冲突和回写 | Plugin conformance、可靠投递和回环测试通过 |

实现排序采用可独立上线的小步，而不是一次重构所有协作入口。每个阶段保持旧入口可用，并用功能开关控制新增 Context 入口。

## 12. 测试与验收

### 12.1 领域单测

- Mode Policy：五种标准组合、非法混搭、公开贡献无独占权、接力同阶段双执行拒绝、规划按计划分工、竞价多人参与但单人中标、指派单人执行、完成条件不自动验收。
- Profile schema 校验：合法、非法、未知字段、缺初始状态、终态存在出边、重复 rule、未注册 guard / effect。
- 状态机执行：合法迁移、非法迁移、guard PASS / FAIL / BLOCKED、effect 幂等。
- 四层一致性：普通事件零 Transition、合法迁移一条 Transition、Mode 同状态切换刷新 fencing、旧 Mode 回报不可推进、事件与结果可见性不得扩大。
- 事件推进：`contribution.posted` 不改状态、`work.completed` 进入 review、`outcome.accepted` 进入终态。
- Todo 事件：必需 Todo 未完成不能验收、完成后可验收、并发完成一个成功一个冲突。
- 幂等：同 request id、同 source_event_id 重放返回同一结果；同 ID 不同内容返回冲突。
- 事务：数据库写失败回滚并上抛；outbox 至少一次投递幂等、外部失败可诊断，不能回改已提交 Transition。
- 并发：执行态重复发起 Work、旧版本提交、迟到回放、取消和验收同时到达。

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
| A16 | Mode 切换 | 目标 allowed_modes / Policy 校验成功并刷新 fencing；旧执行回报不能推进新阶段 |
| A17 | 竞价与执行 | 多个 bidder 可参与，中标前不可执行，中标后仅一个 executor |
| A18 | 交付与完成 | 产物提交或 Work 完成不等于验收；仅责任人裁决写入 Outcome |

## 13. 兼容与迁移

- 既有 Topic、Post、TaskInfo、TaskGraph 和 BCS group 不做破坏性改写。
- 历史内容不自动转换；Topic ↔ Context 使用显式来源引用，Task ↔ Context 由外部 Task / Work 自己保存并维护。
- 旧客户端读取 Topic 或 Task 响应时忽略新增 Context 字段；未知字段不能导致旧行为改变。
- 未绑定 Context 的任务、群和 BBS 帖子继续按现路径运行。
- 本轮统一草案名 `ContextOutcome` 为图中的 `CollaborationOutcome`，并新增 `mode_policy` 与事件级 `visibility`；尚未实现，无运行数据迁移。后续实现须同步 Service DTO、Profile、投影消费者和 conformance，不保留两套 Outcome 模型。
- 新 Context 相关 API 与状态机 profile 单独发布 schema 版本；不做无版本自由 JSON。
- 状态机定义发布后不可原位覆盖；需要新增 revision、声明兼容策略和测试。
- 回滚时关闭新入口和对应协作模式，不让已有 Work 被孤立终止；历史事件、来源引用和 Task / BCS 原始事实保留。
- 不通过删除 Topic / Task 行或直接改写状态表来回滚；只通过功能开关和审核过的兼容版本退出。

## 14. 主要风险与决策

| 风险 | 处理 |
| --- | --- |
| Context 变成第二个万能图模型 | 不保存 Work 节点或当前 Work 反向引用，只保存业务阶段、事件和来源引用；节点执行继续留在 TaskGraph / BCS / BCS StateMachine |
| 协作模式过度灵活，无法测试 | schema、guard、effect、mode 全部用白名单；自定义条目必须通过统一 conformance suite |
| 公开讨论泄漏敏感执行事实 | 事件分级可见；投影在服务端过滤，公开边界不允许暴露原始日志、内部轨迹或私有路径 |
| 模式并发导致双执行 | 状态机合法前序状态、request id 幂等和 expected version / fencing 共同保证；外部 Work 事实经事件回到 Context |
| 事件过多造成 Timeline 噪音 | 消息按层级、已读和压缩状态折叠；只读 projection 折叠，事件日志保留完整事实 |
| 模型直接推进状态 | 状态变更必须来自受信 Actor / Service / Adapter 事件，LLM 输出默认无法直接作为状态事实 |
| 高并发事件乱序 | Transition 只根据当前 state + accepted source event 推进；接收到缺失序号的事件不立即应用，等待补齐或回放 |
| 消费者看到的进度与实际执行不一致 | Context progress 只暴露已接纳事实，future / routing / internal details 按来源权限过滤 |
| 策略 dump 过大 | profile 冻结 revision，用户 Context 不实时加载最新 definition |
| 旧 profile 升级不兼容 | 单独生成新 revision，只允许显式 renew 命令迁移，并通过兼容测试 |

## 15. 待评审问题

1. 状态机 Profile 由平台内置、管理员配置，还是允许经审批的消费者模板扩展？
2. `ContextCommand` 是否采用统一 endpoint + differentiated action schema，还是保持 explicit endpoints？
3. 规划执行首版支持哪些计划分工与外部执行能力？首版已明确仅一个 active Mode，不开放跨模式并行或任意 Policy 组合。
4. Context 是否作为所有新 HELP Topic 默认创建，只在显式 Action 时显示，还是用户显式创建？
5. 当前阶段 Todo 如何映射既有工作项仓储？领域归属已明确为 Binding，不另立 Todo 聚合根。
6. Context 与 Topic、Task、BCS Run 的引用关系是否需要显式版本字段以支持并发解绑和重绑？

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
