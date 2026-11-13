# Context Collab L5 完整领域对象（ACCS-105 + ACCS-301–304 全量落地）

- 日期：2026-11-12
- 状态：Draft / 设计待评审
- 拥有模块：Backend `agentclaw.community.core.task.context_collab`
- 目标消费者：context-collab Method 服务、HTTP/OpenAPI delivery、context-collab tests
- 关联设计：[Context collaboration protocol](../2026-11-09-bbs-context-collaboration-protocol/spec.md)、[Context Collab L6 BBS Product Forms](../2026-11-12-context-collab-l6-product-forms/spec.md)
- 协议依据：ACCS-105（L5 公共合同）、ACCS-301（Single）、ACCS-302（Group）、ACCS-303（Swarm）、ACCS-304（Open）

## 1. 目标与动机

2026-11-09 spec 将 L5 裁剪为 `MethodKind` 受限枚举（"恢复为领域对象、值对象或受限枚举；不新增独立持久化表和外部注册表"）。本 spec 推翻该决定中 L5 部分，按 ACCS-105 公共合同与四份 Method Profile RFC **全量**实现：

1. **收合到的设计是两条线的叠加**（经作者确认）：
   - ACCS-105 L5 公共合同：`MethodProfile`（版本化声明式合同，代码内注册）、`MethodBinding`（进程内一等权威对象 + 生命周期操作族，存于 `MethodStoreProtocol` 注入的内存实现）、`MethodCompositionEdge`（组合关系 + 组合操作族）；
   - ACCS-301–304 Profile 内部对象：`SingleSelection`、`GroupRoster`/`GroupMemberBinding`、`SwarmPlan`/`Stage`/`StageAllocation`/`SwarmExecutionProfile`/`SwarmExecutionRecord`、`OpenOpportunity`/`ParticipationApplication`/`AdmissionDecision`。
2. Method 生命周期、组合与各 Profile 操作族落到 Service API 与 HTTP delivery，每个有副作用操作走 G1 结构授权 + 审计。
3. 既有三个北向 API 的 `method_kind` wire 字段**逐字节兼容**（见 §9）。
4. **持久化整体推迟到 P2**（2026-11-12 复审裁决，推翻本 spec 初版"做 13 张表"的方案）：P1 全部 L5 对象存于进程内 `InMemoryMethodStore`，存储协议缝（§6）保持 P2 可换持久化实现；代价见 §6.4（重启语义弱化）。

旧 spec 的 L2/L3/L6 裁剪决定、G4 已注册状态机、G1 既有授权语义均不变。本 spec 只取代 2026-11-09 spec 中 §3.5 / §5 涉及"不建 L5 实体/注册表"的段落。

## 2. 范围与非目标

**范围内**：§3–§8 全部领域对象、状态机、操作族、进程内存储、G1/G4 接缝、HTTP 路由、测试（Collet 既有路径仍走 SQLite 真仓储）。

**非目标**（P2 接缝，本 spec 留协议正确的停机点）：

- **持久化落库**：P1 不建任何 L5 表/DDL，全部对象存于进程内 `InMemoryMethodStore`（§6）；P2 经同一 `MethodStoreProtocol` 换持久化实现，服务层零改动。

- **Swarm Computation Plane 的外部派发**：`Submit/Observe/Cancel/Collect` 只落语义对象（`SwarmExecutionProfile`/`SwarmExecutionRecord` 含 external_execution_id 列），不实现对外部计算系统或 A2A 的实际 wiring；外部结果回流需要一个显式 `AdoptPlan/CompleteStage` 权威面操作。协议 §303.8 的 Authority/Computation 双平面边界因此保持成立。
- **L1/A2A 信封传输**：跨 Agent 的 L5 操作（`AccsOperationEnvelope(layer=L5)`）不由本模块交付；本模块是 L5 权威面。
- **G3 演进对象**（ImprovementSubject/ImprovementObjective）不入；G3 hook 留 `subject_type` 字段位。
- **Open 反滥用智能检测**（Sybil/垃圾/能力虚报）：仅落结构控制（容量/窗口/速率上限 + 责任主体必填 + 显式撤销），检测策略是 P2。
- **Group 投递语义**（消息顺序、离线消息、历史可见性）仍由 L4 Message/G1 过滤承担，不新建投递状态机。
- **不改** L6 `ProductProfile`/`WorkflowKind`、G4 已注册 task 状态机、G1 `check_create/check_append` 语义。

