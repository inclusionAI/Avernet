# Context Collab L4 Collet 对齐 ACCS（领域模型层）

- 日期：2026-11-12
- 状态：Accepted（对齐方案与全部裁决经作者确认 2026-11-12）
- 拥有模块：Backend `agentclaw.community.core.task.context_collab`
- 目标消费者：context_collab 领域值对象、G4 状态机、仓储 record、services、context-collab tests
- 协议依据：语雀《Avernet ACCS》（ACCS-001 公共合同、ACCS-104 L4，版本 0.2）
- 关联设计：[2026-11-09 协议落地 spec](../2026-11-09-bbs-context-collaboration-protocol/spec.md)（本次**不受**其 wire 兼容裁决约束范围——对齐只做领域模型层）

## 1. 目标与动机

2026-11-12 对 `l4_context_primitives/collet.py` 与 ACCS 协议文档的比对发现三处偏差：

1. 标识符命名：ACCS-001 §2 标识符表规定 Collet 的 ID 字段为 `collet_id`；代码用 `collab_context_id`；
2. 状态借用：ACCS-104 §2 要求“所有对象必须使用独立 ID、版本和状态”；Collet 没有自己的状态，借用 Task 状态词汇（`current_snapshot.status`），且 Context 操作族 `create/activate/suspend/complete/archive`（§3）无对应状态机；
3. 发起者可空：ACCS-104 §7 要求“Collet 必须有发起者”；代码 `actor_ref: PrincipalRef | None` 容忍外部部分行。

本 spec 把 Collet 聚合根对齐到 ACCS。**范围裁决（作者）：只做领域模型层**——值对象、G4 状态机、仓储 record 与 services 适配；不改持久化（DDL/迁移/列名/JSON 列内容布局）、不改 HTTP wire。

## 2. 范围与非目标

**范围内**：collet.py 值对象重构、`collet_lifecycle` G4 状态机、仓储 record 结构调整（record 承接移出聚合的字段）、services/G1 对新字段名的引用适配、测试。

**非目标**：

- 不改 `ac_context_collab_collet` 表结构（迁移已冻结，列名 `collab_context_id`/`actor_ref`/`context_spec` JSON/`current_snapshot` JSON 原样保留）；
- 不改三个北向 API 的请求/响应 wire 字段名；
- **lifecycle_state 持久化推迟 P2（方案 b 裁决）**：状态只存在于进程内 Collet 值对象上，重启归零；P2 经新增列或 JSON 字段落库；
- 多 Space 作用域（P2）；G3 演进对象、L1 envelope 交付（不属本层）。

## 3. ACCS 逐条依据与字段裁决

| ACCS 依据 | 裁决 |
| --- | --- |
| ACCS-001 §2：Collet ID 字段 `collet_id`；稳定、不复用 | `Collet.collab_context_id` → `collet_id`（仓储列名不动，repository 做 `collet_id ↔ collab_context_id` 列映射） |
| ACCS-104 §2：独立**版本** | `Collet.context_version` → `version` |
| ACCS-104 §2：独立**状态** + §3 Context 操作族 | 新增 `state: ColletState` 与 G4 机器 `collet_lifecycle`（§4）；Task 状态词汇归 Task，不再借用 |
| ACCS-104 §7：必须有**发起者** | `actor_ref: PrincipalRef \| None` → `initiator_ref: PrincipalRef`（必填；命名裁决：文档无字段名，取语义化 `initiator_ref`） |
| ACCS-104 §7：必须有**作用域** | `ContextSpec.space` 升为聚合根字段 `scope: SpaceRef` |
| ACCS-104 §2：保留重建关键因果链的 provenance | `current_event_id: str \| None` 保留（事件 DAG 锚点） |
| ACCS-104 §4.2：“Task 已表达协作目标，L4 不再定义平行目标对象” | `objective` 留在 Task 语义（初始 `task.created` 事件 payload、ContextSpec JSON 物理不动） |
| ACCS-105：method 归 L5 MethodBinding | `method_kind` 不进 Collet（顶层 MethodBinding 已承载） |
| ACCS-104 §2：Participant/RoleBinding 是独立对象 | `executor_ref` 不进 Collet（MethodBinding.participant_ref / RoleBinding 承载；物理留 ContextSpec JSON） |

## 4. Collet 生命周期状态机

新 G4 模块 `g4_contracts_consistency/context_state_machines.py`，注册表 `COLLET_MACHINE_REGISTRY`，含一台 `collet_lifecycle`（不并入 `METHOD_MACHINE_REGISTRY`——那是 L5 的）：

