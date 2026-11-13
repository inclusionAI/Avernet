# Context Collab L5 完整领域对象 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 ACCS-105 公共合同 + ACCS-301–304 四个 Method Profile 全量实现 context_collab 的 L5 领域对象：代码内 MethodProfile 合同、MemoryMethod（13 类对象，进程内 `InMemoryMethodStore` 存储，持久化推迟 P2）、操作族 Service API + HTTP 路由、G1/G4 接缝，wire 逐字节兼容。

**Architecture:** 沿 context_collab 模块既有五层缝隙（core 值对象 → G4 声明式状态机 → G1 协议接缝 → service 编排 → store → HTTP delivery**）。每个有副作用操作 = store 状态迁移（G4 机器裁决）+ G1 结构授权 + 对象审计四字段；不进 L4 事件 DAG、不回写 `context_spec`、**不建任何表/DDL/ORM 模型**。

**Tech Stack:** Python 3.12、python-injector DI、FastAPI delivery、pytest（Collet 既有路径仍走 SQLite 真仓储；L5 对象走内存 store）。

**Spec:** `apps/backend/specs/2026-11-12-context-collab-l5-method-objects/spec.md`（下称"spec"；字段语义、状态机词汇、矩阵、作者裁决 15 条以 spec 为准，本计划不重复）。

## Global Constraints

- 分支：`dev_bbs_help_1019_3`，直接叠加当前未提交的 L6 product-forms 改动（已作为独立提交落库）之上；不动 L6 语义。
- 测试命令统一在 `apps/backend/` 下：`DEPLOY_PROFILE=test uv run pytest tests/community/core/task/context_collab/<file> -v`（spec §11）。
- CI 门禁（Task 15 前必过一次）：`./scripts/ci_test.sh`，改动行覆盖率 ≥80%。
- 提交标题格式：`feat(backend): <outcome>`（spec/测试相应 `docs/backend/test`）；每次提交末尾加 `Co-Authored-By: Claude <noreply@anthropic.com>`。
- **不新增** SQL 文件、不改 `persistence/models.py`、不改 `CreateContextRecordRequest` 签名；L5 全部对象仅存于 `InMemoryMethodStore`（进程单例，经 DI 绑定；键带 `(avernet_tenant, env)` 前缀；整锁 `threading.Lock` 串行化）。
- 任一文件 ≤1000 行，超限先拆再写；不格式化未改动的行（仓库 CLAUDE.md）。
- 异常体系只用既有 `ContextCollabValidationError` / `ContextCollabNotFoundError` / `ContextCollabVersionConflict`（`core/task/context_collab/errors.py`）。
- 所有 `*_ref` 只格式校验（复用 L2/L3 ref 值对象解析风格），不做存在性验证（spec §12 第 11 条）。
- wire 逐字节兼容（spec §9）：`method_kind` 字段名/语义/既有取值不变；`MethodKind.from_wire` 未知值仍抛 `ContextCollabValidationError`。

## Review Focus

1. **裸 method_kind 顶层混入**——`create_context` 传 `"Group"/"Open"/"Swarm"` 必须拒绝（只允许 `Single`/`Open + Group`），旧调用方零影响。→ Task 8 测试 `test_create_rejects_bare_group_open_swarm_top_level`。
2. **binding 非 ACTIVE 时的 Profile 副作用**——BOUND/SUSPENDED/TERMINATED 绑定上任何 select/member/allocate/publish 操作拒绝。→ Task 9–12 各状态守卫用例（每任务至少 1 个）。
3. **幂等键冲突语义**——同 ID 同形态返回已存在对象；同 ID 异形态 409。→ Task 5 store 用例 + Task 8 服务用例 `test_bind_idempotent_same_shape_conflict_different_shape`。
4. **组合图终止性**——NEST/DELEGATE_TO/ESCALATE 环拒绝；深度超默认 8 拒；PARALLELIZE 跨 kind 拒。→ Task 3（环/矩阵）+ Task 8（深度上限服务层）。
5. **并发 roster 更新丢失**——store 锁内 revision 比对失败必须抛 `ContextCollabVersionConflict`，不得静默成功（仓库 CLAUDE.md：写失败必须传播）。→ Task 10 `test_roster_revision_conflict`。

---