## 3. L5 公共合同（ACCS-105）

### 3.1 MethodProfile（代码内声明式合同，不建表）

`l5_methods/method_profile.py`。Profile 是版本化合同，随代码发布——**代码即合同来源**，不建持久化注册表、不建外部注册中心（ACCS-305 的"注册"语义由包内声明完成）。

```python
@dataclass(frozen=True)
class MethodProfileDefinition:
    method_kind: str            # "Single" | "Group" | "Open" | "Swarm"
    profile_version: str        # "0.2"，取自对应 RFC
    capabilities: tuple[str, ...]     # 对外可声明的能力（如 single.selection.replace 原子替换）
    primitives_used: tuple[str, ...]  # 声明使用的 L4 原语子集（子集来自 Task/Message/Artifact/Decision/Query/Activity 词汇）
    participation_model: str    # "selected" | "roster" | "stage" | "admission"
    composable_relations: tuple[str, ...]  # 该 Profile 作为父方法允许的组合关系（§3.3 矩阵的行来源）

METHOD_PROFILE_REGISTRY: dict[str, MethodProfileDefinition]  # 四个核心 Profile 剧本
```

`MethodKind` 枚举护展为wire权威：保留 `SINGLE="Single"`、`OPEN_GROUP="Open + Group"`，新增 `GROUP="Group"`、`OPEN="Open"`、`SWARM="Swarm"`。`parse_method_kinds("Open + Group") -> {OPEN, GROUP}` 提供复合 wire 值到核心方法集合的投影；检索/校验一律经它，不裸拆字符串。

### 3.2 MethodBinding（一等权威对象）

```yaml
MethodBinding:
  method_binding_id: string(128)
  collab_context_id: string(128)        # 顶层绑定为所属 Collet
  parent_scope_ref: string | None       # Task/Activity scope 引用（协议允许 Collet/Task/Activity 作用域）
  method_kind: string                   # wire 取值（§3.1），顶层绑定来自 create_context
  method_names: [string]                # parse_method_kinds 投影，Open+Group -> ["Open","Group"]
  profile_versions: {string: string}    # method -> profile_version
  participant_ref: string | None        # 绑定涉及的 L4 Participant 引用（executor 侧）
  role_binding_refs: [string]
  config: object                       # Profile 声明的配置（预算、容量、深度上限等）
  state: BOUND | ACTIVE | SUSPENDED | TERMINATED
  version: integer                     # 乐观并发版本，update/状态转换自增
  bind_reason: string                  # "create_context" | "compose" | "delegate"
```

**初始状态（作者裁决）**：顶层绑定（create_context 投影路径）直接以 **ACTIVE** 入存储——Collet 即刻是活跃上下文，对应既有产品形态"创建即可协作"的语义；嵌套 `bind` 以 **BOUND** 入存储，必须显式 `activate`。§4"binding 非 ACTIVE 拒绝副作用"因此对两条路径都成立。

生命周期操作族（ACCS-105 §3）：`bind`（嵌套/委托绑定时）、`activate`、`update`（config 变更，不改状态，乐观并发版本号自增）、`suspend`、`resume`、`terminate`。状态机：

```text
BOUND ─activate→ ACTIVE ─suspend→ SUSPENDED ─resume→ ACTIVE
BOUND/ACTIVE/SUSPENDED ─terminate→ TERMINATED（终态）
```

约束：`terminate` 后任何 Profile 内部对象操作与 composition 出边创建一律拒绝（结构校验）。**顶层绑定的 `terminate` 门槛（作者裁决）**：仅当 Collet `current_snapshot` 的任务状态为终态（`task.cancelled` / `task.accepted`，沿用快照状态词汇）时允许；活上下文上拒绝——避免摧毁仍活协作的 L5 权威状态。不做自动级联；binding 终态是显式调用。

### 3.3 MethodCompositionEdge（组合关系）

```yaml
MethodCompositionEdge:
  edge_id: string(128)
  collab_context_id: string(128)
  parent_binding: string(128)   # MethodBinding.method_binding_id
  child_binding: string(128)
  relation_kind: NEST | DELEGATE_TO | SUPPORT | PARALLELIZE | ESCALATE
  state: ACTIVE | DETACHED
```