```text
CREATED ─activate→ ACTIVE ⇄ SUSPENDED
ACTIVE/SUSPENDED ─complete→ COMPLETED ─archive→ ARCHIVED（终态）
```

- 操作族映射（ACCS-104 §3 Context 族）：`create`（建 CREATED）→ `activate` → `suspend`/`resume`（对应操作族语义 "suspend"；resume 为可达性补漏）→ `complete` → `archive`；
- 迁移裁决一律经 `resolve_transition`，非法迁移抛 `ContextCollabValidationError`；guard_refs 空（守卫语义由服务层断言）；
- 迁移 token（`collet.created/activated/suspended/resumed/completed/archived`）只存在于机器定义，**不**注册进 `REGISTERED_EVENT_KINDS` 事件 schema（P1 不落事件）；
- 校验器独立：不复用钉死 TaskStatus 词汇的既有校验器，照 L5 `validate_method_machine` 模式新建 `validate_collet_machine` + `COLLET_STATE_GOVERNANCE` 治理类别映射；`COMPLETED` 因需保有 `archive` 出边，不得归入 terminal 类别（自定义 non-terminal 类别）；
- complete/archive 不删除、不级联 L3 Resource（§7）；
- **不自动级联**：noop 行为不连锁（如 suspend 不冻结事件追加的裁决属服务层，本 spec 不引入新规则，保持既有 append 行为）；
- 与 L5 顶层 binding 的 `terminate` 门槛解耦：binding 终止语义照旧，本机不读 Collet snapshot；
- conformance 测试放**新文件** `test_context_g4_conformance.py`（不并入 METHOD 侧文件）。

## 5. 聚合根重构

### 5.1 新 `Collet` 值对象

```python
@dataclass(frozen=True)
class Collet:
    collet_id: str                  # 非空；from_dict 校验
    initiator_ref: PrincipalRef     # 必填（§3）
    state: ColletState              # 新；进程内语义（方案 b）
    scope: SpaceRef                 # 作用域
    current_event_id: str | None    # provenance 锚点
    version: int                    # ACCS 独立版本
    # from_dict/to_dict：collet_id 非空、state 合法值、version ≥ 1；
    # 未知字段拒绝（沿 participants.py 风格）
```

### 5.2 移出聚合根（类型保留，归属变更）

- `ContextSpec` → **创建参数快照**：不再是 `Collet` 字段，由仓储 record 承载；其 JSON 列内容与 GET 读路径零变化。G1 `check_append` 继续读 `context_spec.method_kind`（来源从 `collet.context_spec` 改为 record 伴随字段，语义不变）。
- `CurrentSnapshot` + `ProgressSnapshot` → **读投影**：不再是 `Collet` 字段，由仓储 record 承载；`AppendEventRecordRequest.snapshot_updater` 合同不变。
- 仓储 record 调整：`ContextCreatedRecord`/`ContextStatusRecord`/`EventAppendedRecord` 增加伴随字段（`context_spec`、`current_snapshot`），`collet` 字段只含 §5.1 聚合根。

### 5.3 生命周期操作（服务层）

- **状态存活机制（仿 L5 `InMemoryMethodStore` 先例）**：新增进程内 `InMemoryColletLifecycleStore` DI 单例（整 `threading.Lock`，`collet_id → ColletState`）；**不落数据库、不落 store 列、P2 换成 DB 列/持久化实现，服务层零改动**。重启归零（方案 b 弱化）；store 未见过的 `collet_id` 与 `create_context` 新建均种子 **ACTIVE**（沿“创建即可协作”既有裁决；`CREATED` 保留在机器词汇、可达性由 conformance 测试直接构造值验证）。键不带租户前缀（Collet ID 全局唯一，`ac_context_collab_collet` 唯一键语义不变）。
- 五操作输入 `collet_id` + `ActorContext`（+可选 `reason`），经 §4 机器 `resolve_transition` 裁决后替换 store 内状态；**不落事件、不写库**。
- 操作返回小型结果对象（`old_state`、`new_state`、`actor` 主标识、`reason`），**不**给聚合根加审计字段——审计字段与事件化一起归 P2。
- 重复操作幂等拒绝（非法迁移即天然防重，沿 G2 §8.3 既有裁决）。
- **守卫语义**：`activate/suspend/resume/complete/archive` 仅发起者本人或 executor 结构匹配可调（复用 `_is_principal/_is_executor` 判定语义，内联断言）；**不**扩 `ContextCollabAuthorizerProtocol` 合同。
- P1 纯内部 API：不暴露 HTTP wire（领域模型层裁决）。

