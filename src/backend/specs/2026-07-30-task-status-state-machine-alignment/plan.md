# 任务状态机(对齐产品设计)— 实施计划(plan.md)

> 负责人:栖真。spec:`spec.md`(同目录)。本文件 = HOW:落点、文件、API、算法、测试、skill/副屏的具体改法。
> 落点域:ocb backend 任务内核(开源,代码在 Avernet `src/backend/src/agentclaw/community/core/task/`,用户手动同步到 ocb 的 `ocb-public` submodule)。
> 日期:2026-07-30。

---

## 0. 决策(开放问题收敛)

**开放问题 1(FAILED 触发先后)** 的答案:**先恢复,恢复无望才 FAILED**。

- reroute/split 恢复优先:FAILED 节点先尝试 reroute(换执行者)/ split(拆子节点),走既有 `LOOP_REROUTED → RUNNING` 回路。
- 只有当 FAILED 节点**不可恢复**时,任务才升 `FAILED`。"不可恢复" = `compute_gap` 新字段 `unrecoverable_failed` 非空,命中以下任一:
  - **(a) 原子终止** —— `recompose_count >= MAX_RECOMPOSE` 且仍有 FAILED 节点(无 split 余地)。
  - **(b) 节点升级** —— 该 FAILED 节点 `attempted_executors` 去重执行者数 `>= DEFAULT_MAX_ATTEMPTS` 且无 reroute 候选(或 atomic 下无 split 余地)。
- R4 (a)/(b) 两种触发**统一汇入 `unrecoverable_failed` 集合**;`FAILED` 是该集合非空时的终态动作,不再区分两条来源路径。

**开放问题 2(是否保留 DISCUSSING)** 的答案:**删除**(spec 已定)。`DRAFTING` 单态覆盖要素补全期,amend 不切状态(spec R2)。

---

## 1. 枚举改动(`core/task/domain/models.py`)

### 1.1 `TaskStatus`(8 态 → 7 态)

```python
class TaskStatus(StrEnum):
    DRAFTING = "drafting"      # was INTAKE  + DISCUSSING 合并
    DEFINED = "defined"        # was PLANNED
    EXECUTING = "executing"    # 同名
    REVIEWING = "reviewing"    # was VALIDATING
    DONE = "done"              # was DELIVERED
    CANCELLED = "cancelled"    # 同名
    FAILED = "failed"          # 新增终态(was HUNG 的不可恢复语义)
```

映射(老 → 新,纯重命名/合并,无历史兼容):

| 老 | 新 |
|---|---|
| `INTAKE` | `DRAFTING` |
| `DISCUSSING` | `DRAFTING`(合并,amend 不再切态) |
| `PLANNED` | `DEFINED` |
| `EXECUTING` | `EXECUTING` |
| `VALIDATING` | `REVIEWING` |
| `DELIVERED` | `DONE` |
| `CANCELLED` | `CANCELLED` |
| `HUNG` | 删除(语义拆分:节点级 `HUMAN_REQUIRED` 承载"被 hung";任务级不可恢复 → `FAILED`) |

### 1.2 `NodeStatus`(7 态 → 6 态,删 `PARTIAL_FAILED`)

```python
class NodeStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"              # 验收不通过 + 执行失败合一
    SKIPPED = "skipped"
    HUMAN_REQUIRED = "human_required"
```

- `PARTIAL_FAILED` 删除。验收不通过(`NODE_REJECTED`)与执行失败(`NODE_FAILED`)统一置 `FAILED`,区分下沉到节点属性 `acceptance_result`(`pass`/`fail`)与 failure kind(已有 `attempted_executors[-1].outcome`)(spec R9)。

### 1.3 `GraphStatus`

`root_phase` 字段类型仍是 `TaskStatus`,自动跟随新枚举。`GraphStatus` 本身(`ON_PLAZA` / `AWAITING_HUMAN_ACCEPT` / `VERIFIED`)不依赖 TaskStatus,**不动**。

---

## 2. 状态机迁移表(`core/task/domain/state_machine.py`)

### 2.1 任务级迁移表(整表替换)