操作族映射：`nest→NEST`、`delegate-to→DELEGATE_TO`、`support→SUPPORT`、`parallelize→PARALLELIZE`、`escalate→ESCALATE`、`detach`（置 DETACHED，保留审计）。结构校验（ACCS-105 §8）：

1. 组合图对 NEST/DELEGATE_TO/ESCALATE 关系做**无环**校验（新增边后 DFS 检测，含跨 Collet 不可能——组合边限定同一 collab_context_id 内）；
2. 组合深度上限默认 8（`MethodBinding.config.max_compose_depth` 可覆盖），超限拒绝；
3. **组合矩阵（作者裁决，按 RFC 语义固化为版本化合同）**：

| 父方法 | 关系 | 允许的子方法 kind |
| --- | --- | --- |
| 任意 | NEST / SUPPORT / DELEGATE_TO | 任意核心方法（ACCS-301 §7：selected Agent 可将子 Task 委托给 Single、Group 或 Swarm） |
| Group、Swarm | PARALLELIZE | 同 kind（并行副本语义，禁止跨 kind 伪装扩容） |
| Single | ESCALATE | Group、Swarm |
| Group | ESCALATE | Swarm |
| Swarm | ESCALATE | （无——Swarm 已是最大参与规模，不允许继续升级） |

矩阵以 `method_composition.py` 模块级 `COMPOSITION_MATRIX` 落地，Profile 的 `composable_relations` 声明与矩阵行一致，防漂移自检两者对齐。

## 4. Method Profile 内部对象（ACCS-301–304）

通用规则（适用 §4.1–§4.4）：所有内部对象通过 `method_binding_id` 反查 MethodBinding；binding 非 ACTIVE 时拒绝副作用操作（G1 + 状态机双保险）；每次状态转换在对象上落审计四字段（§6.3）；状态转换裁决走 G4 声明式状态机（§8.2），非法迁移抛 `ContextCollabValidationError`。

### 4.1 Single Profile（ACCS-301）

```yaml
SingleSelection:
  selection_id: string(128)
  method_binding_id: string(128)
  scope_ref: string                    # 作用域引用（父 Task/Activity）
  selected_agent_participant_ref: string
  selected_by_ref: string              # PrincipalRef 序列化
  requirement_refs: [string]
  selection_evidence_refs: [string]
  state: PROPOSED | ACTIVE | REPLACED | RELEASED
  valid_from: timestamp
  valid_until: timestamp?
```

操作族：`Select`（PROPOSED→ACTIVE；同一 binding + scope 断言至多一个 ACTIVE——存储层唯一断言保证）、`Replace`（旧 ACTIVE→REPLACED、新 PROPOSED→ACTIVE，存储锁内原子）、`Release`（ACTIVE→RELEASED）、`Delegate`（= 创建嵌套 MethodBinding + DELEGATE_TO 边的便捷操作）。一致性断言按 RFC §9 五条逐条测试（含"Query 结果不自动成为 selection"——即 Select 必须显式携带 agent 引用）。

### 4.2 Group Profile（ACCS-302）

```yaml
GroupRoster:
  roster_id: string(128)
  method_binding_id: string(128)
  revision: integer                    # 乐观并发版本，每次成员变更 +1

GroupMemberBinding:
  group_member_binding_id: string(128)
  method_binding_id: string(128)
  participant_ref: string
  address: string
  role_binding_refs: [string]
  state: INVITED | ACTIVE | SUSPENDED | LEFT | REMOVED | REPLACED
  joined_at: timestamp?
  left_at: timestamp?
```

操作族：`Invite`（建 INVITED 成员）、`AddMember`（INVITED→ACTIVE；参与者已在 roster 中且未终态则拒绝）、`SuspendMember`、`ResumeMember`（SUSPENDED→ACTIVE，补齐成员状态机可达性——作者裁决补漏）、`Leave`（成员本人自助，ACTIVE/SUSPENDED→LEFT）、`RemoveMember`（未完成工作转移字段 `handover_to` 可选，P1 校验存在性不执行转移——作者裁决）、`ReplaceMember`（原子：旧→REPLACED、新→ACTIVE）、`ChangeRole`（写 `role_binding_refs`，roster revision +1）。Roster 更新全部走"storage 锁内 revision 比对"，revision 不匹配即 `ContextCollabVersionConflict`（409 语义）。roster 不建 L3 Membership、不扩资源权限（G1 断言）。

