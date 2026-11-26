# Context Collab L4 Collet 对齐 ACCS 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `l4_context_primitives/collet.py` 的 Collet 聚合根对齐 ACCS:`collet_id`/`initiator_ref`(必填)/`version`/`scope`(作用域)/独立 `state`(Context 生命周期),快照与创建参数出聚合根;只做领域模型层,不动 DDL/wire。

**Architecture:** 沿 context_collab 既有缝隙——collet.py 纯值对象 → G4 声明式状态机(新 `COLLET_MACHINE_REGISTRY`,照 L5 `validate_method_machine` 模式)→ G1 结构授权(spec 显式传入,授权语义零变化)→ services 编排 → 仓储 record 分载(record 伴随字段承接移出聚合的 `context_spec`/`current_snapshot`)→ 生命周期状态进程内 store(仿 L5 `InMemoryMethodStore`,重启归零,P2 换持久化)。

**Tech Stack:** Python 3.12、python-injector DI、SQLAlchemy(stop 不动)、pytest(SQLite 真仓储既有测试保零回归)。

**Spec:** `apps/backend/specs/2026-11-12-context-collab-l4-collet-accs-alignment/spec.md`(下称 spec;字段裁决、方案 b/i、G4/守卫/dict 序列化细节以 spec 为准,本计划不重复)。

## Global Constraints

- 分支 `dev_bbs_help_1019_3` 直接叠加 HEAD;不动 L5 method-objects 语义、不动 `service_api.py` DTO、不动 HTTP adapter、不改表/列/迁移。
- 测试命令:`cd apps/backend && DEPLOY_PROFILE=test uv run pytest tests/community/core/task/context_collab/ -v`(用户指示:CI 门禁与全量套件不跑;触碰 DI/endpoint 时补跑 `tests/ .../test_task_module.py`、`tests/community/endpoints/test_context_collab*.py`)。
- 每次提交 `feat(backend): <outcome>`(测试相应 `test(backend)`,文登记 `docs(backend)`),末尾追加 `Co-Authored-By: Claude <noreply@anthropic.com>`。
- 任一文件 ≤1000 行;不格式化未改动行。
- 异常只用既有 `ContextCollabValidationError` / `ContextCollabNotFoundError`。
- `collet.*` 迁移 token 不进 `g4_contracts_consistency/schema.py` 的 `REGISTERED_EVENT_KINDS`。

## Review Focus

1. **NULL 发起者行回退**:方案 i 要求解码响亮失败——不得为图省事让 `initiator_ref` 传回 `None` 或虚构占位。→ Task 2 Step 5 `test_null_initiator_row_decode_raises`。
2. **snapshotless 行零回归**:spec 裁决"`current_snapshot` NULL 容忍保留"——既有 `test_snapshotless_collet_gets_null_snapshot_but_full_progress` / `test_append_onto_snapshotless_collet_keeps_null_snapshot` 必须原样通过。→ Task 2 Step 8。
3. **领域名↔列名映射写反(读写两个方向)**:`collet_id`↔`collab_context_id` 列、`version`↔`context_version` 列、`initiator_ref`↔`actor_ref` 列;round-trip 必须双向钉死。→ Task 2 Step 1 `test_round_trip_preserves_field_names`。
4. **G4 治理类别漂移**:`COMPLETED` 误归 terminal 系会导致 `archive` 出边被纪律拒绝、注册即炸。→ Task 1 Step 1 `test_completed_is_not_terminal_and_keeps_archive`。
5. **G1 漏传 spec 后权限判定读错源**:`_is_principal` 改经显式 spec 后,不得改读 `current_snapshot`(可空),陌生 actor 仍拒。→ Task 2 Step 8 既有 `test_context_collab_service.py` / `test_method_authorization.py` 断言不打折。

---

### Task 1: ColletState + `collet_lifecycle` G4 机器

**Files:**
- Modify: `src/agentclaw/community/core/task/context_collab/l4_context_primitives/collet.py`(加 `ColletState` 枚举)
- Create: `src/agentclaw/community/core/task/context_collab/g4_contracts_consistency/context_state_machines.py`
- Test: `tests/community/core/task/context_collab/test_context_g4_conformance.py`