```python
TERMINAL_TASK_STATUSES: frozenset[TaskStatus] = frozenset(
    {TaskStatus.DONE, TaskStatus.CANCELLED, TaskStatus.FAILED}
)

_BASE_TASK_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.DRAFTING: frozenset({TaskStatus.DEFINED}),
    TaskStatus.DEFINED: frozenset({TaskStatus.EXECUTING}),
    TaskStatus.EXECUTING: frozenset({TaskStatus.REVIEWING, TaskStatus.EXECUTING, TaskStatus.FAILED}),
    TaskStatus.REVIEWING: frozenset({TaskStatus.DONE, TaskStatus.EXECUTING}),
    TaskStatus.DONE: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
    TaskStatus.FAILED: frozenset(),
}

# 任意非终态可 → CANCELLED(去掉 HUNG — 任务级不再有 HUNG 出边)
TASK_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    src: outgoing | {TaskStatus.CANCELLED}
    if src not in TERMINAL_TASK_STATUSES
    else outgoing
    for src, outgoing in _BASE_TASK_TRANSITIONS.items()
}
```

要点:
- `DRAFTING` 出边只有 `DEFINED`(amend 不切态,故无 `DRAFTING→DRAFTING` 需求;自环无意义)。
- `EXECUTING → FAILED` 新增(spec §2.2 R4 终态)。
- `REVIEWING → EXECUTING` 返工边保留(spec R6)。
- `HUNG` 相关全删;`CANCELLED` 仍是任意非终态可达。

### 2.2 节点级迁移表(删 `PARTIAL_FAILED`)

```python
NODE_TRANSITIONS: dict[NodeStatus, frozenset[NodeStatus]] = {
    NodeStatus.PENDING: frozenset({NodeStatus.RUNNING, NodeStatus.SKIPPED}),
    NodeStatus.RUNNING: frozenset({NodeStatus.DONE, NodeStatus.FAILED, NodeStatus.HUMAN_REQUIRED}),
    NodeStatus.FAILED: frozenset({NodeStatus.RUNNING, NodeStatus.DONE}),  # 重试 / 验收通过兜底
    NodeStatus.HUMAN_REQUIRED: frozenset({NodeStatus.RUNNING}),
    NodeStatus.DONE: frozenset({NodeStatus.DONE}),
    NodeStatus.SKIPPED: frozenset(),
}
```

- 删 `PARTIAL_FAILED` 行;`RUNNING` 出边去掉 `PARTIAL_FAILED`,保留 `DONE/FAILED/HUMAN_REQUIRED`(spec R8/R11)。
- `FAILED → DONE` 保留(验收通过兜底,spec R11)。

---

## 3. `TaskService`(`core/task/services/task_service.py`)

按 `grep` 命中点逐一改(13 处 `TaskStatus.*` + `PARTIAL_FAILED` 2 处):

### 3.1 `create` / 初始化图谱(L136, L468)

`TaskExecutionGraph(root_phase=TaskStatus.INTAKE)` → `TaskStatus.DRAFTING`(两处:`create` 和 `_apply_event` 的 `TASK_CREATED` 分支)。

### 3.2 `amend` + `_apply_event` SPEC_AMENDED 分支(L174-176, L474-475)

**删掉 amend 触发的状态切换**。新模型 amend 不切态(spec R2):任务留 `DRAFTING` 直到 `finalize_plan`。

```python
# amend (L168-178):去掉 if task.status == INTAKE: _advance_phase(DISCUSSING)
def amend(self, task_id, patch):
    task = self._load(task_id)
    if task is None:
        return None
    self._apply_spec_patch(task, patch)
    self._emit(task, EventKind.SPEC_AMENDED, patch=patch)
    self._task_repo.save(task)
    return self._task_repo.get_by_id(task_id)

# _apply_event SPEC_AMENDED (L473-476):去掉 INTAKE→DISCUSSING 推进
if kind == EventKind.SPEC_AMENDED:
    self._apply_spec_patch(task, payload.get("patch") or {})
    return  # 不再切态;任务留 DRAFTING
```

### 3.3 `finalize_plan`(L181-196)

合法源态 `{DISCUSSING, PLANNED}` → `{DRAFTING}`(`DRAFTING` 是唯一的计划冻结源态)。

```python
if task.status not in {TaskStatus.DRAFTING}:
    raise Illegal_state(... )
self._advance_phase(task, TaskStatus.DEFINED)  # was PLANNED
```