## 6. 兼容性

1. **wire**：三个北向 API 请求/响应字段名零变化（`collab_context_id` wire 字段照旧；HTTP 层映射新领域名）。
2. **持久化**：两张表、全部列、既有迁移不动；repository 对内消费新领域名（`collet_id`/`initiator_ref`/`version`），对外读写旧列名。
3. **既有语义**：append 事件链、G1 授权读面、`expected_version` 乐观并发、L5 顶层 binding 投影读 `context_spec.method_kind`——全部行为不变，仅字段载体从 `collet.*` 迁到 record 伴随字段。
4. **发起者严格读（方案 i 裁决）**：仓储解码读面收紧——`actor_ref` 为 NULL 的存量/外部部分行解码即抛 `ContextCollabValidationError`（响亮失败，不伪装）；`current_snapshot` 为 NULL 的容忍不受影响（快照移出聚合根，仍是 record 可空伴随字段，`test_snapshotless_collet_*` 用例语义保留）。DDL 列仍 nullable，不迁移。
5. **重命名边界**：**只改领域聚合根与仓储 record**；`service_api` DTO 字段名（`collab_context_id`/`context_version`）不改——它们是应用/交付合同，HTTP adapter 零改动。仓储对内消费 `collet_id`/`initiator_ref`/`version`，对 DB 读写旧列名。
6. **dict 序列化（P2 前置合同）**：`Collet.from_dict/to_dict` 键 = 新字段名 snake_case；`state` 序列化用机器大写词汇值（`"CREATED"` 等）；record 伴随字段用领域对象类型（`ContextSpec`/`CurrentSnapshot`），保留往返校验。
7. `ColletEvent` 俗名清理不在本次范围（沿用既有事实）。

## 7. 测试策略

并列 `tests/community/core/task/context_collab/`：

| 文件 | 覆盖 |
| --- | --- |
| `test_collet_aggregate.py`（新建） | 字段校验、`initiator_ref` 必填、state 合法值、round-trip、record 承载 spec/snapshot 重建、NULL `actor_ref` 行解码抛错（方案 i） |
| `test_context_g4_conformance.py`（新建） | `collet_lifecycle` 注册校验、词汇、治理类别（COMPLETED 非 terminal）、CREATED 可达性 |
| `test_collet_lifecycle.py`（新建） | 五操作状态机正反例、store 种子 ACTIVE、幂等拒绝、守卫（本人/executor 放行、陌生 actor 拒）、重启归零语义 |
| `test_context_collab_service.py` / `test_service_real_repository.py`（更新） | 服务拆用 record 字段后 create/append/get 零回归 |
| 既有 L5 四服务测试 + DI 测试 | `record.collet.context_spec` 改 record 伴随字段后的适配回归 |

按需只跑 `context_collab` 目录测试；CI 门禁延后统一处理。

## 8. 作者裁决（实现者不需再问）

1. 只做领域模型层；lifecycle_state 落库是 P2（方案 b）；重命名止步领域聚合根 + record，`service_api` DTO 不动。
2. `collet_id`/`initiator_ref`/`version`/`scope` 命名为本 spec 裁决（ACCS 只约束语义不约束字段名，可评审推翻）。
3. 状态存活采用进程内 `InMemoryColletLifecycleStore` 单例（整锁、keyed by `collet_id`）；未见过的 collet 与新建均种子 ACTIVE；重启归零。
4. 发起者严格读（方案 i）：NULL `actor_ref` 行解码抛 `ContextCollabValidationError`；DDL 不改。
5. `resume` 补漏操作一并实现；操作返回结果对象，聚合根不加审计字段（P2 与事件化同批）。
6. `archive` 后不再接受 activate/complete（终态）；COMPLETED 在治理类别里非 terminal（保有 archive 出边）。
7. 五操作守卫：发起者本人/executor 结构匹配（`_is_principal/_is_executor` 语义），不扩授权协议合同；P1 内部 API 不上 wire。
8. `collet.*` 迁移 token 不注册进事件 schema。

## 9. 留给实现者的空间

- record dataclass 具体字段排布；
- 生命周期操作挂在 ContextCollabService 还是独立帮助模块；
- 测试断言的具体构建方式。