**Interfaces:**
- Produces: `ColletState(str, Enum)` 值 = `"CREATED"/"ACTIVE"/"SUSPENDED"/"COMPLETED"/"ARCHIVED"`(大写机器词汇);`COLLET_EVENT_KINDS: frozenset[str]` = `{"collet.created","collet.activated","collet.suspended","collet.resumed","collet.completed","collet.archived"}`(词汇闭合;`collet.created` 仅注册不配规则);`COLLET_STATE_GOVERNANCE`(waiting→CREATED,active→ACTIVE,blocked→SUSPENDED,review→COMPLETED,terminal_success→ARCHIVED);`COLLET_MACHINE_REGISTRY: dict[str, StateMachineDefinition]` 键 `"collet_lifecycle"`;`validate_collet_machine(machine)`(照 `method_state_machines.validate_method_machine` 模式:状态词=ColletState 闭合、事件词=COLLET_EVENT_KINDS、guard_refs 一律空、复用 `_check_str`/`_validate_header`/`_validate_reachability`)。
- Consumes: `g4_contracts_consistency/state_machine.py` 的 `StateMachineDefinition`/`StateDefinition`/`TransitionRule`/`resolve_transition` 与三个校验原语。
- 迁移规则(逐条钉死,spec §4):

```text
CREATED   --collet.activated-->  ACTIVE
ACTIVE    --collet.suspended-->  SUSPENDED
SUSPENDED --collet.resumed-->    ACTIVE
ACTIVE|SUSPENDED --collet.completed--> COMPLETED
COMPLETED --collet.archived-->   ARCHIVED
```

- [ ] **Step 1: 写失败测试**——`test_all_collet_machines_pass_validation`(注册期 `validate_collet_machine`)、`test_lifecycle_state_vocabulary`、`test_completed_is_not_terminal_and_keeps_archive`(`COMPLETED` 治理类别非 terminal 且保有 `collet.archived` 出边;`ARCHIVED` 有 `collet.archived` 入边且无出边)、`test_created_is_entry_and_all_states_reachable`(`CREATED` 归 waiting;直接用该状态值传递可达性豁免语义)、`test_collet_tokens_not_in_event_schema`(`COLLET_EVENT_KINDS` 与 `schema.REGISTERED_EVENT_KINDS` 不相交)。

- [ ] **Step 2: 跑失败** — `DEPLOY_PROFILE=test uv run pytest tests/community/core/task/context_collab/test_context_g4_conformance.py -v`,预期 FAIL(模块不存在)。
- [ ] **Step 3: 实现**——`collet.py` 加 `ColletState`;新机器模块加载期逐台 `validate_collet_machine`(快速失败)。
- [ ] **Step 4: 跑通过**,并跑 `.../test_state_machine.py .../test_method_g4_conformance.py -v` 确认既有 G4 零回归。
- [ ] **Step 5: Commit** `feat(backend): add ColletContext lifecycle state machine to G4`

### Task 2: 聚合根重构 + record 分载 + 严格解码(原子)

> 聚合根构造签名一变,仓储/服务/测试工厂全线告警——本任务一原子提交内完成全部适应,期间既有套件红属预期,Step 8 收口全绿。值对象类型 `ContextSpec`/`CurrentSnapshot`/`ProgressSnapshot` 的字段、keys、校验**全部不变**,只是不再挂在 Collet 上。

**Files:**
- Modify: `src/agentclaw/community/core/task/context_collab/l4_context_primitives/collet.py`(Collet 瘦身六字段 + `from_dict`/`to_dict`;模块 docstring 更新"快照/spec 是 record 伴随投影")
- Modify: `src/agentclaw/community/core/task/context_collab/persistence/repository.py`(`_collet_from_row` 严格解码、decode/create 两条路径构造六字段 Collet、record 加伴随字段、`_collet_row` 签名补 `spec`/`snapshot` 入参)
- Modify: `src/agentclaw/community/core/task/context_collab/g1_secure_govern/authorization.py`(协议与方法签名加 `spec: ContextSpec`,helpers 改读 spec)
- Modify: `src/agentclaw/community/core/task/context_collab/services/context_collab_service.py`、`services/method_lifecycle_service.py`、`services/single_group_method_service.py`、`services/open_method_service_base.py`、`services/swarm_method_service_base.py`(字段载体迁移)
- Modify(测试工厂/桩适配): `tests/community/core/task/context_collab/` 下 `test_l4_primitives.py`、`test_open_profile.py`、`test_group_profile.py`、`test_single_profile.py`、`test_swarm_profile.py`、`test_method_lifecycle_service.py`、`test_context_collab_service.py`、`test_context_collab_append.py`、`test_context_collab_status.py`、`test_service_real_repository.py`
- Test(新建): `tests/community/core/task/context_collab/test_collet_aggregate.py`