**引用校验边界（作者裁决，适用全 spec）**：P1 中 L4 Task/Activity/RoleBinding 是值对象与事件 DAG 事实，无持久化身份存储——本 spec 所有 `*_ref` 字段做**格式校验**（非空前缀形状，复用 L2/L3 ref 值对象的解析规则），不做存在性验证；存在性校验是 P2（接入事件 DAG/参与者存储后）。

### 4.3 Swarm Profile（ACCS-303）

```yaml
SwarmPlan:
  swarm_plan_id / method_binding_id / task_ref
  max_parallelism / max_depth: integer
  budget_resource_refs: [string]
  termination_condition: object
  state: DRAFT | READY | RUNNING | PAUSED | COMPLETED | FAILED | CANCELLED

Stage:
  stage_id / swarm_plan_ref / task_ref / name
  dependency_stage_refs: [string]
  requirement_refs / input_artifact_refs: [string]
  output_artifact_roles: [string]
  completion_condition: object
  state: PENDING | READY | ALLOCATING | RUNNING | SUCCEEDED | FAILED | SKIPPED | CANCELLED

StageAllocation:
  allocation_id / stage_ref
  target_kind: AGENT | COMPUTE
  target_ref: string
  execution_profile_ref: string
  qualification_evidence_refs / authorization_evidence_refs: [string]
  lease_ref: string
  attempt: integer
  state: PROPOSED | ACTIVE | RELEASED | EXPIRED | REVOKED

SwarmExecutionProfile:
  execution_profile_id
  mode: A2A_AGENT | NATIVE_COMPUTE
  target_ref: string
  supported_work_kinds: [PLAN | MATCH | SCHEDULE | EVALUATE | EXECUTE]
  input_contract_ref / output_contract_ref: string
  lifecycle_mapping: object

SwarmExecutionRecord:
  execution_record_id / operation_ref
  stage_allocation_ref: string?
  execution_profile_ref: string
  external_execution_id: string
  state: SUBMITTED | RUNNING | SUCCEEDED | FAILED | CANCELLED
```

权威面操作族：`DeclareExecutionProfile`（binding 作用域内声明 `SwarmExecutionProfile`——对象 P1 即活的：profile 经本操作入存储，作者裁决）、`SubmitPlanCandidate`（DRAFT；**自带 provenance 由服务端落一行 `SwarmExecutionRecord`**，operation_ref 指向本次提交操作，作者裁决）、`ValidatePlan`（结构校验不改状态，返回报告）、`AdoptPlan`（DRAFT→READY，未 Validate 通过的拒绝）、`StartPlan`（READY→RUNNING，前置 READY Stage 同步转 READY）、`PausePlan`、`ResumePlan`（PAUSED→RUNNING，补齐计划状态机可达性——作者裁决补漏）、`AllocateStage`（READY→ALLOCATING→分配 ACTIVE Allocation，断言 active allocation 有效 lease：`lease_expires_at > now`；allocation 必须引用 binding 作用域内已声明的 `SwarmExecutionProfile`）、`CompleteStage`（校验 completion_condition 结构存在 + allocation 状态，RUNNING→SUCCEEDED）、`FailStage`、`TerminatePlan`（终态；之后拒绝一切计划扩展）。结构校验：Stage 依赖图无环、`max_depth`/`max_parallelism`/Stage 数上限、同 Stage 并行 ACTIVE Allocation ≤ max_parallelism、预算引用为合法 ResourceRef。**Computation Plane 派发在 P1 不实现**（§2）：`SwarmExecutionRecord` 仅由上述权威面操作创建/查询，`external_execution_id` P1 恒为来源操作的审计标识；外部执行回写通过显式 `CompleteStage/AdoptPlan` 进入。

### 4.4 Open Profile（ACCS-304）