### Task 1: MethodKind 扩展 + MethodProfile 注册表

**Files:**
- Modify: `src/agentclaw/community/core/task/context_collab/l5_methods/method_binding.py`
- Create: `src/agentclaw/community/core/task/context_collab/l5_methods/method_profile.py`
- Test: `tests/community/core/task/context_collab/test_network_profiles.py`（既有）、`tests/community/core/task/context_collab/test_method_profiles.py`（新建）

**Interfaces:**
- Produces: `MethodKind`（新增 `GROUP="Group"`、`OPEN="Open"`、`SWARM="Swarm"`）；`parse_method_kinds(wire: str) -> tuple[MethodKind, ...]`；`MethodProfileDefinition`（frozen dataclass，字段 = spec §3.1：`method_kind`、`profile_version="0.2"`、`capabilities`、`primitives_used`、`participation_model`、`composable_relations`）；`METHOD_PROFILE_REGISTRY: dict[str, MethodProfileDefinition]`（键 = "Single"/"Group"/"Open"/"Swarm"）；`get_profile(method: str) -> MethodProfileDefinition`。
- Consumes: 无（首个任务）。

- [ ] **Step 1: 修正既有拒绝用例并写失败测试**

`test_network_profiles.py` 的 `test_method_kind_rejects_unknown_wire_values` 参数列表去掉 `"Swarm"`（保留 `""`、`"open+group"`、`"single"`，可补 `"swarm"` 小写）；`test_method_kind_accepts_spec_values` 增加 GROUP/OPEN/SWARM 断言。新建 `test_method_profiles.py`：

```python
def test_parse_method_kinds_composite_and_single():
    assert parse_method_kinds("Open + Group") == (MethodKind.OPEN, MethodKind.GROUP)
    assert parse_method_kinds("Single") == (MethodKind.SINGLE,)

def test_parse_method_kinds_rejects_unknown():
    with pytest.raises(ContextCollabValidationError):
        parse_method_kinds("Swarm + Group")

def test_registry_covers_four_core_methods():
    assert set(METHOD_PROFILE_REGISTRY) == {"Single", "Group", "Open", "Swarm"}

def test_profile_declares_composition_relations_and_primitives():
    for name, profile in METHOD_PROFILE_REGISTRY.items():
        assert profile.profile_version == "0.2"
        assert profile.composable_relations  # 非空，与 Task 3 矩阵行一致（Task 3 再钉）
        assert set(profile.primitives_used) <= {"Task", "Message", "Artifact", "Decision", "Query", "Activity"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `DEPLOY_PROFILE=test uv run pytest tests/community/core/task/context_collab/test_method_profiles.py tests/community/core/task/context_collab/test_network_profiles.py -v` — 预期新文件 FAIL（模块不存在）。

- [ ] **Step 3: 实现枚举扩展与注册表**

`method_binding.py` 在 `MethodKind` 增加三值；`from_wire` 行为不变。新增模块级 `parse_method_kinds`：按 `" + "` 拆分、逐项映射 `MethodKind`、任意未知片段抛 `ContextCollabValidationError`；`"Single"` 等单值直接映射。`method_profile.py` 按 spec §3.1 落四个 Profile（capabilities 取自 301–304 §Operation Model 语义；`composable_relations` 与 Task 3 矩阵的父行一致——Single→("nest","delegate_to","support","escalate") 等，Task 3 有对齐断言）。

- [ ] **Step 4: 跑测试确认通过**（同 Step 2 命令，预期全 PASS）
- [ ] **Step 5: Commit** `feat(backend): extend method kinds and add L5 method profile registry`

### Task 2: G4 Method 状态机注册表

**Files:**
- Create: `src/agentclaw/community/core/task/context_collab/g4_contracts_consistency/method_state_machines.py`
- Test: `tests/community/core/task/context_collab/test_method_g4_conformance.py`

**Interfaces:**
- Consumes: `StateMachineDefinition`、`validate_definition`（既有 `g4_contracts_consistency/state_machine.py`）。
- Produces: `METHOD_MACHINE_REGISTRY: dict[str, StateMachineDefinition]`，键 = `method_binding`、`single_selection`、`group_member`、`swarm_plan`、`swarm_stage`、`swarm_allocation`、`swarm_execution_record`、`open_opportunity`、`open_application`（九台）。guard_refs 一律空（守卫语义由服务层断言，G4 不依赖 store）。

- [ ] **Step 1: 写失败测试**（状态机词汇 = spec §3.2/§4.1–4.4 YAML 词汇逐字；迁移规则 = spec 操作族）

```python
def test_all_method_machines_pass_g4_validation():
    for machine in METHOD_MACHINE_REGISTRY.values():
        validate_definition(machine)  # 可达性豁免沿用全局规则

