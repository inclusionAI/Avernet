# Context Collab 任务代办产品层 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现第三个产品"任务代办"——用户发布带目标的代办任务、指定 bot 为辅助执行 Agent（顶层单值 `MethodKind.OPEN`，P1 闭协作），任务推进经专用 G4 `task_commission_goal` 机器闭环到验收/取消；模块级 service 接口（HTTP 后置）。

**Architecture:** 平台三件先行（L6 profile 值 + G4 新机器 + G1 白名单加 Open + 状态裁决纯函数提升进 G4）→ products/task_commission 自持编排（发布 + Work 全族六动作，产品时序门 ★ + 提交权 bot-only ★K）→ 读投影 + DI + api 再导出 → 收尾。乙案依赖矩阵与架构测试原样执法，零 service_api。

**Tech Stack:** Python 3.12、FastAPI（不触，HTTP 后置）、SQLAlchemy（SQLite 测试）、python-injector DI、pytest。

**Spec:** `apps/backend/specs/2026-11-13-context-collab-task-commission-product/spec.md`（Accepted；§2 域映射、§3 机器、§4 协议扩张、§5.1 签名、裁决 A–L 为权威，本计划不重复）。

## Global Constraints

- 分支 `dev_bbs_help_1019_3` 直接叠加 HEAD。
- 乙案依赖矩阵不变：products/ import 白名单由既有架构测试执法（`g4_contracts_consistency` 已在白名单内，新 `workflow_resolution` 模块免修执法件）。
- 不动两张协议表/列/迁移/三个既有 wire；`bbs_help_request` 形态零改动。
- 测试命令：`cd apps/backend && DEPLOY_PROFILE=test uv run pytest tests/community/core/task/context_collab/ tests/community/repository/task/context_collab/ tests/community/di/test_task_module.py -v`（全量套件与 CI 门禁不跑——用户指示）。
- 提交 `<type>(backend): <outcome>`，尾行 `Co-Authored-By: Claude <noreply@anthropic.com>`；任一文件 ≤1000 行；不格式化未改动行；不新增依赖。
- 异常只用既有三类错误；`T | None` 仅 `note`/孤儿 `body` 等合法域可空。
- 词汇常量从源头 import（`MethodKind.OPEN.value`、`ProductProfile.TASK_COMMISSION.value`、`WorkflowKind.TASK_COMMISSION_GOAL.value`、`VISIBILITY_PUBLIC`）；六 task.* 事件名逐字。

## Review Focus

1. **终态后推进**：done/cancelled 上任何动作必须被产品时序门拒（裁决 J；G4 通用合同是"无规则命中=接纳"，不设门则事件静默落库）→ T2 `test_advance_rejected_after_terminal`。
2. **越时序推进**：created 上 submit、reviewing 上 start——时序门逐档拒。→ T2 `test_advance_rejected_out_of_order`。
3. **发起人越权 submit**：裁决 K 仅指定 bot（executor 值匹配）；发起人调 submit 必须拒。→ T2 `test_submit_rejects_principal`。
4. **无 artifacts 提交**：守卫 FAIL 必须以 ValidationError 冒出而非静默。→ T2 `test_submit_requires_artifacts`。
5. **状态推进非恒等**：六动作后快照 status 按机器派写（与公开讨论的恒等 J 相反）。→ T2 `test_full_lifecycle_walks_machine`（created→in_progress→reviewing→done 全链 + reject 回环 + cancel）。
6. **白名单扩张不松旧禁**：裸 "Open" 顶层放行后 "Group/Swarm" 仍拒；`bbs_public_discussion` 行为零回归。→ T1 `test_create_allows_bare_open_rejects_group_swarm`、目录回归。

---

### Task 1: 平台三件（profile + 机器 + G1 白名单 + 状态裁决提升）

**Files:**
- Modify: `src/.../context_collab/l6_products/profiles.py`（`ProductProfile.TASK_COMMISSION = "task_commission"`、`WorkflowKind.TASK_COMMISSION_GOAL = "task_commission_goal"`）
- Modify: `src/.../context_collab/g4_contracts_consistency/state_machine.py`（新机器 + 注册表三键）
- Create: `src/.../context_collab/g4_contracts_consistency/workflow_resolution.py`（提升函数）
- Modify: `src/.../context_collab/g1_secure_govern/authorization.py:36`（`TOP_LEVEL_METHOD_KINDS` 加 `"Open"`）
- Modify: `src/.../context_collab/services/context_collab_service.py`（`_resolve_event_status` 改为提升函数薄包装，行为不变）
- Test: `tests/.../test_state_machine.py`（**改写** `test_builtin_machine_pinned` + 新机 conformance）、`tests/.../test_method_authorization.py` 或 G1 对应、`tests/.../test_context_collab_service.py`（**改写** 裸 Open 拒绝清单）