**Interfaces:**
- Produces(Task 3/4 依赖,签名逐字):

```python
@dataclass
class Collet:
    collet_id: str
    initiator_ref: PrincipalRef
    state: ColletState
    scope: SpaceRef
    current_event_id: str | None
    version: int
    # from_dict(data) -> Collet / to_dict() -> dict
```

  `from_dict` 校验:`collet_id` 非空、`initiator_ref`/`scope`/`state`/`version` 必填、`state` 命中 `ColletState` 值、`version` 为 ≥1 整数、未知顶层键拒绝(键集 = 六字段名)。
- Produces(仓储): `_collet_from_row(row) -> tuple[Collet, ContextSpec, CurrentSnapshot | None]`(严格解码:`actor_ref` 列 NULL 抛 `ContextCollabValidationError`,消息含 `collab_context_id`;`current_snapshot` NULL 保留;`scope` = spec JSON 的 space;`state` = `ColletState.ACTIVE` 种子,P2 换列读取)。Record 增伴随字段:`ContextCreatedRecord(collet, context_spec, current_snapshot, initial_event)`、`ContextStatusRecord(collet, context_spec, current_snapshot, events)`、`EventAppendedRecord(event, collet, context_spec, current_snapshot)`。
- Produces(G1 接缝): `ContextCollabAuthorizerProtocol.check_append(collet: Collet, spec: ContextSpec, command, actor)`、`filter_visible_events(collet: Collet, spec: ContextSpec, events, actor)`;`_is_principal(spec, actor)`、`_is_executor(spec, actor)`、`_is_public_open_message(spec, command)`。`check_create` 不变;授权规则、错误消息语义零变化(消息里 `collet.collab_context_id!r` → `collet.collet_id!r`)。
- Consumes: Task 1 `ColletState`。
- 服务适配点(现在唯一权威清单):`context_collab_service.py` 的 `_resolve_event_status(collet, spec, snapshot, command, context)`、create/append/get 三处的 `record.collet.collab_context_id`→`collet.collet_id`、`record.collet.context_version`→`collet.version`、`record.collet.context_spec`/`record.collet.current_snapshot`→record 伴随字段;`method_lifecycle_service.py:342,423,464`、`single_group_method_service.py:274`、`open_method_service_base.py:118`、`swarm_method_service_base.py:99` 的 `record.collet.context_spec` → `record.context_spec`。

- [ ] **Step 1: 写失败测试** `test_collet_aggregate.py`:`test_round_trip_preserves_field_names`(六字段名/`state` 大写值)、`test_initiator_and_scope_and_state_required`(缺失/非法抛 `ContextCollabValidationError`)、`test_version_must_be_positive_int`、`test_unknown_top_level_keys_rejected`。

- [ ] **Step 2: 跑失败(签名未变)**
- [ ] **Step 3: 实现 collet.py**——六字段 + `from_dict`/`to_dict`;`ContextSpec`/`CurrentSnapshot`/`ProgressSnapshot` 原样保留;跑 Step 1 通过。
- [ ] **Step 4: 仓储适配**——decode/create 双路径构造六字段 Collet;`_collet_row(collet, spec, snapshot, now)` 签名扩展;record 伴随字段。
- [ ] **Step 5: 写严格解码测试**(追加到 `test_collet_aggregate.py`):`test_null_initiator_row_decode_raises`(裸 SQL 插 `actor_ref=NULL` 行 → `get_current_status` 抛 `ContextCollabValidationError`,不返回成功)。
- [ ] **Step 6: G1 + services 适配**——接上述 Interfaces 清单逐点改;`test_context_collab_service.py` 桩授权器签名同步。
- [ ] **Step 7: 测试工厂适配**——10 个 Modify 文件里的 `Collet(...)` 构造与新 record 形态对齐(六字段 + spec/snapshot 伴随传参);确认 `test_snapshotless_*` 两用例断言不动。
- [ ] **Step 8: 全目录回归** — `DEPLOY_PROFILE=test uv run pytest tests/community/core/task/context_collab/ -v` 全绿;补跑 `tests/community/endpoints/test_context_collab_endpoints.py tests/community/endpoints/test_context_collab_methods_endpoints.py tests/community/di/test_task_module.py -v` 零回归。
- [ ] **Step 9: Commit** `feat(backend): align Collet aggregate to ACCS domain boundary`

### Task 3: InMemoryColletLifecycleStore + 生命周期五操作 + DI