```yaml
OpenOpportunity:
  opportunity_id / method_binding_id / scope_ref
  participation_type: COLLET_PARTICIPANT | GROUP_MEMBER | SWARM_ALLOCATION | SINGLE_CONTRIBUTION
  requirement_refs / admission_policy_refs: [string]
  capacity: integer?
  opens_at: timestamp
  closes_at: timestamp?
  state: DRAFT | OPEN | PAUSED | CLOSED | CANCELLED

ParticipationApplication:
  application_id / opportunity_ref
  applicant_agent_ref: string
  accountable_principal_ref: string     # 必填：责任主体
  capability_evidence_refs / proposed_artifact_refs / requested_role_refs: [string]
  state: SUBMITTED | SCREENING | ADMITTED | REJECTED | WITHDRAWN | REVOKED
  submitted_at: timestamp

AdmissionDecision:
  admission_decision_id / application_ref / decision_ref
  admitted_scope_ref: string?
  resulting_binding_refs: [string]      # 录取产物：GROUP_MEMBER 时指向 group_member_binding 等
```

操作族：`PublishOpportunity`（DRAFT→OPEN；opens_at/closes_at 窗口校验）、`PauseOpportunity`、`ReopenOpportunity`（PAUSED→OPEN，补齐状态机可达性——作者裁决补漏）、`CloseOpportunity`（终态）、`SubmitApplication`（任意已认证 actor；Opportunity 必须 OPEN、在窗口内、capacity 未满——ACTIVE/SUBMITTED+SCREENING 计数 < capacity）、`Screen`（SUBMITTED→SCREENING，标记进入评审）、`Admit`（→ADMITTED；`SWARM_ALLOCATION` 类型必须携带 `target_stage_ref` 输入并校验其处于本 binding 作用域内计划的 READY/RUNNING Stage，缺省拒绝——作者裁决；按 participation_type 产生绑定产物：GROUP_MEMBER→创建 ACTIVE GroupMemberBinding；SWARM_ALLOCATION→落 ACTIVE StageAllocation 语义记录；SINGLE_CONTRIBUTION→仅记录 artifact refs 不产生参与绑定；COLLET_PARTICIPANT→P1 拒绝，需 L4 参与者治理，P2）、`Reject`、`Withdraw`（申请责任主体本人）、`RevokeAdmission`（ADMITTED→REVOKED；同时按 resulting_binding_refs 终结对应成员/分配状态）。录取仅对 `admitted_scope_ref` 生效，不产生 Space Membership/Grant（G1 断言 + 测试）。

## 5. Service API（transport-agnostic）

新文件 `core/task/context_collab/method_service_api.py`（不动既有 `service_api.py`，二者均 <1000 行）。模式沿用既有：dataclass 命令/结果 + `runtime_checkable Protocol`，delivery 只依赖 Protocol。按操作族拆四个协议：

```python
class MethodLifecycleServiceProtocol(Protocol):     # §3.2 + §3.3 bind/activate/.../compose/detach + inspection
class GroupProfileOpsServiceProtocol(Protocol):     # §4.2（含 Single 委托入口）
class SwarmProfileOpsServiceProtocol(Protocol):      # §4.3
class OpenProfileOpsServiceProtocol(Protocol):       # §4.4
```

inspection 操作族返回投影：`binding state summary`、`capabilities`（自 METHOD_PROFILE_REGISTRY）、`conformance evidence`（最近一次 G4 校验报告 + 各 Profile 内部对象状态计数）。实现落在 `services/method_lifecycle_service.py` 与 `services/method_profile_services*.py`（single+group / swarm+open 按 1000 行边界拆），全部通过 `MethodOpsAuthorizerProtocol`（§8.1）与存储协议 `MethodStoreProtocol`（§6）解耦。

## 6. 内存存储（P1）与持久化路径（P2）

### 6.1 存储协议与实现

新文件 `persistence/method_store.py`：`MethodStoreProtocol`（§3–§4 全部 13 类对象读写的唯一入口）+ `InMemoryMethodStore` 进程内实现（DI 单例）。不变式在存储层强制，整体 `threading.Lock` 串行化：

- binding 键 `(avernet_tenant, env, collab_context_id, method_binding_id)`；同键同形态幂等、异形态 `ContextCollabValidationError`（§8.3）；
- Single 同 binding+scope 至多一个 ACTIVE；
- Group roster `revision` 比对，不匹配抛 `ContextCollabVersionConflict`；
- Open application 唯一 `(opportunity_ref, applicant_agent_ref)` 非终态；
- 各集合按 ID 保持插入序（列表投影稳定）。

对象一律 frozen dataclass，更新 = 锁内替换携带审计字段的新实例；并发语义由存储锁保证（既有 SQLite 仓储测试因 L5 不落库而不受影响）。

### 6.2 租户/环境隔离