**Interfaces:**
- Produces（T2 消费，签名逐字）：

```python
# workflow_resolution.py(g4_contracts_consistency)
def resolve_workflow_status(
    *, workflow_kind: str | None,
    executor_ref,                       # AgentRef | None(创建参数)
    snapshot,                           # CurrentSnapshot | None(可空外部行)
    event_kind: str,
    payload: dict,
    actor,                              # PrincipalRef(G1 ActorContext.actor)
) -> str | None
# 语义与 services._resolve_event_status 逐字等价:机器命中→守卫求值→resolve_transition;
# workflow_kind 未命中/快照 None/无规则命中→原 status 或 None(不变即 None);

TASK_COMMISSION_PRECONDITIONS: dict[str, set[str]]   # T1 机器旁 T2 消费,六动作前置状态集
# {"task.started": {"created"}, "task.progress.updated": {"in_progress"},
#  "task.submitted": {"in_progress"}, "task.accepted": {"reviewing"},
#  "task.rejected": {"reviewing"}, "task.cancelled": {"created","in_progress","reviewing","blocked"}}
```
- Consumes: `GUARD_REGISTRY`/`GuardEvaluationInput`/`resolve_transition`/`MACHINE_REGISTRY`（G4 既有）。
- 机器六迁移与守卫词逐字抄 `BBS_HELP_REQUEST_GOAL_MACHINE` 形状；`allowed_methods=("Open",)` 六态一致。

- [ ] **Step 1: 失败测试**——`test_state_machine.py`：改写 `test_builtin_machine_pinned`（三键）+ `test_task_commission_goal_conformance`（注册期校验、allowed_methods=("Open",)、六迁移形状与 guard_refs 逐字）；G1：`test_create_allows_bare_open_rejects_group_swarm`（Open 放行、Group/Swarm 仍拒）；改写 `test_create_rejects_bare_group_open_swarm_top_level` 的拒绝清单；`workflow_resolution`：`test_resolve_workflow_status_promotes_team_todo`（用既有 team_todo 机器+快照挪 `test_context_collab_append.py` 既有断言同款例子，钉提升函数与原语义逐字等价，此测试也可直接搬原 private 函数用例）。
- [ ] **Step 2: 跑失败** → **Step 3: 实现**（profile 值、机器+注册提升表、G1 frozenset 加项、`workflow_resolution.py`、service 薄包装改造）→ **Step 4: 全绿**（含 `test_context_collab_append.py` 状态推进套零回归——提升函数等价性的最强证据）。
- [ ] **Step 5: Commit** `feat(backend): allow top-level Open and add task_commission workflow to platform`

### Task 2: products/task_commission 发布 + Work 六动作

**Files:**
- Create: `src/.../context_collab/products/task_commission/__init__.py`、`.../task_commission/service_api.py`、`.../task_commission/service.py`
- Test: `tests/.../products/test_task_commission_publish.py`（新）、`tests/.../products/test_task_commission_work.py`（新）

**Interfaces:**
- Produces（T3 消费；spec §5.1 签名逐字）：
  - `TaskCommissionServiceProtocol`（`publish_task/start_task/report_progress/submit_task/accept_task/reject_task/cancel_task/detail_task/list_tasks` 九法；读两法 T3 落，本任务 `NotImplementedError` 占位）
  - `PublishTaskCommand(title, body, space, assignee_agent_id, resource_refs=…)`、`TaskAdvanceCommand(collab_context_id, note=None, artifacts=[])`、`TaskCommissionCreatedView(collet_id, title, body_event_id, status, context_version)`、`TaskAdvancedView(collab_context_id, event_id, status, context_version)`
  - 编排差异（同公开讨论骨架上的三处）：`method_kind=MethodKind.OPEN.value`、`executor_ref=AgentRef("bot", command.assignee_agent_id)`、`workflow_kind=WorkflowKind.TASK_COMMISSION_GOAL.value`；
  - 六动作统一编排管（spec §5.2）：`get_current_status` → 404/外 profile 拦截 → **时序门 `TASK_COMMISSION_PRECONDITIONS[event_kind]`** → **提交权门**（仅 `task.submitted`：actor 为 bot 型且 `principal_id==executor.agent_id`）→ validate → G1 `check_append`（`_TaskControlIntent` duck-type）→ append（primitive=Task、public、`snapshot_updater=replace(status, resolve 结果)`;结果 None 时用原 status）；