**Files:**
- Create: `src/agentclaw/community/core/task/context_collab/persistence/collet_lifecycle_store.py`
- Create: `src/agentclaw/community/core/task/context_collab/services/collet_lifecycle_service.py`
- Modify: `src/agentclaw/community/di/modules/task_module.py`
- Test: `tests/community/core/task/context_collab/test_collet_lifecycle.py`、`tests/community/di/test_task_module.py`(追加单例解析用例)

**Interfaces:**
- Produces:
  - `ColletLifecycleStore:` 整 `threading.Lock`,`dict[collet_id, ColletState]`;`get(collet_id: str) -> ColletState | None`、`set(collet_id: str, state: ColletState) -> None`、`get_or_seed(collet_id: str) -> ColletState`(未见即写入并返回 ACTIVE);P1 `InMemoryColletLifecycleStore` 进程单例(P2 换列实现)。
  - `ColletLifecycleResult` dataclass:`collet_id: str`、`old_state: ColletState`、`new_state: ColletState`、`initiator_id: str`、`reason: str | None`。
  - `ColletLifecycleService(collet_lifecycle_store, collab_repo)`,方法签名统一起头 `(collet_id: str, context: ActorContext, reason: str | None = None) -> ColletLifecycleResult`,五方法:`activate`/`suspend`/`resume`/`complete`/`archive`(token 逐字 = `collet.activated`/`collet.suspended`/`collet.resumed`/`collet.completed`/`collet.archived`,经 Task 1 机器 `resolve_transition` 裁决)。流程:`get_current_status` 为 None → `ContextCollabNotFoundError` → 守卫(actor = `collet.initiator_ref.principal_id` 本人或 `spec.executor_ref.agent_id`,沿 `_is_executor` 先例字符串相等;陌生 actor `ContextCollabValidationError`)→ `get_or_seed` 取现态 → `resolve_transition` → `set` 新态 → 返回结果。不落事件、不写库(repo 仅读用)。
- Consumes: Task 1 机器、Task 2 record 伴随字段。

- [ ] **Step 1: 写失败测试** `test_collet_lifecycle.py`——五操作正反态(activate 幂等拒绝、suspend→resume 回 ACTIVE、complete 后 activate 拒、archive 后 complete 拒终态防线)、陌生 actor 拒、本人/executor 放行、`test_unseeded_collet_defaults_active`(焊死种子语义)、`test_fresh_store_instance_loses_state`(新 store 实例同 collet 回 ACTIVE,焊"重启归零")、collet 不存在 `ContextCollabNotFoundError`。

- [ ] **Step 2: 跑失败 → Step 3: 实现 store + service → Step 4: 跑通过**
- [ ] **Step 5: DI 绑定**——`task_module.py` 按 Context collab 段既有模式:`ColletLifecycleStore → InMemoryColletLifecycleStore` singleton、`ColletLifecycleService → ColletLifecycleService` singleton;`test_task_module.py` 追加两用例后跑 `DEPLOY_PROFILE=test uv run pytest tests/community/di/test_task_module.py -v`。
- [ ] **Step 6: Commit** `feat(backend): add in-process ColletContext lifecycle operations`

### Task 4: spec 登记与收尾回归

**Files:**
- Modify: `apps/backend/specs/README.md`(索引一行)
- Test: 全目录回归

- [ ] **Step 1:** README 索引加 `2026-11-12-context-collab-l4-collet-accs-alignment/`(按字母序插入)。
- [ ] **Step 2:** `DEPLOY_PROFILE=test uv run pytest tests/community/core/task/context_collab/ tests/community/di/test_task_module.py -v` 整段全绿(预期 735+ 增量)。
- [ ] **Step 3: Commit** `docs(backend): register L4 Collet ACCS alignment spec`

---

## Self-Review 结论

- Spec 覆盖:§3 字段表→T2;§4 机器→T1;§5.1→T2;§5.2 伴随字段→T2;§5.3 五操作/store/守卫→T3;§6.4 方案 i→T2 Step 5;§6.5/6 序列化/边界→T2;§7 测试表→T1–T3 测试文件一一对应;§8 八条裁决全数入任务(1/5/8→T1,4→T2,3/7→T3,2/6→约束与 T1)。
- 类型一致:`ColletState`/`COLLET_MACHINE_REGISTRY`/`get_or_seed`/record 伴随字段名(`context_spec`/`current_snapshot`)在产生任务与消费任务间逐字一致。
- 比例:4 任务对约 20 处文件改动;T2 为原子重构、其余任务均为 5–9 步 TDD,服务实现细节留给实现者。