> 注:`finalize_plan` 允许重入(已 `DEFINED` 再冻结)是否需要?spec 未要求;保持**只允许 `DRAFTING` 源态**,重入报错。

### 3.4 `cancel`(L210-214)

`_advance_phase(task, TaskStatus.CANCELLED)` —— `CANCELLED` 同名,不需改;守卫表已允许任意非终态 → `CANCELLED`。

### 3.5 `_apply_event` 节点事件(L521-545 区段)

- `NODE_ACCEPTED` → `node.status = DONE`;`acceptance_result = "pass"`(不变)。
- `NODE_REJECTED` → `require_node_transition(node, FAILED)` + `node.status = FAILED`(was `PARTIAL_FAILED`);`acceptance_result = "fail"`(不变,承载区分)。
- `NODE_FAILED` → `FAILED`(不变,本来就没用 `PARTIAL_FAILED`)。
- `GOAL_VERIFIED`(pass)→ `_advance_phase(task, DONE)`(was `DELIVERED`)。
- `GOAL_REJECTED`(fail)→ `_apply_goal_verdict(verdict="fail")` —— `AWAITING_HUMAN_ACCEPT` 等图谱状态逻辑保留(返工);**删掉 `HUNG` 分支**:
  - 旧:`if run_mode == BBS: _advance_phase(HUNG) else: _apply_goal_verdict(fail)`。
  - 新:BBS 模式 goal rejected 不再走任务级 `HUNG`;统一走 `_apply_goal_verdict(fail)` → 返工 `REVIEWING → EXECUTING`(或不可恢复时由 scheduler 升 `FAILED`)。删 `HUNG` 分支。
- `HUNG` event kind 分支(L546)删除;`CANCELLED` event 分支保留(→ `CANCELLED`)。

### 3.6 `_apply_goal_verdict`

- pass:`graph_status = VERIFIED`;`_advance_phase(task, DONE)`(was `DELIVERED`)。
- fail:`graph_status = AWAITING_HUMAN_ACCEPT`;返工推进(已有逻辑,确认其目标态是 `EXECUTING` 而非旧 `EXECUTING`——`REVIEWING → EXECUTING` 由守卫表支持)。

### 3.7 `_advance_phase`(L566)

签名不变(接收 `TaskStatus`)。仅仅是传入的目标态值变了。无需改实现。

### 3.8 `history`(用户新增,在流)

不动。

---

## 4. `TaskScheduler`(`core/task/services/task_scheduler.py`)

6 处 `TaskStatus.*` + 2 处 `PARTIAL_FAILED`。

### 4.1 `start`(L247-256)

```python
if task.status is not TaskStatus.DEFINED:           # was PLANNED
    raise IllegalTransitionError(...)
task.status = TaskStatus.EXECUTING
task.execution_graph.root_phase = TaskStatus.EXECUTING
```

### 4.2 `tick` 终态推进(L303-314)

- `_all_settled` 为真 → `_advance(task, REVIEWING)`(was `VALIDATING`)。
- **新增 FAILED 分支**(R4 触发,放 termination guard 前):

```python
gap = compute_gap(task)
if gap["unrecoverable_failed"]:
    self._advance(task, TaskStatus.FAILED)
    self._svc._task_repo.save(task)
    logger.info("[Scheduler] task %s unrecoverable FAILED → FAILED", task_id)
    return {"task_id": task_id, "action": "task_failed"}
```

- termination guard:`loop_round >= MAX_LOOP_ROUNDS or no_progress >= MAX_NO_PROGRESS_TICKS` →
  - 若 `gap["unrecoverable_failed"]` → `_advance(FAILED)`(已被上面分支拦截,此处兜底同效)。
  - 否则 `_advance(REVIEWING)`(was `VALIDATING`)。

### 4.3 `compute_gap` refactor(L117-149)

**删掉对 `NodeStatus.PARTIAL_FAILED` 的依赖**,改读节点属性(spec R9/R10):