- Consumes: T1 `TASK_COMMISSION_PRECONDITIONS`/`resolve_workflow_status`、既有 `creation_payload`/`project_created_context`/仓储五法。

- [ ] **Step 1: 失败测试**（fake 沿 write 测试先例扩展支持 Task 控制事件与状态推进时序）：publish 用例——`test_publish_wires_open_commission`（method=Open、executor=assignee、六键、首帖正文、L5 单值 binding、视图五字段）、`test_publish_rejects_blank_fields`；work 用例——`test_full_lifecycle_walks_machine`（principal 发布→bot start→bot progress→bot submit(artifacts)→principal accept=done,每步断言 status 派写与 context_version 递增）、`test_reject_loop_returns_to_in_progress`、`test_cancel_from_created`、`test_start_blocks_stranger_actor`（G1）、`test_submit_requires_artifacts`、`test_submit_rejects_principal`、`test_advance_rejected_out_of_order`（created 上 submit）、`test_advance_rejected_after_terminal`（done/cancelled 上全部六动作）、`test_unknown_or_foreign_profile_not_found`、`test_version_conflict_propagates`。
- [ ] **Step 2: 跑失败** → **Step 3: 实现三文件** → **Step 4: 跑绿** + products 目录全绿 + 全目录回归。
- [ ] **Step 5: Commit** `feat(backend): add task commission publish and work use cases`

### Task 3: 读投影 + DI + api 再导出 + 收尾

**Files:**
- Create: `.../products/task_commission/views.py`、`src/agentclaw/community/api/task/task_commission_service.py`
- Modify: `.../products/task_commission/service.py`（两读法）、`src/agentclaw/community/di/modules/task_module.py`（绑定）
- Test: `tests/.../products/test_task_commission_read.py`（新）、`tests/community/di/test_task_module.py`（追加）

**Interfaces:**
- Produces：

```python
# views.py
@dataclass(frozen=True)
class TaskCommissionSummaryView:
    collet_id: str; title: str; initiator_id: str; message_count: int; created_at: datetime

@dataclass(frozen=True)
class TaskCommissionDetailView:
    collet_id: str; title: str; status: str            # 快照当前任务状态(机器权威)
    body: DiscussionDetailBody | None                  # 沿用 bbs views 先例(孤儿帖 None)
    events: list[dict]                                  # 公开事件流投影(event_id/event_kind/primitive/actor_id/occurred_at),含 Task 控制事件
```
- `list_tasks`=`list_collets_by_product("task_commission")`+`count_message_events`；`detail_task` 标题读源新主/回退沿公开讨论先例。
- DI／api 再导出沿 `BbsPublicDiscussionService` 既有两处先例（绑定 + `api/task/task_commission_service.py`）。

- [ ] **Step 1: 失败测试**：`test_list_tasks_filters_by_profile_and_orders`、`test_detail_task_shows_status_and_history`（含控制事件入投影、标题读源）、`test_detail_rejects_foreign_profile`、DI singleton + 共享注入、api 再导出冒烟。
- [ ] **Step 2: 跑失败** → **Step 3: 实现** → **Step 4: 全量收尾回归**（三目录 + 产品目录）。
- [ ] **Step 5: Commit** `feat(backend): add task commission read cases and module-level delivery`
- [ ] **Step 6: Commit** `docs(backend): register task commission product spec`（README 索引 + spec/plan 目录入提交）

---

## Self-Review 结论

- Spec 覆盖：§2 域映射→T2；§2a 操作族映射为 T2 编排逐条(发布=propose/assign、六动作=Work 族、读=Query)；§3 机器→T1；§4 两处合同扩张→T1；§5.2 编排与读投影→T2/T3；§6 裁决 A–L 落点——A/B/C/D 作者三票入设计前提、E(artifacts)→T2 测试、F(public)→T2 编排、G(提升)→T1、H(守卫零新词)→T1 机器、I(note)→T2 提交、J(时序门)→T1 产表+T2 消费、K(bot-only submit)→T2、L(命名/测试改写)→T1/T3；§7 测试表 1:1 落 T1–T3。
- 类型一致：`TASK_COMMISSION_PRECONDITIONS`/`resolve_workflow_status`（T1 产 T2 消）、`TaskCommissionServiceProtocol`/视图（T2/T3 产，T3 DI/api 消）签名逐字。
- Review Focus 6 项全部有归属测试（终态/时序/越权/无 artifacts/非恒等/白名单不松旧禁→T1/T2）。
- 比例：3 任务，签名与断言为主、零方法体转写；T1 行为不变以 append 套件回归为证。