def test_binding_lifecycle_machine_vocabulary():
    m = METHOD_MACHINE_REGISTRY["method_binding"]
    assert {s.state_name for s in m.states} == {"BOUND", "ACTIVE", "SUSPENDED", "TERMINATED"}
```

治理类别映射（写进模块 docstring）：入口/waiting→`BOUND/PROPOSED/INVITED/DRAFT/PENDING/SUBMITTED`；active→`ACTIVE/READY/RUNNING/OPEN`；review→`SCREENING/ALLOCATING`；blocked→`SUSPENDED/PAUSED`；terminal→`REPLACED/RELEASED/LEFT/REMOVED/COMPLETED/SUCCEEDED/ADMITTED/CLOSED`（success 系）与 `FAILED/CANCELLED/REJECTED/REVOKED/SKIPPED/EXPIRED/WITHDRAWN`（cancelled/failed 系）。

- [ ] **Step 2: 跑失败** — `DEPLOY_PROFILE=test uv run pytest tests/community/core/task/context_collab/test_method_g4_conformance.py -v` FAIL（模块不存在）。
- [ ] **Step 3: 实现九台机器**，模块加载时逐台 `validate_definition`（快速失败）。
- [ ] **Step 4: 跑通过；再跑** `.../test_state_machine.py -v` 确认既有 G4 测试零回归。
- [ ] **Step 5: Commit** `feat(backend): register L5 method state machines with G4`

### Task 3: MethodCompositionEdge 值对象 + 组合矩阵 + 环检测

**Files:**
- Create: `src/agentclaw/community/core/task/context_collab/l5_methods/method_composition.py`
- Test: `tests/community/core/task/context_collab/test_method_composition.py`（纯单元，无 store）

**Interfaces:**
- Produces: `CompositionRelation(str, Enum)`（`NEST="nest"`、`DELEGATE_TO="delegate_to"`、`SUPPORT="support"`、`PARALLELIZE="parallelize"`、`ESCALATE="escalate"`；`from_wire` 接受 `"delegate-to"` 别名）、`CompositionEdgeState(str, Enum)`（`ACTIVE`/`DETACHED`）、`COMPOSITION_MATRIX: dict[tuple[str, CompositionRelation], frozenset[str]]`（spec §3.3 表格逐格固化；任意父 × NEST/SUPPORT/DELEGATE_TO → 任意核心方法；PARALLELIZE 仅同 kind；Single→ESCALATE→{Group,Swarm}；Group→ESCALATE→{Swarm}；Swarm→ESCALATE→无）、`validate_composition(parent_method: str, relation: CompositionRelation, child_method: str) -> None`（违规抛 `ContextCollabValidationError`）、`detect_cycle(pairs: Sequence[tuple[str, str]]) -> bool`（迭代 DFS，空/重复入参安全）、`MethodCompositionEdge` frozen dataclass（spec §3.3 YAML 字段 + §6.3 审计四字段）`from_dict/to_dict`（风格沿 `l4_context_primitives/participants.py`：必填字段缺失抛校验错）。
- Consumes: Task 1 `MethodKind`、`METHOD_PROFILE_REGISTRY`。

- [ ] **Step 1: 写失败测试** — 覆盖矩阵每一行 + `"delegate-to"` 别名 + `detect_cycle` 正反例 + `from_dict` 缺字段拒绝；加 `test_matrix_agrees_with_profile_declarations`（遍历 `METHOD_PROFILE_REGISTRY`，断言其 `composable_relations` 与矩阵行一致）。
- [ ] **Step 2: 跑失败（模块不存在）→ Step 3: 实现（矩阵按 spec §3.3；`validate_composition` 父×关系行不存在即拒）→ Step 4: 跑通过**
- [ ] **Step 5: Commit** `feat(backend): add method composition matrix and acyclic edge validation`

### Task 4: MethodBinding 值对象

**Files:**
- Modify: `src/agentclaw/community/core/task/context_collab/l5_methods/method_binding.py`
- Test: `tests/community/core/task/context_collab/test_method_binding.py`（新建；生命周期服务用例在 Task 8）

**Interfaces:**
- Produces: `BindingState(str, Enum)`（`BOUND`/`ACTIVE`/`SUSPENDED`/`TERMINATED`）；`MethodBinding` frozen dataclass（字段 = spec §3.2 YAML 含 `version: int`、`g3_subject_type: str | None = None`、§6.3 审计四字段 `transitioned_from/transition_reason/transitioned_by/transitioned_at`）+ `from_dict(data: dict) -> MethodBinding` / `to_dict()`。校验规则：`method_binding_id`/`collab_context_id` 非空；`method_kind` 经 `MethodKind.from_wire`；`method_names` 与 `parse_method_kinds(method_kind)` 投影一致；`profile_versions` 键集 == method_names 集且值非空；`config["max_compose_depth"]` 缺省 8、必须 1–64 整型；未知字段拒绝。
- Consumes: Task 1。

- [ ] **Step 1: 写失败测试** — round-trip、`method_names` 不一致拒绝、非法 `state` 拒绝、`config` 深度默认 8 与越界拒绝、未知字段拒绝。
- [ ] **Step 2: 失败 → Step 3: 实现 → Step 4: 通过**（同 Task 1 命令，目标文件换 `test_method_binding.py`）。
- [ ] **Step 5: Commit** `feat(backend): add MethodBinding value object with wire validation`

### Task 5: InMemoryMethodStore（binding/edge 切片 + 锁骨架）

**Files:**
- Create: `src/agentclaw/community/core/task/context_collab/persistence/method_store.py`
- Test: `tests/community/core/task/context_collab/test_method_store.py`

**Interfaces:**
- Produces: `MethodStoreProtocol(Protocol)` + `InMemoryMethodStore`（整锁 `threading.Lock`；全部键带 `(avernet_tenant, env)` 前缀；dict 按 ID 保插入序）。本任务交付 binding + composition edge 切片，后续任务在同一文件追加各自 slice：
  - `upsert_binding(binding: MethodBinding) -> MethodBinding`（幂等：同键同形态返回既有；异形态抛 `ContextCollabValidationError`）
  - `get_binding(method_binding_id: str, collab_context_id: str) -> MethodBinding | None`、`replace_binding(binding: MethodBinding) -> None`（不存在抛 `ContextCollabNotFoundError`；服务层乐观版本经其为语义换新实例）
  - `list_bindings(collab_context_id: str) -> list[MethodBinding]`
  - `insert_composition_edge(edge) -> edge`（同端点同关系 ACTIVE 重复插入幂等）、`replace_composition_edge`（detach 用）、`list_composition_edges(collab_context_id, *, active_only=True) -> list`、`get_composition_edge(edge_id, collab_context_id)`
  - 构造入参：无（DI 单例）；测试各自新起实例。
- Consumes: Task 3/4 值对象。

- [ ] **Step 1: 写失败测试** — `test_upsert_binding_idempotent_same_shape`（RF #3）、`test_upsert_binding_conflict_different_shape`、`test_binding_keys_isolated_per_tenant_env`、edge 幂等/覆盖/detach 后 `active_only` 不可见、`test_lock_serializes_concurrent_upserts`（两线程交错 upsert 断言不丢）。
- [ ] **Step 2: 失败 → Step 3: 实现 → Step 4: 通过**
- [ ] **Step 5: Commit** `feat(backend): add in-memory L5 method store with binding and edge slices`

### Task 6: G1 MethodOps 授权接缝

**Files:**
- Create: `src/agentclaw/community/core/task/context_collab/g1_secure_govern/method_authorization.py`
- Test: `tests/community/core/task/context_collab/test_method_authorization.py`

**Interfaces:**
- Produces: `MethodOpsAuthorizerProtocol(Protocol)`（`check_lifecycle_op(op: str, binding: MethodBinding, actor: ActorContext) -> None`、`check_group_op(op, binding, actor, member=None)`、`check_swarm_op(op, binding, actor)`、`check_open_op(op, binding, actor, application=None)`）+ `StructuralMethodOpsAuthorizer` 默认实现（规则 = spec §8.1；principal/executor 判定沿 `authorization.py` 既有 `_is_principal/_is_executor` 语义；提取共用小函数时**不得**改动既有 `check_create/check_append` 行为）。自助操作：`Withdraw`/`Leave` 用 `actor.actor.principal_id` 与目标行标识**字符串相等**（spec §12 第 8 条）。
- Consumes: Task 4 `MethodBinding`；`service_api.ActorContext`；既有 `g1_secure_govern/authorization.py` 的判定先例。

- [ ] **Step 1: 写失败测试** — 矩阵式：principal/executor/陌生 actor × lifecycle/group/swarm/open 操作；本人比对正反例；越权一律 `ContextCollabValidationError`。
- [ ] **Step 2: 失败 → Step 3: 实现 → Step 4: 通过**
- [ ] **Step 5: Commit** `feat(backend): add structural L5 method authorization seam`

### Task 7: Method Service API 契约

**Files:**
- Create: `src/agentclaw/community/core/task/context_collab/method_service_api.py`
- Test: `tests/community/core/task/context_collab/test_method_service_api.py`

**Interfaces:**
- Produces（dataclass + `runtime_checkable Protocol`，风格对照既有 `service_api.py`）：
  - 命令：`BindMethodCommand`（幂等键 `method_binding_id` 可空→服务端生成；含 `method_kind`/`parent_scope_ref`/`participant_ref`/`config`/`bind_reason`）、`MethodStateChangeCommand`（含 `reason`）、`UpdateBindingConfigCommand`（`expected_version`）、`ComposeMethodCommand`（`relation`/`child_binding_id`/`child_method_kind`）；检视结果 `MethodInspectionResult`（`binding: dict`、`state_summary: dict`、`capabilities: dict`、`conformance_evidence: dict`）。
  - 四协议：`MethodLifecycleServiceProtocol`（`bind/activate/suspend/resume/terminate/update_config/inspect/compose/detach`）、`GroupMethodOpsServiceProtocol`（Single 四操作 `select/replace/release/delegate` + group 八操作 `invite/add_member/suspend_member/resume_member/leave/remove_member/replace_member/change_role`）、`SwarmMethodOpsServiceProtocol`（11 操作 spec §4.3）、`OpenMethodOpsServiceProtocol`（11 操作 spec §4.4）。方法签名统一起头 `(collab_context_id: str, method_binding_id: str, command, context: ActorContext)`，追加参数（如 `target_stage_ref`、`expected_revision`）在命令里。
- Consumes: Task 1 枚举、Task 6 协议名。
- 测试只验 Protocol shape + dataclass 默认值（纯结构测试）。

- [ ] **Step 1: 写失败测试 → Step 2: 失败 → Step 3: 实现 → Step 4: 通过**
- [ ] **Step 5: Commit** `feat(backend): add L5 method service API contracts`

### Task 8: 生命周期服务 + create_context 顶层投影 + 读路径自愈

**Files:**
- Create: `src/agentclaw/community/core/task/context_collab/services/method_lifecycle_service.py`
- Modify: `src/agentclaw/community/core/task/context_collab/services/context_collab_service.py`（构造器新增 `method_store: MethodStoreProtocol`；create_context 成功后调用投影帮助函数）
- Modify: `src/agentclaw/community/di/modules/task_module.py`（`MethodStoreProtocol → InMemoryMethodStore` singleton；本任务只补 ContextCollabService 新参所需绑定，其余协议绑定在 Task 13）
- Test: `tests/community/core/task/context_collab/test_method_lifecycle_service.py`（新建）、`test_method_binding.py`（追加生命周期用例）、既有 `test_context_collab_service.py`/`test_l6_product_e2e.py` 补 store 注入参数

**Interfaces:**
- Produces: `MethodLifecycleService(store: MethodStoreProtocol, authorizer: MethodOpsAuthorizerProtocol, collab_repo: ContextCollabRepositoryProtocol)` 实现 Task 7 lifecycle 协议 + 模块级纯函数 `project_created_context(store, spec: ContextSpec, actor: ActorContext) -> MethodBinding`（顶层 ACTIVE binding + Open+Group 时 roster/初始成员草稿在 Tasks 9–10 接入后补挂；本任务先落 binding）与 `ensure_top_level_binding(store, collab_repo, collab_context_id) -> MethodBinding`（§6.4 读路径自愈：缺顶层 binding 时按 `context_spec.method_kind` 重建 ACTIVE 投影）。规则（spec §3.2/§9）：
  - `bind`：嵌套绑定初始 BOUND；`activate/suspend/resume/terminate` 走 `METHOD_MACHINE_REGISTRY["method_binding"]` 裁决 + 审计四字段；`update_config` 乐观 `version`；`terminate` 顶层门槛：Collet `current_snapshot` 任务状态为 `task.cancelled`/`task.accepted` 才放行；terminated 后 compose 出边创建拒绝。
  - `compose`：`validate_composition` → 全图 `detect_cycle`（store active 边 + 新边）→ 深度 ≤ `config.max_compose_depth`（默认 8）→ 落边。
  - `inspect`：binding 投影 + state_summary + capabilities（自 `METHOD_PROFILE_REGISTRY`）+ conformance_evidence（G4 机器校验 + 对象计数）。
  - G1 `StructuralContextCollabAuthorizer.check_create` 扩展：顶层只允许 `Single`/`Open + Group`（裸 `Group/Open/Swarm` 拒绝；spec §9.5）——只加新用例不动旧断言。
- Consumes: Task 1–7 全部。

- [ ] **Step 1: 写失败测试** — 状态迁移全矩阵（非法迁移拒绝 + 重复 activate 幂等拒绝）、`test_create_rejects_bare_group_open_swarm_top_level`（RF #1）、`test_top_level_binding_created_active_and_projects_context_spec`、`test_terminate_top_level_requires_terminal_collet_status`、`test_bind_idempotent_same_shape_conflict_different_shape`（RF #3）、`test_compose_rejects_cycle_and_depth_overrun`（RF #4）、`test_inspect_rebuilds_missing_top_level_binding`（§6.4 自愈）。
- [ ] **Step 2: 跑失败 → Step 3: 实现 → Step 4: 跑通过**，并跑 `.../test_context_collab_service.py .../test_service_real_repository.py .../test_l6_product_e2e.py -v` 确认 create 路径零回归。
- [ ] **Step 5: Commit** `feat(backend): add method lifecycle service with context-create projection`

### Task 9: Single Profile 值对象 + 服务 + store 切片

**Files:**
- Create: `src/agentclaw/community/core/task/context_collab/l5_methods/single_profile.py`（`SingleSelection` frozen dataclass + 状态枚举对齐 `METHOD_MACHINE_REGISTRY["single_selection"]`）
- Modify: `src/agentclaw/community/core/task/context_collab/persistence/method_store.py`（追加 `insert_selection`/`get_active_selection(method_binding_id, scope_ref)`/`replace_selection(old, new)` 锁内原子/`update_selection_state`）
- Create: `src/agentclaw/community/core/task/context_collab/services/single_group_method_service.py`（本任务只交付 Single 四操作）
- Test: `tests/community/core/task/context_collab/test_single_profile.py`

**Interfaces:**
- Produces: `SingleGroupMethodService` 的 Single 段（`select` 断言同 binding+scope 无 ACTIVE、`replace` 锁内原子旧→REPLACED/新→ACTIVE、`release`、`delegate` = 嵌套 binding + DELEGATE_TO 边，复用 Task 8 bind/compose 帮助函数）。RF #2 用例：`test_single_ops_reject_non_active_binding`。
- Consumes: Task 8 服务内部帮助函数、Task 5 store 追加切片、Task 6 授权、Task 7 命令。
- RFC-301 §9 五条一致性断言逐条入测试（含"Query 结果不自动成为 selection"=select 必须显式携带 agent 引用）。

- [ ] **Step 1: 失败测试 → Step 2: 失败 → Step 3: 实现 → Step 4: 通过**
- [ ] **Step 5: Commit** `feat(backend): implement Single method profile operations`

### Task 10: Group Profile 值对象 + 服务 + store 切片

**Files:**
- Create: `src/agentclaw/community/core/task/context_collab/l5_methods/group_profile.py`（`GroupRoster` + `GroupMemberBinding` frozen dataclass）
- Modify: `persistence/method_store.py`（`insert_roster`/`get_roster`/`bump_roster_revision(method_binding_id, expected_revision)`（不匹配抛 `ContextCollabVersionConflict`，RF #5）/`insert_member`/`replace_member`/`update_member_state`/`list_members`）
- Modify: `services/single_group_method_service.py`（group 八操作补全）
- Test: `tests/community/core/task/context_collab/test_group_profile.py`

**Interfaces:**
- Produces：成员状态机经 `METHOD_MACHINE_REGISTRY["group_member"]` 裁决；每成员变更一次 roster revision bump（spec §4.2 规格：原子双写在 `replace_member` 中为一次 bump）；`change_role` 写 `role_binding_refs`（格式校验）；`invite` 拒绝未终态重复参与者；`leave` 本人（Task 6）。RF #2 守卫用例本任务 1 个。`project_created_context` 在此补挂 roster + 初始成员（principal/executor ACTIVE 成员行）。
- Consumes: Task 8 帮助函数、Task 6。

- [ ] **Step 1: 失败测试（九状态全覆盖含 `ResumeMember/Leave` 可达性；`test_roster_revision_conflict`）→ 2 → 3 → 4**
- [ ] **Step 5: Commit** `feat(backend): implement Group method profile operations`

### Task 11: Swarm Profile 值对象 + 服务 + store 切片

**Files:**
- Create: `src/agentclaw/community/core/task/context_collab/l5_methods/swarm_profile.py`（5 个 frozen dataclass）
- Modify: `persistence/method_store.py`（plan/stage/allocation/execution_profile/execution_record 五组 CRUD + 状态替换）
- Create: `src/agentclaw/community/core/task/context_collab/services/swarm_open_method_service.py`（Swarm 段）
- Test: `tests/community/core/task/context_collab/test_swarm_profile.py`

**Interfaces:**
- Produces: Swarm 11 操作（spec §4.3）。结构校验：Stage 依赖图无环（`detect_cycle` 复用）、`max_depth/max_parallelism/Stage` 数、并行 ACTIVE allocation ≤ max_parallelism、`lease_expires_at > now` 断言、预算 refs 格式校验；`submit_plan_candidate` 自建 `SwarmExecutionRecord`；`allocate_stage` 要求已声明 `SwarmExecutionProfile`；机器裁决用 swarm_plan/swarm_stage/swarm_allocation 三台；binding 非 ACTIVE 拒绝（RF #2）。

- [ ] **Step 1: 失败测试（Stage 图无环、深度/并行上限、lease 过期拒绝 `CompleteStage`、外部 record 不改权威状态（adopt 前后断言 plan 状态）、`ResumePlan` 可达）→ 2 → 3 → 4**
- [ ] **Step 5: Commit** `feat(backend): implement Swarm method profile authority-plane operations`

### Task 12: Open Profile 值对象 + 服务 + store 切片

**Files:**
- Create: `src/agentclaw/community/core/task/context_collab/l5_methods/open_profile.py`（3 个 frozen dataclass）
- Modify: `persistence/method_store.py`（opportunity/application/decision 三组 CRUD + 状态替换 + application 终态判定）
- Modify: `services/swarm_open_method_service.py`（Open 11 操作）
- Test: `tests/community/core/task/context_collab/test_open_profile.py`

**Interfaces:**
- Produces（spec §4.4）：容量 = ACTIVE+SUBMITTED+SCREENING 计数 < capacity、窗口校验、`submit_application` 任意已认证 actor；`admit` 幂等按 participation_type 产物（GROUP_MEMBER→ACTIVE 成员、SWARM_ALLOCATION→校验 `target_stage_ref` 属本 binding 作用域 READY/RUNNING Stage、SINGLE_CONTRIBUTION→仅记 refs、COLLET_PARTICIPANT→拒绝）；`revoke_admission` 按 resulting_binding_refs 终结成员/分配；`withdraw` 本人。binding 非 ACTIVE 拒绝；admitted ≠ Space Membership 断言。

- [ ] **Step 1: 失败测试 → 2 → 3 → 4**
- [ ] **Step 5: Commit** `feat(backend): implement Open method profile admission operations`

### Task 13: DI 绑定补全

**Files:**
- Modify: `src/agentclaw/community/di/modules/task_module.py`（lifecycle/group/swarm/open 四协议 + `MethodOpsAuthorizerProtocol` 绑定；注释风格沿既有 Context collab 段）
- Test: `tests/community/core/task/context_collab/test_method_lifecycle_service.py`（追加 DI 单例解析用例）

**Interfaces:**
- Consumes: Task 5–12 产出的协议与实现类；Task 8 已绑 `MethodStoreProtocol`。
- 绑定模式照抄既有段（`binder.bind(..., scope=singleton)` + authorizer 显式绑定注释）。
- [ ] **Step 1: 失败测试（四协议 + authorizer 均可从 injector 解析且 singleton）→ 2 → 3: 绑定 → 4: 通过**
- [ ] **Step 5: Commit** `feat(backend): wire L5 method services into DI`

### Task 14: HTTP schemas + 路由

**Files:**
- Create: `src/agentclaw/community/adapters/http/task/schemas_context_collab_methods.py`
- Modify: `src/agentclaw/community/adapters/http/task/context_collab.py`（同 router 追加；超 1000 行则拆 `context_collab_methods.py` 同 prefix）
- Test: `tests/community/core/task/context_collab/test_method_http.py`（沿既有 HTTP 测试放置约定定位并入）

**Interfaces:**
- Produces: 路由清单 = spec §7（bind/激活族/update/inspect/compose/detach + single/group/swarm/open 各段）；DTO Pydantic 字段名与 Task 7 命令一一对应（snake_case wire）；错误映射沿既有（`ContextCollabValidationError`→400、`ContextCollabVersionConflict`→409）。`Envelope[...]` 响应模式照抄既有三端点。
- Consumes: Task 13 后协议可 `Injected(...)`。
- [ ] **Step 1: 失败测试（每资源族 1 happy + RF #1 顶层裸值经 HTTP 拒 + 1 个 409 映射）→ 2 → 3: 实现 → 4: 通过；再跑 `pytest tests/community -k "context_collab or method" -v` 全量回归**
- [ ] **Step 5: Commit** `feat(backend): expose L5 method operations over HTTP delivery`

### Task 15: e2e + CI 门禁

**Files:**
- Test: `tests/community/core/task/context_collab/test_method_e2e.py`
- Modify: `apps/backend/specs/README.md`（spec 索引一行）

**Interfaces:**
- Consumes: 全部前置任务。
- [ ] **Step 1: 写 e2e（Collet 走 SQLite 真仓储 + L5 走 InMemoryMethodStore，沿 `test_l6_product_e2e.py` 模式）**——create_context（Open+Group）→ 顶层 binding ACTIVE 断言 → invite/add_member → open opportunity → submit/screen/admit→成员产物断言 → compose DELEGATE_TO Single 嵌套 binding → activate → select → inspect conformance_evidence 非空。
- [ ] **Step 2: 跑通 e2e**
- [ ] **Step 3: 全量门禁** `DEPLOY_PROFILE=test uv run pytest tests/community -v` 然后 `./scripts/ci_test.sh` — 预期全绿 + 改动行覆盖率达标；失败如实报告不降门槛。
- [ ] **Step 4: spec README 登记新 spec 目录**
- [ ] **Step 5: Commit** `test(backend): cover L5 method objects end to end`

---

## Self-Review 结论

- Spec 覆盖：§3.1→T1、§3.2→T2/T4/T8、§3.3→T3/T8、§4.1–4.4→T9–12、§5→T7–12、§6（内存存储）→T5+各 store 切片、§6.3 审计→T3/T4 值对象字段、§6.4 自愈→T8、§7→T14、§8.1→T6、§8.2→T2、§8.3→T5/T8–12、§9→T1/T6/T8、§10 文件布局与任务 Files 一致（无 sql/models 行）、§11 测试表与 Test 文件一一对应（去 SQLite 字样）。G3 词位→T4 字段。
- 类型一致性：`MethodKind`/`parse_method_kinds`/`METHOD_PROFILE_REGISTRY`/`COMPOSITION_MATRIX`/`METHOD_MACHINE_REGISTRY`/`MethodStoreProtocol`/`MethodBinding` 在各任务 Interfaces 块一次定义、后续引用不造新名。
- 比例：15 任务对 16 处文件改动，每任务 5 步 TDD；服务实现细节全部留给实现者，计划只钉签名/规则出处/测试名。