```python
def compute_gap(task: Task) -> dict:
    reroute_nodes, split_nodes, unrecoverable = [], [], []
    for n in task.execution_graph.nodes:
        if n.status is not NodeStatus.FAILED:
            continue
        attempts = len({a.executor_id for a in n.attempted_executors}) if n.attempted_executors else 0
        atomic = task.execution_graph.loop_round >= MAX_RECOMPOSE  # 或 recompose_count,视已有字段
        acceptance_fail = n.properties.get("acceptance_result") == "fail"
        # 验收失败 → 优先 reroute(换执行者重跑);执行失败 attempts 未耗尽 → reroute;耗尽 → split
        if attempts < DEFAULT_MAX_ATTEMPTS and not atomic:
            reroute_nodes.append(n)
        elif not atomic:
            split_nodes.append(n)
        else:
            unrecoverable.append(n)  # (a) 原子终止 OR (b) attempts 耗尽且无 reroute/split 余地
    atomic = task.execution_graph.loop_round >= MAX_RECOMPOSE
    need_split = bool(split_nodes) and not atomic
    need_reroute = bool(reroute_nodes) or (bool(split_nodes) and atomic)
    return {
        "need_reroute": need_reroute,
        "need_split": need_split,
        "reroute_nodes": reroute_nodes,
        "split_nodes": split_nodes,
        "atomic": atomic,
        "unrecoverable_failed": unrecoverable,  # 新增
    }
```

要点:
- 不再用 `PARTIAL_FAILED`(已删)。验收失败(`acceptance_result=="fail"`)与执行失败统一从 `FAILED` 节点里按 `attempts`/`atomic` 分流。
- 新增 `unrecoverable_failed` 字段,承载 R4 (a)/(b) 两种触发(§0 决策)。

### 4.4 `_watchdog`(6.5.3 已定型)

- `set_node_status(FAILED)` 不变(节点 FAILED,非任务级)。
- 注释里 "force VALIDATING" → "force REVIEWING"。
- 常量 `PROBE_AFTER_TICKS/MAX_PROBES/MAX_REDRIVES/MAX_NO_PROGRESS_TICKS/MAX_LOOP_ROUNDS/MAX_RECOMPOSE/DEFAULT_MAX_ATTEMPTS` 不动。

### 4.5 `_advance`(L435)

不变(转调 `require_task_transition`)。

---

## 5. 事件 `EventKind`(`core/task/domain/events.py`)

`EventKind` 枚举值(`node.accepted` / `node.rejected` / `node.failed` / `goal.verified` / `goal.rejected` / `loop.rerouted` / `cancelled` / `hung` / …)是事件名,**不引用 TaskStatus**。仅需:

- 评估 `HUNG` event kind 是否还有生产者(§3.5 删了 `GOAL_REJECTED` 的 HUNG 分支 + `HUNG` event 分支)。若 `EventKind.HUNG` 不再有写者,保留枚举项(向前兼容事件日志)但标注 deprecated;**不留任务级 HUNG 状态**(状态机已删)。
- 不新增 event kind(FAILED 是 scheduler 内部 `_advance`,复用既有 task-status 变更通道,不引入 `task.failed` event;如需事件留痕,复用 `_emit` + 既有 `TASK_*` 或新增,见 §10 风险)。

---

## 6. HTTP 层(`adapters/http/task/`)

### 6.1 `schemas.py`

`TaskGraphView` / `TaskNodeView` / `TaskDetailView` 的 `status` 字段是松类型 `str`(承载枚举 `.value`),**无需改 schema 定义**。仅:
- 若 schema 有 `Literal["intake","discussing",...]` 约束 → 替换为新值集合。grep 确认无 Literal 约束(状态字段均裸 str),故 schemas.py 多数情况下不改。

### 6.2 `router.py`

status 序列化走 `task.status.value`,自动跟随枚举。仅注释/文档串里有旧状态名的需改。无逻辑改动。

---

## 7. Noop / 测试双(`plugins/community/task/__init__.py` 等)

### 7.1 `NoopTaskService`

- docstring "Task at INTAKE" → "at DRAFTING"。
- `get_task_graph` 返回的 `root_phase: "intake"` → `"drafting"`。
- `get_node_detail` 返回 `status: "pending"`(node,不变)。

### 7.2 `NoopTaskService.history` / `_NoopExecution.probe`(test_protocols.py)

不动(6.5.3 已加)。

### 7.3 `LocalBotExecutorPort` / `HangingBotExecutor`(`local_executor.py`)

事件 envelope `kind: "node.accepted"` 不变;不引用 TaskStatus。不动。

---

## 8. 测试改动

### 8.1 Avernet community task 套件(~247 用例)

按枚举名全局替换 + 断言更新:

| 旧断言 | 新断言 |
|---|---|
| `status == "intake"` / `TaskStatus.INTAKE` | `drafting` / `DRAFTING` |
| `"discussing"` / `DISCUSSING` | `drafting`(amend 后**不再**到 discussing,改为断言仍 `drafting`) |
| `"planned"` / `PLANNED` | `defined` |
| `"validating"` / `VALIDATING` | `reviewing` |
| `"delivered"` / `DELIVERED` | `done` |
| `"hung"` / `HUNG` | 删除或改 `failed`(按场景) |
| `NodeStatus.PARTIAL_FAILED` | `NodeStatus.FAILED` + 断言 `properties["acceptance_result"] == "fail"` |

重点测试文件:
- `tests/community/api/task/test_scheduler.py` —— `compute_gap` 断言(删 `PARTIAL_FAILED` 用例,加 `unrecoverable_failed` 用例)、startup `PLANNED→EXECUTING` → `DEFINED→EXECUTING`、tick 终态 `VALIDATING` → `REVIEWING`、新增 `task_failed` action 用例。
- `tests/community/api/task/test_e2e_case_a_d.py`(若有)—— 全回路态名断言。
- `tests/community/api/task/test_task_service_api_conformance.py` —— amend 后态断言改 `drafting`(不变态);finalize_plan 源态 `drafting`;goal.verified → `done`。
- `tests/community/api/task/test_local_executor.py` —— 事件 kind 不变。
- `tests/community/api/task/test_protocols.py` —— Noop 双不动(§7.2)。
- 任何 `HUNG` 相关用例:断言改 `failed`(任务级)或 `human_required`(节点级)。

### 8.2 新增 FAILED 用例(覆盖 R4 a/b)

- `(a) 原子终止`:构造 `loop_round >= MAX_RECOMPOSE` + 一个 FAILED 节点 → `tick` 后 `task.status == FAILED`。
- `(b) 节点升级`:构造 FAILED 节点 `attempted_executors` 去重数 `>= DEFAULT_MAX_ATTEMPTS` + 无 reroute 候选 → `tick` 后 `FAILED`。
- 恢复优先:FAILED 节点 `attempts < max` + 有候选 → `tick` 走 reroute(不升 FAILED),断言 `LOOP_REROUTED` + 节点回 `RUNNING`。

### 8.3 ocb corp smoke(`ocb/src/backend/tests/corp/endpoints/test_task_loop_smoke.py`)

- `test_corp_profile_task_loop_http_baseline`:`created["status"] == "intake"` → `"drafting"`;`graph["root_phase"] == "intake"` → `"drafting"`;`detail["status"] == "intake"` → `"drafting"`。
- `test_corp_profile_plan_approve_tick_closes_catalog_gap`:`plan_r.json()["status"] == "planned"` → `"defined"`;`approve_r.json()["status"] in ("executing","validating")` → `("executing","reviewing")`。
- `test_corp_profile_full_loop_http_to_delivered`:`"validating"` → `"reviewing"`;`"delivered"` → `"done"`。
- 这些依赖 ocb-public submodule 同步;Avernet 改完后用户手动 sync,再跑 corp smoke。

---

## 9. task-recognition skill + 副屏 card(`ocb/skills/task-recognition/`)

### 9.1 `SKILL.md` 状态引用替换

| 位置 | 旧 | 新 |
|---|---|---|
| frontmatter `description` 末句 "状态流转为 PLANNED" | `PLANNED` | `DEFINED` |
| Phase C1 标题 "创建任务(INTAKE)" + 文 "状态为 INTAKE" | `INTAKE` | `DRAFTING` |
| Phase C2 末 "amend 后 Task 状态自动迁移 INTAKE → DISCUSSING" | 自动迁移 | **删**:amend 不切态,留 `DRAFTING` |
| Phase D3 末 "Task 状态迁移 DISCUSSING → PLANNED" | `DISCUSSING → PLANNED` | `DRAFTING → DEFINED` |
| Phase D4 标题 "PLANNED → EXECUTING" + 文 "PLANNED → EXECUTING" | `PLANNED → EXECUTING` | `DEFINED → EXECUTING` |
| Phase E "Task 保留在 DISCUSSING/PLANNED 状态" | `DISCUSSING/PLANNED` | `DRAFTING/DEFINED` |
| `## 状态流转对照` 表全部 | INTAKE/DISCUSSING/PLANNED | DRAFTING/DRAFTING/DEFINED(见 §0 映射) |
| `## 异常处理` "amend 后状态未迁移…空 patch 不触发 INTAKE→DISCUSSING" | 整条 | **删**(amend 不切态,该异常概念失效) |
| `## 卡片交互` payload status 枚举 `"intake \| discussing \| planned \| executing \| validating \| delivered \| cancelled \| hung"` | 旧集 | `"drafting \| defined \| executing \| reviewing \| done \| cancelled \| failed"` |
| 各 Phase 输出示例 `"status":"discussing"` / `"planned"` | 旧 | `drafting` / `defined` |
| `### 各 Phase 产出` 表 `task.status` 列 | discussing/planned | drafting/defined |