全部键带 `(avernet_tenant, env)` 前缀（与既有表唯一索引语义一致），跨租户串读在存储层即不可能；服务层不重复判定。

### 6.3 审计字段（状态承载对象通用）

`transitioned_from`、`transition_reason`、`transitioned_by`（actor principal_id）、`transitioned_at`——每次状态转换覆写于内存对象。conformance evidence 与完整事件化审计以此为基线；**不**把 L5 操作折叠进 L4 事件 DAG（事件 DAG 词汇属 L4，ACCS-105 §7 也要求 A2A/外部状态独立映射）。

### 6.4 生命周期与重启（语义弱化，作者裁决）

- **进程重启后 store 归零**：Collet（DB 持久）仍在而 binding 蒸发。读路径不报错：`inspect`/Profile 操作发现上下文缺顶层 binding 时，按 `context_spec.method_kind` **按需重建 ACTIVE 投影**（静默自愈）；roster/成员如实为空。
- 已 `terminate` 的顶层 binding 在重启后与丢失不可区分——按需重建会以 ACTIVE 复活；terminate 门槛断言（§3.2）仍能挡住对活 Collet 的终止，但"终止后不可复活"在跨重启维度退化。完整持久化是 P2 修复路径，经 §6.1 协议缝替换实现。
- P2 持久化时：不变式移交数据库约束（部分唯一索引/乐观版本列），审计字段映射为行内四列（§6.3）。

## 7. HTTP delivery

- 新 schema 文件 `adapters/http/task/schemas_context_collab_methods.py`（Pydantic DTO，wire 命名 snake_case 与 Service API 字段一致）。
- 路由并入 `adapters/http/task/context_collab.py` 既有分组（同文件若超 1000 行则拆 `context_collab_methods.py`，路由前缀不变）：
  - `POST /collab/contexts/{id}/method-bindings`（嵌套 bind）
  - `POST .../method-bindings/{binding_id}/{activate|suspend|resume|terminate}`
  - `PATCH .../method-bindings/{binding_id}`（update config）
  - `GET  .../method-bindings/{binding_id}`（inspection：state summary/capabilities/conformance evidence）
  - `POST .../method-bindings/{binding_id}/compose` / `DELETE .../compose/{edge_id}`（detach）
  - `POST .../method-bindings/{binding_id}/group/{members|suspend|resume|leave|remove|replace|roles}`、`.../single/{select|replace|release|delegate}`
  - `POST .../method-bindings/{binding_id}/swarm/execution-profiles`、`.../swarm/plans`、`.../swarm/plans/{plan_id}/{adopt|start|pause|resume|terminate}`、`.../swarm/stages/{stage_id}/{allocate|complete|fail}`
  - `POST .../method-bindings/{binding_id}/open/opportunities`、`.../open/opportunities/{opportunity_id}/{pause|reopen|close}`、`.../open/applications/{application_id}/{screen|admit|reject|withdraw|revoke}`
- 错误映射沿用 `ContextCollabValidationError` → 400 语义冲突 → 409 的既有约定。（路由最终命名在 plan 阶段对齐既有 OpenAPI 风格微调。）

## 8. 横切治理接缝

### 8.1 G1（Secure and Govern）

新接缝 `g1_secure_govern/method_authorization.py`：`MethodOpsAuthorizerProtocol`（`check_lifecycle_op` / `check_group_op` / `check_swarm_op` / `check_open_op`），与既有 `ContextCollabAuthorizerProtocol` **并列不改**（避免破坏 P1 实现与 P2 替换方）。默认结构实现：

- lifecycle/compose/swarm/authority 面操作：actor 必须是 Collet 的 principal 或 executor（复用 `_is_principal/_is_executor` 判定规则）；
- group 成员管理：principal/executor；成员本人仅可自助 `Leave`（ACTIVE/SUSPENDED→LEFT）——`ChangeRole` **不经**自助路径（成员自改 role_binding_refs 属提权，2026-11-12 复审裁决收窄）；
- open `SubmitApplication`：任意已认证 actor；`Withdraw`/`Leave` 类自助操作 = actor `principal_id` 与操作目标行存储标识**字符串相等**（比对 `applicant_agent_ref` / `accountable_principal_ref` / `participant_ref`，沿既有 `_is_executor` 的 `principal_id == agent_id` 先例——作者裁决，身份注册表是 P2）；其余 open 操作 principal/executor；
- 一切 L5 操作不授予 Resource 权限——纯断言测试覆盖。