### 9.2 `card.jsx` 状态引用

卡片内 `data.task.status` 的判定分支(渲染徽章/文案)按新枚举替换:`intake→drafting`、`discussing→drafting`、`planned→defined`、`validating→reviewing`、`delivered→done`、`hung→failed`(或删 hung 分支)。具体行号在实现阶段 grep `status` 定位。

### 9.3 skill 不触达面的说明

在 SKILL.md `## 状态流转对照` 补一行说明:`REVIEWING` / `DONE` / `FAILED` 是内核调度器 + owner-bot 自驱推进,skill 不主动调用 API 触发,但 payload `task.status` 序列化会携带,card 需能渲染。

---

## 10. 落点与红线

- **代码落点**:枚举/状态机/service/scheduler/Noop/测试 → Avernet `src/backend/src/agentclaw/community/core/task/` + `plugins/community/task/` + `adapters/http/task/` + `tests/community/api/task/`。skill/card → `ocb/skills/task-recognition/`。corp smoke → `ocb/src/backend/tests/corp/`。
- **不 cp 到 ocb-public**:Avernet 改完后用户手动 sync 到 ocb 的 `ocb-public` submodule(用户既定流程)。
- **api↔core 红线**:本次改动全部在 `core/task/` 内(枚举/状态机/service),`adapters/http/task/` 仅状态值跟随。不新增 api→core 跨层调用,不违反四层依赖。
- **Avernet 代码风格**:`from __future__ import annotations`、`Optional[T]` 不用 `T | None`、`StrEnum`、dataclass、不裸 SQL。
- **编辑纪律**:只改状态名相关行,不顺手格式化无关代码。

---

## 11. 风险与回滚

| 风险 | 缓解 |
|---|---|
| `amend` 不再切态,依赖 "amend 后到 DISCUSSING" 的既有测试/逻辑断 | §3.2 显式删切态 + §8 全套断言更新;skill 文案同步 |
| `HUNG` 删除,既有 HUNG 用例/事件日志语义断 | 状态机删 HUNG;`EventKind.HUNG` 保留枚举不写;用例改 `failed`/`human_required` |
| `compute_gap` 重构引入 `unrecoverable_failed` 判错,R4 漏触发 | §8.2 新增 a/b 两条 FAILED 用例 + 恢复优先用例 |
| ocb-public 同步时点:corp smoke 依赖 submodule 新枚举 | Avernet 改完→用户 sync→再跑 corp smoke;期间 corp smoke 暂 skip |
| 破坏性重命名无灰度(任务未上线,spec 已授权) | 直接改,无 alias;回滚 = git revert 整个改动集 |

---

## 12. 实施顺序(进 tasks 阶段拆条)

1. 枚举 + 状态机(models.py / state_machine.py)—— 自底向上,先定契约。
2. task_service.py 状态引用 + amend 切态删除 + HUNG 分支删除。
3. task_scheduler.py 状态引用 + compute_gap refactor + FAILED 分支。
4. Noop / schemas / router 注释与 root_phase。
5. Avernet community 测试全量更新 + 新增 FAILED 用例 → 跑绿。
6. SKILL.md + card.jsx 状态引用。
7. (用户 sync ocb-public 后)ocb corp smoke 更新 → 跑绿。

---

> 本 plan 覆盖 spec 的 R1–R11 + §4 验收标准 + §5 开放问题(已收敛于 §0)。待用户 "proceed" 后进 tasks 阶段,拆为可勾选清单。