### 8.2 G4（Contracts and Consistency）

- 四个 Profile 内部状态机 + binding 生命周期用 G4 既有声明式机制（`StateMachineDefinition`/`validate_definition` 风格）注册于新的 `METHOD_MACHINE_REGISTRY`，复用 G4 纯谓词守卫；一个 machine 对象绑定一个实体状态列。
- `resolve_transition` 同构裁决迁移；守卫输入由服务层求值传入（与既有 append_event 用法一致）。
- 注册时执行机器自身校验（可达性豁免等待状态沿既有规则）。
- MethodProfile 声明（capabilities/primitives_used/composable_with）与四个内部状态机词汇在模块加载时自检，对不上 RFC 词汇即抛错——防漂移测试同步落在 `test_method_g4_conformance.py`。

### 8.3 G2（Reliable and Live）

- 幂等：状态机非法迁移本身即天然防重（重复 activate 幂等拒绝）；创建类操作（bind、opportunity、plan 等）接受调用方传入 ID 作幂等键，**同 ID + 同请求形态 → 返回已存在对象（幂等成功）；同 ID + 不同请求形态 → 409 语义冲突**（作者裁决，upsert-by-key）。不传 ID 则服务端生成。
- Swarm lease：allocation 对象存 `lease_expires_at`；`CompleteStage` 时过期则要求先释放再重分配，过期检查为结构断言；**没有后台续租/回收扫描**（P2，避免热路径扫描负担）。
- 迟到结果：外部结果只能经显式权威面操作进入，DIS 正是显式 comformance 断言（防"外部成功自动改权威状态"）。

### 8.4 G3

Profile/Binding/组合策略的 `subject_type` 词位预留（`MethodBinding` 上的 `g3_subject_type` 字段，P1 恒 None），不实现演进对象。

## 9. 兼容性（wire 逐字节兼容）

1. 三个北向 API（create/append/get）请求与响应**不**改：`method_kind` 字段名、取值、语义不变；新增的 `GROUP/OPEN/SWARM` 取值对旧调用方是纯增量（不传则不出现）。
2. `create_context` 在 Collet 落库成功后，于内存 store 同步创建顶层 MethodBinding + Open+Group 时的 roster + 初始成员（executor/principal）；store 写失败如实上抛（内存写基本不可能失败，但失败绝不让"有 Collet 无 binding"的响应伪装成功）。`ContextSpec.method_kind` 仍是 L4 投影的权威 wire 字段，顶层 binding 由它投影派生——**单向派生，不回写**（P1 裁决：两个写入口都会改会破坏 snapshot 不变式；binding 的 lifecycle 操作只改 binding 自身状态，不动 `context_spec`）。
3. G1 `check_append` 仍读 `context_spec.method_kind`，不读 binding（读取面零改动）。
4. `MethodKind.from_wire` 未知值仍抛 `ContextCollabValidationError`——新增四值不改变该行为。
5. `Swarm` wire 值解锁意味着 create_context 可传 `method_kind="Swarm"`：P1 结构授权不允许 `Swarm` 作为顶层 method（未声明 executor 的 Swarm 顶层无责任主体结构）——`check_create` 增加同一拒绝对 `GROUP`/`OPEN`/`SWARM` 裸值生效；顶层只接受 `Single` / `Open + Group`（既有两个产品形态不变）。这与 ACCS 无冲突：301–304 的内部对象经嵌套 bind 进入。

## 10. 文件布局

```text
core/task/context_collab/
├── l5_methods/
│   ├── method_binding.py        # MethodKind 扩展 + MethodBinding 值对象/校验
│   ├── method_profile.py        # MethodProfileDefinition + METHOD_PROFILE_REGISTRY
│   ├── method_composition.py    # MethodCompositionEdge + 组合规则/无环校验
│   ├── single_profile.py        # SingleSelection
│   ├── group_profile.py         # GroupRoster / GroupMemberBinding
│   ├── swarm_profile.py         # Swarm 5 对象
│   └── open_profile.py          # Open 3 对象
├── method_service_api.py        # §5 协议与命令/结果对象
├── services/
│   ├── method_lifecycle_service.py
│   └── method_profile_services*.py   # single+group / swarm+open（按 1000 行边界拆）
├── persistence/
│   └── method_store.py          # MethodStoreProtocol + InMemoryMethodStore
├── g1_secure_govern/method_authorization.py
└── g4_contracts_consistency/method_state_machines.py   # METHOD_MACHINE_REGISTRY
```

## 11. 测试策略

按操作族并列于 `tests/community/core/task/context_collab/`：

| 文件 | 覆盖 |
| --- | --- |
| `test_method_binding.py` | 生命周期状态机、审计列、wire 派生、兼容性 |
| `test_method_composition.py` | 五种关系、无环、深度上限、composable_with 矩阵、detach |
| `test_single_profile.py` | RFC-301 §9 五条一致性断言逐条 |
| `test_group_profile.py` | roster 乐观并发（409 重放）、成员状态机（含 Leave/ResumeMember 可达性）、ChangeRole 引用格式校验 |
| `test_swarm_profile.py` | Stage 图无环、深度/并行/预算上限、lease 断言、双平面隔离（外部 record 不改权威状态） |
| `test_open_profile.py` | 容量/窗口/责任主体、录取产物绑定、撤销收敛、admitted≠membership 断言 |
| `test_method_g4_conformance.py` | 状态机注册校验 + Profile 声明防漂移 |
| `test_method_e2e.py` | 端到端（Collet 走 SQLite 真仓储 + L5 走 InMemoryMethodStore）：create→binding 投影→group 成员→open 申请录取→swarm 委托链（沿用既有 e2e 文件模式） |

CI：`DEPLOY_PROFILE=test uv run pytest tests/community -v` 全绿；改动行覆盖率 ≥80%（`./scripts/ci_test.sh` 门禁）；不触碰 BCS/引擎/前端。

## 12. 作者裁决（实现者不需再问，但评审可推翻）

1. MethodProfile 代码内声明、不建持久化注册表——合同随代码版本化，外部动态注册是 P2。
2. 顶层 binding 由 create_context 单向投影，**初始 ACTIVE**；嵌套 bind 初始 BOUND，需显式 activate；L5 lifecycle 操作不回写 `context_spec`。
3. 组合深度默认 8；组合边限同一 collab_context_id 内；组合矩阵按 §3.3 表格固化。
4. 顶层 method 只允许 `Single`/`Open + Group`，`Group/Open/Swarm` 裸值只能经嵌套 bind 进入。
5. 审计 = 对象内转换四字段（from/reason/by/at），完整事件化审计与 G3 演进对象同属 P2。
6. Swarm Computation Plane 只落语义对象不做外部派发（`SwarmExecutionProfile` 经 `DeclareExecutionProfile` 声明、provenance record 由提交操作自建）；Open 反滥用仅结构控制。
7. 不建 lease 后台扫描；过期由操作时断言。
8. `Withdraw`/`Leave` 类自助操作放行给操作对象本人（`principal_id` 字符串相等比对，沿 `_is_executor` 先例），其余治理操作 principal/executor。
9. L5 操作不进 L4 事件 DAG。
10. 顶层 binding `terminate` 仅在 Collet 快照任务状态为终态（task.cancelled / task.accepted）时允许。
11. 所有 `*_ref` P1 做格式校验不做存在性验证（L4 无持久化身份存储；存在性校验为 P2）。
12. 幂等键语义：同 ID 同形态返回已存在对象，同 ID 异形态 409。
13. `Admit` SWARM_ALLOCATION 必须携带并校验 `target_stage_ref`。
14. 成员/成员恢复、计划恢复、机会重开四个补漏操作（`Leave/ResumeMember/ResumePlan/ReopenOpportunity`）一并实现，保证 G4 状态机全状态可达自检通过。
15. **持久化整体推迟 P2**：13 张表方案作废，P1 全部 L5 对象入进程内 `InMemoryMethodStore`（重启归零、读路径按需重建顶层 ACTIVE 投影、terminated binding 跨重启可复活为已声明的语义弱化，§6.4）；P2 经 `MethodStoreProtocol` 换持久化实现，服务层零改动。

## 13. 留给实现者的空间

- Service API 命令类字段命名（在对齐 wire 表格语义下）；
- repository 内查询构造细节与索引微调；
- 路由命名与既有 OpenAPI 风格的对齐；
- 1000 行边界处的文件再拆分选择。
