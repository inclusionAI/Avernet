# BBS 自主接力 skill 实现计划(plan.md)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 6 态 `ExecutionEngine` 任务框架上叠加 BBS 自主接力——内容 skill `bbs-relay-pickup` + 三条 `bbs/*` 路由 + drain/harness bbs 守卫,让任意引擎 bot 自驱接 BBS 升级任务续做。

**Architecture:** bot 经 `GET /api/task/list`(筛 `bbs_mode`)+ `/dashboard` 取整图 → `POST /api/task/bbs/claim` CAS 独占任务根(`bbs_owner`)→ 自判 → `POST /api/task/bbs/attach` 挂一个 `run_mode="bbs"` scoped 子节点并 start → 执行 → `POST /api/task/bbs/result` 落终态 + 清根 `bbs_owner` 释放 → 下个 bot 读 `goal`+DONE 叶子接力。lease 复用 `TaskHarness` SLA;崩溃到期 harness 清 `bbs_owner`+标终态(不重派)。全程经 `TaskGraphService` SSOT 写口,不改 `Status`/转移表/`TaskNodePatch`/`PlanResult`/`on_*` 签名。

**Tech Stack:** Python 3.12 / FastAPI + fastapi-injector(`Injected(...)` DI)/ Pydantic v2 / `@dataclass` + `StrEnum` 领域模型 / `threading.RLock` per-task 串行 / stdlib `logging` / pytest。

**Spec:** `src/backend/specs/2026-08-09-task-goal-driven-task-runner-bbs/spec.md`(权威 WHAT/WHY;本计划为其 HOW delta,声明不破坏上游 `2026-08-09-task-goal-driven-execution-framework` 契约)

## Global Constraints

(逐字引自 spec §2.3 / §7.3 / §13.3,每个 task 隐含遵守)
- 不改 6 态 `Status` 与转移表 `_ACCEPTANCE_TRANSITIONS`/`_DIRECT_TRANSITIONS`;不引入 `FAILED→RUNNING`/`HUNG→RUNNING`。
- 不改 `TaskNodePatch`/`TaskGraphPatch`/`PlanResult`/`on_execute`/`on_start`/`on_report`/`on_miss`/`on_harness` 签名;新行为只做加法(新路由、新 `TaskGraphService`/`ExecutionEngine`/`TaskHarness` 方法、新 `TaskSummary.bbs_mode` 字段)。
- 状态写入单一化:`TaskGraphService`(`update_task_node_info`/`add_task_nodes`/`update_task_graph_info`)唯一改口;claim/attach/result 均经其。
- 错误映射:`TaskError` 家族不在 `_DOMAIN_ERROR_STATUS_MAP`,沿用现有 router 级 `try/except → HTTPException` 模式(见 `router.py:43-50,146-159`)。
- `BBS_MAX_DEPTH`、`SLA_TIMEOUT` 当前不在 `_execution_config` 默认,本计划自加默认。
- 鉴权本期不做:`bbs/*` 沿用 `/api/task/*` 裸奔现状。

## Plan-level refinements over spec(实现期厘清,需同步回 spec)

> 这些是 spec WHAT 层面留白、计划 HOW 层面必须定死的点。spec 自检未发现;实现后建议回填 spec(见交付时提示)。

1. **`bbs/result` 不直调 `engine.on_report`**(spec FR-EXT-03 写"→ `engine.on_report`")。`on_report` 的 `_on_pass_collect` 会触发框架经 owner-bot 重规划+派发,与 §10.4"下个 bot 自挂 scoped 节点接力"冲突。故 `bbs/result` 走**新增 `ExecutionEngine.on_bbs_report`(加法,collector-free)**:仅 `update_task_node_info` 翻 scoped 节点终态 + 可选根收口 + 清 `bbs_owner`,不跑 `_on_pass_collect`/`_on_fail_collect`/`_drain`。仍经 SSOT `update_task_node_info`(与 `on_report` 同一写口),仅省去 side-effect。
2. **根目标收口信号**:bot 报 `bbs/result` 时带 `root_verified: bool`。`True` = bot(LLM 判)认定根 `Goal` 已满足 → `on_bbs_report` 翻根 `PLANNING→DONE`(acceptance PASS)+ 图 `DONE`。`False` = 仅本 scoped 节点终态,留给下个 bot 接力。
3. **BBS 深度闸 = per-task `bbs_relay_count`**(图 `extend_props`),每次 `attach` +1;`>= BBS_MAX_DEPTH` → 图 `HUNG(stuck)` + 拒 attach(`TaskStateError`)。区别于 `loop_round`(升级计数,已由 `MAX_LOOP` 兜底)。
4. **claim 仅校验 `bbs_mode=True`**;"图空闲 + 根 `PLANNING`"由 `attach` 经 `add_task_nodes` 的 a/b/c/d 触发条件自然裁(不满足→`GraphIntegrityError`→409)。claim 不重复判空闲。

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `core/task/domain/models.py` | `TaskSummary` 加 `bbs_mode` | Modify |
| `core/task/task_graph/task_graph_service.py` | SSOT 加法:`claim_bbs_owner`/`attach_bbs_node`/`list_task_summaries` 填 `bbs_mode`/`_execution_config` 加 `BBS_MAX_DEPTH` 默认 | Modify |
| `core/task/task_center/task_service.py` | facade 加法:`claim_bbs_task`/`attach_bbs_node`/`report_bbs_result` | Modify |
| `core/task/task_center/engine.py` | 加法:`on_bbs_report`(collector-free 翻态+收口+清 owner);`_prepare_into` 跳过 `run_mode=="bbs"` | Modify |
| `core/task/task_harness/harness.py` | `_poll_once` RUNNING 扫:`run_mode=="bbs"` SLA 到期走"清 `bbs_owner`+标终态",不重派 | Modify |
| `core/task/api/task_service.py` | `TaskServiceProtocol` 加三方法签名 | Modify |
| `adapters/http/task/schemas.py` | `TaskSummaryDTO.bbs_mode` + `BbsClaimDTO`/`BbsAttachDTO`/`BbsResultDTO` | Modify |
| `adapters/http/task/translator.py` | `TaskSummary→DTO` 透传 `bbs_mode` | Modify |
| `adapters/http/task/router.py` | 三路由 `POST /api/task/bbs/{claim,attach,result}` | Modify |
| `tests/community/core/task/test_bbs_*.py` | 单测/契约测 | Create |
| `tests/community/core/task/singlebox_e2e/test_bbs_relay_e2e.py` | E2E | Create |
| `bbs-relay-pickup/SKILL.md` + `references/*` | 内容 skill | Create |

---

### Task 1: `TaskSummary.bbs_mode` 直出

**Files:**
- Modify: `core/task/domain/models.py:145-154`(`TaskSummary`)
- Modify: `core/task/task_graph/task_graph_service.py:365-380`(`list_task_summaries`)
- Modify: `adapters/http/task/schemas.py:107-114`(`TaskSummaryDTO`)
- Modify: `adapters/http/task/translator.py`(TaskSummary→DTO 映射,grep `TaskSummaryDTO(`)
- Test: `tests/community/core/task/test_bbs_summary_mode.py`

**Interfaces:**
- Consumes: `TaskExecutionGraph.extend_props["bbs_mode"]`(由 `_hung_and_escalate` 写,`engine.py:423`)
- Produces: `TaskSummary.bbs_mode: bool`;`TaskSummaryDTO.bbs_mode: bool`

- [ ] **Step 1: 写失败测试**

```python
# tests/community/core/task/test_bbs_summary_mode.py
from agentclaw.community.core.task.task_graph.task_graph_service import TaskGraphService
from agentclaw.community.core.task.domain.models import TaskInfo, TaskSpec, Metadata, Goal, AcceptanceCriteria, Context

def _task_info(task_id="t1"):
    return TaskInfo(task_spec=TaskSpec(metadata=Metadata(task_id=task_id, title="t", instruction="i"),
                    context=Context(background="", extend_props={}),
                    goal=Goal(objective="o", acceptances=[AcceptanceCriteria(id="a1", description="d")])),
                    source_channel_type="bot", source_channel_id="b1", execution_config={})

def test_summary_exposes_bbs_mode_flag():
    svc = TaskGraphService()
    svc.initialize_graph(_task_info())
    svc.update_task_graph_info("t1", __import__("agentclaw.community.core.task.domain.models", fromlist=["TaskGraphPatch"]).TaskGraphPatch(
        extend_props_patch={"bbs_mode": True}))
    summaries = svc.list_task_summaries()
    assert summaries[0].bbs_mode is True

def test_summary_bbs_mode_default_false():
    svc = TaskGraphService()
    svc.initialize_graph(_task_info("t2"))
    assert svc.list_task_summaries()[0].bbs_mode is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `src/backend/.venv/bin/python -m pytest tests/community/core/task/test_bbs_summary_mode.py -v`
Expected: FAIL(`TaskSummary` 无 `bbs_mode` 属性)

- [ ] **Step 3: 最小实现**

`models.py` `TaskSummary` 末尾加字段:
```python
    bbs_mode: bool = False
```

`task_graph_service.py` `list_task_summaries` 的 `TaskSummary(...)` 构造加:
```python
                summaries.append(TaskSummary(
                    task_id=tid, run_id=graph.run_id, status=graph.status,
                    title=title, node_count=len(graph.tasks), loop_round=graph.loop_round,
                    bbs_mode=bool(graph.extend_props.get("bbs_mode", False)))
```

`schemas.py` `TaskSummaryDTO` 末尾加:
```python
    bbs_mode: bool = False
```

`translator.py`:在构造 `TaskSummaryDTO` 处(grep `TaskSummaryDTO(`)加 `bbs_mode=summary.bbs_mode`。

- [ ] **Step 4: 跑测试确认通过**

Run: `src/backend/.venv/bin/python -m pytest tests/community/core/task/test_bbs_summary_mode.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/backend/src/agentclaw/community/core/task/domain/models.py \
  src/backend/src/agentclaw/community/core/task/task_graph/task_graph_service.py \
  src/backend/src/agentclaw/community/adapters/http/task/schemas.py \
  src/backend/src/agentclaw/community/adapters/http/task/translator.py \
  tests/community/core/task/test_bbs_summary_mode.py
git commit -m "feat(task): expose bbs_mode on TaskSummary/DTO for relay discovery"
```

---

### Task 2: `BBS_MAX_DEPTH` 默认接入 `_execution_config`

**Files:**
- Modify: `core/task/task_graph/task_graph_service.py:53-54`(模块默认常量)、`401-409`(`_execution_config`)
- Test: `tests/community/core/task/test_bbs_config.py`

**Interfaces:**
- Consumes: 无
- Produces: `_execution_config(task_id)["BBS_MAX_DEPTH"]`(默认 3),供 Task 5 attach 深度闸

- [ ] **Step 1: 写失败测试**

```python
# tests/community/core/task/test_bbs_config.py
from agentclaw.community.core.task.task_graph.task_graph_service import TaskGraphService
from agentclaw.community.core.task.domain.models import TaskInfo, TaskSpec, Metadata, Goal, AcceptanceCriteria, Context

def _ti(tid="c1"):
    return TaskInfo(task_spec=TaskSpec(metadata=Metadata(task_id=tid, title="t", instruction="i"),
                    context=Context(background="", extend_props={}),
                    goal=Goal(objective="o", acceptances=[AcceptanceCriteria(id="a1", description="d")])),
                    source_channel_type="bot", source_channel_id="b1", execution_config={})

def test_bbs_max_depth_default():
    svc = TaskGraphService()
    svc.initialize_graph(_ti())
    cfg = svc._execution_config("c1")
    assert cfg["BBS_MAX_DEPTH"] == 3

def test_bbs_max_depth_overridable():
    svc = TaskGraphService()
    svc.initialize_graph(_ti("c2"))
    svc.update_task_graph_info("c2", __import__("agentclaw.community.core.task.domain.models", fromlist=["TaskGraphPatch"]).TaskGraphPatch(
        extend_props_patch={"execution_config": {"BBS_MAX_DEPTH": 5}}))
    assert svc._execution_config("c2")["BBS_MAX_DEPTH"] == 5
```

- [ ] **Step 2: 跑测试确认失败**

Run: `src/backend/.venv/bin/python -m pytest tests/community/core/task/test_bbs_config.py -v`
Expected: FAIL(`KeyError: 'BBS_MAX_DEPTH'`)

- [ ] **Step 3: 最小实现**

模块默认区(`_DEFAULT_MAX_DEPTH=2`/`_DEFAULT_MAX_LOOP=10` 旁)加:
```python
_DEFAULT_BBS_MAX_DEPTH = 3
```

`_execution_config` 内 `cfg.setdefault(...)` 块加一行:
```python
        cfg.setdefault("BBS_MAX_DEPTH", _DEFAULT_BBS_MAX_DEPTH)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `src/backend/.venv/bin/python -m pytest tests/community/core/task/test_bbs_config.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/backend/src/agentclaw/community/core/task/task_graph/task_graph_service.py tests/community/core/task/test_bbs_config.py
git commit -m "feat(task): default BBS_MAX_DEPTH in _execution_config"
```

---

### Task 3: `TaskGraphService.claim_bbs_owner` + `TaskService.claim_bbs_task` + Protocol

**Files:**
- Modify: `core/task/task_graph/task_graph_service.py`(加 `claim_bbs_owner`)
- Modify: `core/task/task_center/task_service.py:26-86`(加 `claim_bbs_task`)
- Modify: `core/task/api/task_service.py`(`TaskServiceProtocol` 加签名)
- Test: `tests/community/core/task/test_bbs_claim.py`

**Interfaces:**
- Consumes: per-task `_lock_for`;`update_task_node_info`;`_require_graph`
- Produces: `TaskGraphService.claim_bbs_owner(task_id, bot_id) -> NodeOpResult`;`TaskService.claim_bbs_task(task_id, bot_id) -> NodeOpResult`(同步)

- [ ] **Step 1: 写失败测试**

```python
# tests/community/core/task/test_bbs_claim.py
import threading
from agentclaw.community.core.task.task_graph.task_graph_service import TaskGraphService
from agentclaw.community.core.task.domain.models import TaskInfo, TaskSpec, Metadata, Goal, AcceptanceCriteria, Context, TaskGraphPatch
from agentclaw.community.core.task.domain.errors import TaskStateError

def _ti(tid="p1"):
    return TaskInfo(task_spec=TaskSpec(metadata=Metadata(task_id=tid, title="t", instruction="i"),
                    context=Context(background="", extend_props={}),
                    goal=Goal(objective="o", acceptances=[AcceptanceCriteria(id="a1", description="d")])),
                    source_channel_type="bot", source_channel_id="b1", execution_config={})

def _bbs_task(svc, tid):
    svc.initialize_graph(_ti(tid))
    svc.update_task_graph_info(tid, TaskGraphPatch(extend_props_patch={"bbs_mode": True}))

def test_claim_cas_exactly_one_wins():
    svc = TaskGraphService()
    _bbs_task(svc, "p1")
    r1 = svc.claim_bbs_owner("p1", "botA")
    assert r1.success is True
    try:
        svc.claim_bbs_owner("p1", "botB")
        assert False, "second claim should lose CAS"
    except TaskStateError:
        pass

def test_claim_idempotent_for_same_bot():
    svc = TaskGraphService()
    _bbs_task(svc, "p2")
    svc.claim_bbs_owner("p2", "botA")
    r = svc.claim_bbs_owner("p2", "botA")  # 同 bot 重 claim 幂等
    assert r.success is True

def test_claim_rejects_non_bbs_task():
    svc = TaskGraphService()
    svc.initialize_graph(_ti("p3"))  # 未置 bbs_mode
    try:
        svc.claim_bbs_owner("p3", "botA")
        assert False
    except TaskStateError:
        pass
```

- [ ] **Step 2: 跑测试确认失败**

Run: `src/backend/.venv/bin/python -m pytest tests/community/core/task/test_bbs_claim.py -v`
Expected: FAIL(`AttributeError: 'TaskGraphService' object has no attribute 'claim_bbs_owner'`)

- [ ] **Step 3: 最小实现**

`task_graph_service.py` 加(顶部 `import time` 若无):
```python
    def claim_bbs_owner(self, task_id: str, bot_id: str) -> NodeOpResult:
        """BBS 接力:任务根级 CAS 占有(root.run_info.extend_props['bbs_owner'])。恰一赢;输者/非 bbs 任务 → TaskStateError。"""
        with self._lock_for(task_id):
            graph = self._require_graph(task_id)
            if not graph.extend_props.get("bbs_mode"):
                raise TaskStateError(f"claim_bbs_owner: task={task_id} 非 bbs_mode 任务")
            root = next((n for n in graph.tasks if n.node_id == task_id), None)
            if root is None:
                raise TaskNotFoundError(f"claim_bbs_owner: root not found task={task_id}")
            owner = root.run_info.extend_props.get("bbs_owner")
            if owner is not None and owner != bot_id:
                raise TaskStateError(f"claim_bbs_owner: task={task_id} 已被 {owner} 占有")
            return self.update_task_node_info(TaskNodePatch(
                task_id=task_id, node_id=task_id,
                extend_props_patch={"bbs_owner": bot_id, "bbs_claim_at": time.time()}))
```
(RLock 可重入,`update_task_node_info` 内部再取 `_lock_for` 安全。)

`task_service.py` `TaskService` 加:
```python
    def claim_bbs_task(self, task_id: str, bot_id: str) -> NodeOpResult:
        """BBS 接力步②:任务根级 CAS 占有。"""
        return self._graph.claim_bbs_owner(task_id, bot_id)
```

`api/task/task_service.py` `TaskServiceProtocol` 加(与现有 `execute`/`get_task_dashboard`/`list_tasks` 并列):
```python
    def claim_bbs_task(self, task_id: str, bot_id: str) -> NodeOpResult: ...
```

- [ ] **Step 4: 跑测试确认通过**

Run: `src/backend/.venv/bin/python -m pytest tests/community/core/task/test_bbs_claim.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/backend/src/agentclaw/community/core/task/task_graph/task_graph_service.py \
  src/backend/src/agentclaw/community/core/task/task_center/task_service.py \
  src/backend/src/agentclaw/community/core/task/api/task_service.py \
  tests/community/core/task/test_bbs_claim.py
git commit -m "feat(task): add task-level CAS claim_bbs_owner for BBS relay"
```

---

### Task 4: `POST /api/task/bbs/claim` 路由

**Files:**
- Modify: `adapters/http/task/schemas.py`(加 `BbsClaimDTO`)
- Modify: `adapters/http/task/router.py`(加路由;`TaskStateError` 已 import 于 `:109` 区)
- Test: `tests/community/core/task/test_bbs_claim_route.py`

**Interfaces:**
- Consumes: `TaskService.claim_bbs_task`(Task 3);`Injected(TaskServiceProtocol)`;`HTTPException`(已 import `:43`)
- Produces: `POST /api/task/bbs/claim` → `ApiResponse[dict]`(含 `root_node_id`);`TaskStateError→409`

- [ ] **Step 1: 写失败测试**

```python
# tests/community/core/task/test_bbs_claim_route.py
import pytest
from fastapi.testclient import TestClient
from agentclaw.community.adapters.http.app import app
from agentclaw.community.core.task.domain.models import TaskGraphPatch

@pytest.fixture(autouse=True)
def _bbs_task(task_graph_service, task_service_protocol):
    from agentclaw.community.core.task.domain.models import TaskInfo, TaskSpec, Metadata, Goal, AcceptanceCriteria, Context
    ti = TaskInfo(task_spec=TaskSpec(metadata=Metadata(task_id="r1", title="t", instruction="i"),
                  context=Context(background="", extend_props={}),
                  goal=Goal(objective="o", acceptances=[AcceptanceCriteria(id="a1", description="d")])),
                  source_channel_type="bot", source_channel_id="b1", execution_config={})
    task_graph_service.initialize_graph(ti)
    task_graph_service.update_task_graph_info("r1", TaskGraphPatch(extend_props_patch={"bbs_mode": True}))

def test_claim_route_200_then_409(client):
    r1 = client.post("/api/task/bbs/claim", json={"task_id": "r1", "bot_id": "botA"})
    assert r1.status_code == 200 and r1.json()["data"]["root_node_id"] == "r1"
    r2 = client.post("/api/task/bbs/claim", json={"task_id": "r1", "bot_id": "botB"})
    assert r2.status_code == 409
```
> 注:测试 fixture 复用现有 singlebox/conftest 的 `client`/`task_graph_service`/`task_service_protocol`(见 `tests/community/core/task/singlebox_e2e/conftest.py`);若该层无 `client` fixture,改用 `TestClient(app)` + 手动注入 graph,实现者据 conftest 适配。

- [ ] **Step 2: 跑测试确认失败**

Run: `src/backend/.venv/bin/python -m pytest tests/community/core/task/test_bbs_claim_route.py -v`
Expected: FAIL(404 / 路由不存在)

- [ ] **Step 3: 最小实现**

`schemas.py` 加:
```python
class BbsClaimDTO(BaseModel):
    """POST /api/task/bbs/claim 请求体。"""
    task_id: str
    bot_id: str
```

`router.py` 加(与现有 `@router.post` 同风格,`Injected` DI):
```python
@router.post("/bbs/claim", response_model=ApiResponse[dict[str, Any]])
async def bbs_claim(
    body: BbsClaimDTO,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> ApiResponse[dict[str, Any]]:
    """BBS 接力步②:任务根级 CAS 占有;恰一赢,输者 409。"""
    try:
        result = service.claim_bbs_task(body.task_id, body.bot_id)
    except TaskStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return ApiResponse(success=True, message="OK", error_code=200,
                       data={"root_node_id": result.node_id, "task_id": body.task_id})
```

- [ ] **Step 4: 跑测试确认通过**

Run: `src/backend/.venv/bin/python -m pytest tests/community/core/task/test_bbs_claim_route.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/backend/src/agentclaw/community/adapters/http/task/{schemas.py,router.py} tests/community/core/task/test_bbs_claim_route.py
git commit -m "feat(task): POST /api/task/bbs/claim route (task-level CAS, 409 on lose)"
```

---

### Task 5: `TaskGraphService.attach_bbs_node` + `TaskService.attach_bbs_node` + Protocol

**Files:**
- Modify: `core/task/task_graph/task_graph_service.py`(加 `attach_bbs_node`)
- Modify: `core/task/task_center/task_service.py`(加 `attach_bbs_node`)
- Modify: `core/task/api/task_service.py`(Protocol 加签名)
- Test: `tests/community/core/task/test_bbs_attach.py`

**Interfaces:**
- Consumes: `claim_bbs_owner`(Task 3,持有者校验);`add_task_nodes`(a/b/c/d 触发);`update_task_node_info`(`PENDING→RUNNING`);`update_task_graph_info`(`bbs_relay_count`);`_execution_config["BBS_MAX_DEPTH"]`(Task 2)
- Produces: `TaskService.attach_bbs_node(task_id, parent_node_id, task_spec, bot_id) -> TaskNode`(新建 `run_mode="bbs"` 子节点 + 翻 `RUNNING`)

- [ ] **Step 1: 写失败测试**

```python
# tests/community/core/task/test_bbs_attach.py
import uuid
import pytest
from agentclaw.community.core.task.task_graph.task_graph_service import TaskGraphService
from agentclaw.community.core.task.domain.models import (TaskInfo, TaskSpec, Metadata, Goal, AcceptanceCriteria, Context,
    TaskGraphPatch, Status)
from agentclaw.community.core.task.domain.errors import TaskStateError

def _ti(tid):
    return TaskInfo(task_spec=TaskSpec(metadata=Metadata(task_id=tid, title="t", instruction="i"),
                    context=Context(background="", extend_props={}),
                    goal=Goal(objective="o", acceptances=[AcceptanceCriteria(id="a1", description="d")])),
                    source_channel_type="bot", source_channel_id="b1", execution_config={})

def _scoped_spec():
    return TaskSpec(metadata=Metadata(task_id=f"bbs-{uuid.uuid4().hex[:6]}", title="bbs-scoped", instruction="do part"),
                   context=Context(background="", extend_props={}),
                   goal=Goal(objective="part", acceptances=[AcceptanceCriteria(id="a1", description="d")]))

def _bbs_root_planning(svc, tid):
    """构造 bbs_mode + 根 PLANNING(可委托)的可接任务。"""
    svc.initialize_graph(_ti(tid))
    svc.update_task_graph_info(tid, TaskGraphPatch(extend_props_patch={"bbs_mode": True}))
    # 根 PENDING → PLANNING(满足 add 触发 cond_c)
    svc.update_task_node_info(__import__("agentclaw.community.core.task.domain.models", fromlist=["TaskNodePatch"]).TaskNodePatch(
        task_id=tid, node_id=tid, status=Status.PLANNING))

def test_attach_creates_bbs_node_running():
    svc = TaskGraphService()
    _bbs_root_planning(svc, "a1")
    svc.claim_bbs_owner("a1", "botA")
    node = svc.attach_bbs_node("a1", parent_node_id="a1", task_spec=_scoped_spec(), bot_id="botA")
    assert node.run_info.run_mode == "bbs"
    assert node.status == Status.RUNNING
    assert node.run_info.assignee == "botA"
    graph = svc.query_task_dashboard("a1")
    assert graph.extend_props.get("bbs_relay_count") == 1

def test_attach_rejects_non_owner():
    svc = TaskGraphService()
    _bbs_root_planning(svc, "a2")
    svc.claim_bbs_owner("a2", "botA")
    with pytest.raises(TaskStateError):
        svc.attach_bbs_node("a2", "a2", _scoped_spec(), bot_id="botB")

def test_attach_depth_gate_hung():
    svc = TaskGraphService()
    _bbs_root_planning(svc, "a3")
    svc.update_task_graph_info("a3", TaskGraphPatch(extend_props_patch={"execution_config": {"BBS_MAX_DEPTH": 1}}))
    svc.claim_bbs_owner("a3", "botA")
    svc.attach_bbs_node("a3", "a3", _scoped_spec(), "botA")  # relay_count 1 == BBS_MAX_DEPTH 1
    # 清 owner 后第二次 attach 应触发深度闸
    svc.update_task_node_info(__import__("agentclaw.community.core.task.domain.models", fromlist=["TaskNodePatch"]).TaskNodePatch(
        task_id="a3", node_id="a3", extend_props_patch={"bbs_owner": None}))
    svc.claim_bbs_owner("a3", "botA")
    with pytest.raises(TaskStateError):
        svc.attach_bbs_node("a3", "a3", _scoped_spec(), "botA")
    assert svc.query_task_dashboard("a3").status == Status.HUNG
```

- [ ] **Step 2: 跑测试确认失败**

Run: `src/backend/.venv/bin/python -m pytest tests/community/core/task/test_bbs_attach.py -v`
Expected: FAIL(`AttributeError: ... 'attach_bbs_node'`)

- [ ] **Step 3: 最小实现**

`task_graph_service.py` 加(`import uuid` 若无):
```python
    def attach_bbs_node(self, task_id: str, parent_node_id: str, task_spec: "TaskSpec", bot_id: str) -> TaskNode:
        """BBS 接力步④:在 parent 下新建 run_mode=bbs scoped 子节点 + 翻 PENDING→RUNNING(create+start 合一)。
        前置:调用者须为当前 bbs_owner;parent 须满足 add 触发条件(根 PLANNING 等);深度闸 BBS_MAX_DEPTH。"""
        with self._lock_for(task_id):
            graph = self._require_graph(task_id)
            root = next((n for n in graph.tasks if n.node_id == task_id), None)
            if root is None or root.run_info.extend_props.get("bbs_owner") != bot_id:
                raise TaskStateError(f"attach_bbs_node: 非claim持有者 task={task_id}")
            relay_count = int(graph.extend_props.get("bbs_relay_count", 0))
            if relay_count >= self._execution_config(task_id)["BBS_MAX_DEPTH"]:
                self.update_task_graph_info(task_id, TaskGraphPatch(
                    status=Status.HUNG, extend_props_patch={"hung_reason": "bbs_relay_exhausted"}))
                raise TaskStateError(f"attach_bbs_node: BBS relay 深度达上限 task={task_id}")
            node_id = f"bbs-{uuid.uuid4().hex[:8]}"
            node = TaskNode(node_id=node_id, task_id=task_id, status=Status.PENDING,
                            task_spec=task_spec,
                            run_info=RuntimeInfo(run_mode="bbs", assignee=bot_id, start_time=time.time()),
                            node_run_graph=graph)
            self.add_task_nodes([node], parent_node_id=parent_node_id)   # a/b/c/d 校验 + 父→PLANNING
            self.update_task_node_info(TaskNodePatch(
                task_id=task_id, node_id=node_id, status=Status.RUNNING))  # create+start
            self.update_task_graph_info(task_id, TaskGraphPatch(
                extend_props_patch={"bbs_relay_count": relay_count + 1}))
            return node
```

`task_service.py` 加:
```python
    def attach_bbs_node(self, task_id: str, parent_node_id: str, task_spec: "TaskSpec", bot_id: str) -> "TaskNode":
        """BBS 接力步④:挂 run_mode=bbs scoped 节点 + start。"""
        return self._graph.attach_bbs_node(task_id, parent_node_id, task_spec, bot_id)
```

`api/task_service.py` Protocol 加:
```python
    def attach_bbs_node(self, task_id: str, parent_node_id: str, task_spec: "TaskSpec", bot_id: str) -> "TaskNode": ...
```

- [ ] **Step 4: 跑测试确认通过**

Run: `src/backend/.venv/bin/python -m pytest tests/community/core/task/test_bbs_attach.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/backend/src/agentclaw/community/core/task/{task_graph/task_graph_service.py,task_center/task_service.py,api/task_service.py} tests/community/core/task/test_bbs_attach.py
git commit -m "feat(task): attach_bbs_node (scoped bbs child + start, depth gate)"
```

---

### Task 6: `POST /api/task/bbs/attach` 路由

**Files:**
- Modify: `adapters/http/task/schemas.py`(`BbsAttachDTO` + 复用 `TaskSpecDTO`)
- Modify: `adapters/http/task/router.py`(路由)
- Modify: `adapters/http/task/translator.py`(`task_spec_from_dto` 复用)
- Test: `tests/community/core/task/test_bbs_attach_route.py`

**Interfaces:**
- Consumes: `TaskService.attach_bbs_node`(Task 5);`task_spec_from_dto`(`schemas.py` 现有,`execute_task` 用)
- Produces: `POST /api/task/bbs/attach` → `ApiResponse[dict]`(含 `node_id`);`TaskStateError`/`GraphIntegrityError`→409

- [ ] **Step 1: 写失败测试**

```python
# tests/community/core/task/test_bbs_attach_route.py
def test_attach_route_creates_node(client, task_graph_service, task_service_protocol):
    # 复用 Task 4 的 _bbs_task 式构造 + 根 PLANNING + claim
    ...  # 实现者照 test_bbs_claim_route 的 fixture 模式:initialize_graph→bbs_mode→根 PLANNING→claim botA
    r = client.post("/api/task/bbs/attach", json={
        "task_id": "x1", "parent_node_id": "x1", "bot_id": "botA",
        "task_spec": {"metadata": {"task_id": "bbs-scoped", "title": "s", "instruction": "do"},
                      "context": {"background": "", "extend_props": {}},
                      "goal": {"objective": "part", "acceptances": [{"id": "a1", "description": "d"}]}}})
    assert r.status_code == 200
    assert r.json()["data"]["node_id"].startswith("bbs-")

def test_attach_route_non_owner_409(client, ...):
    r = client.post("/api/task/bbs/attach", json={"task_id": "x2", "parent_node_id": "x2", "bot_id": "botB", "task_spec": {...}})
    assert r.status_code == 409
```
> 实现者:照 `test_bbs_claim_route.py` 的 fixture(构造 bbs_mode + 根 PLANNING + claim)。`task_spec` JSON 结构对齐 `TaskSpecDTO`(`schemas.py` 现有)。

- [ ] **Step 2: 跑测试确认失败**

Run: `src/backend/.venv/bin/python -m pytest tests/community/core/task/test_bbs_attach_route.py -v`
Expected: FAIL(路由不存在)

- [ ] **Step 3: 最小实现**

`schemas.py` 加:
```python
class BbsAttachDTO(BaseModel):
    """POST /api/task/bbs/attach 请求体。"""
    task_id: str
    parent_node_id: str
    task_spec: TaskSpecDTO
    bot_id: str
```

`router.py` 加:
```python
@router.post("/bbs/attach", response_model=ApiResponse[dict[str, Any]])
async def bbs_attach(
    body: BbsAttachDTO,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> ApiResponse[dict[str, Any]]:
    """BBS 接力步④:挂 run_mode=bbs scoped 节点 + start。仅 claim 持有者。"""
    task_spec = task_spec_from_dto(body.task_spec)
    try:
        node = service.attach_bbs_node(body.task_id, body.parent_node_id, task_spec, body.bot_id)
    except (TaskStateError, GraphIntegrityError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return ApiResponse(success=True, message="OK", error_code=200,
                       data={"node_id": node.node_id, "task_id": body.task_id})
```
(`task_spec_from_dto`、`GraphIntegrityError` 已在 router 导入区可见;若 `GraphIntegrityError` 未导入,补 import。)

- [ ] **Step 4: 跑测试确认通过**

Run: `src/backend/.venv/bin/python -m pytest tests/community/core/task/test_bbs_attach_route.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/backend/src/agentclaw/community/adapters/http/task/{schemas.py,router.py,translator.py} tests/community/core/task/test_bbs_attach_route.py
git commit -m "feat(task): POST /api/task/bbs/attach route (scoped bbs node + start)"
```

---

### Task 7: `ExecutionEngine.on_bbs_report` + `TaskService.report_bbs_result` + Protocol

**Files:**
- Modify: `core/task/task_center/engine.py`(加 `on_bbs_report`)
- Modify: `core/task/task_center/task_service.py`(加 `report_bbs_result`)
- Modify: `core/task/api/task_service.py`(Protocol 加签名)
- Test: `tests/community/core/task/test_bbs_report.py`

**Interfaces:**
- Consumes: `update_task_node_info`(acceptance 翻态);`update_task_graph_info`(图 DONE/HUNG);`_root`(engine.py:158);`TaskNodePatch`/`AcceptanceResult`(已 import)
- Produces: `TaskService.report_bbs_result(task_id, node_id, bot_id, acceptance_result?, output_patch?, exec_error?, root_verified=False) -> NodeOpResult`(async,collector-free)

- [ ] **Step 1: 写失败测试**

```python
# tests/community/core/task/test_bbs_report.py
import pytest
from agentclaw.community.core.task.task_graph.task_graph_service import TaskGraphService
from agentclaw.community.core.task.task_center.task_service import TaskService
from agentclaw.community.core.task.domain.models import (TaskInfo, TaskSpec, Metadata, Goal, AcceptanceCriteria, Context,
    TaskGraphPatch, TaskNodePatch, Status, AcceptanceResult, AcceptanceVerdict)
from agentclaw.community.core.task.domain.errors import TaskStateError

# 复用 Task 5 的 _bbs_root_planning + attach 构造一个 RUNNING bbs 节点
async def test_report_pass_finishes_graph_when_root_verified(task_service_with_bbs_node):
    svc, task_id, node_id, bot = task_service_with_bbs_node
    r = await svc.report_bbs_result(task_id, node_id, bot,
                                    acceptance_result=AcceptanceResult(AcceptanceVerdict.PASS), root_verified=True)
    assert r.success is True
    assert svc.get_task_dashboard(task_id).status == Status.DONE
    # claim 已释放
    root = next(n for n in svc.get_task_dashboard(task_id).tasks if n.node_id == task_id)
    assert root.run_info.extend_props.get("bbs_owner") is None

async def test_report_fail_partial_releases_claim(task_service_with_bbs_node):
    svc, task_id, node_id, bot = task_service_with_bbs_node
    await svc.report_bbs_result(task_id, node_id, bot,
                                acceptance_result=AcceptanceResult(AcceptanceVerdict.FAIL, gaps=["partial"]),
                                output_patch={"progress": 30})
    root = next(n for n in svc.get_task_dashboard(task_id).tasks if n.node_id == task_id)
    assert root.run_info.extend_props.get("bbs_owner") is None  # 释放
    scoped = next(n for n in svc.get_task_dashboard(task_id).tasks if n.node_id == node_id)
    assert scoped.status == Status.FAILED
    assert scoped.run_info.output.get("progress") == 30  # checkpoint 保留

async def test_report_rejects_non_owner(task_service_with_bbs_node):
    svc, task_id, node_id, bot = task_service_with_bbs_node
    with pytest.raises(TaskStateError):
        await svc.report_bbs_result(task_id, node_id, "botOTHER",
                                    acceptance_result=AcceptanceResult(AcceptanceVerdict.PASS))
```
> fixture `task_service_with_bbs_node`:构造 bbs_mode + 根 PLANNING + claim botA + attach 一个 scoped 节点,返回 `(TaskService, task_id, node_id, botA)`。按 Task 3/5 的构造模式。

- [ ] **Step 2: 跑测试确认失败**

Run: `src/backend/.venv/bin/python -m pytest tests/community/core/task/test_bbs_report.py -v`
Expected: FAIL(`AttributeError: ... 'report_bbs_result'`)

- [ ] **Step 3: 最小实现**

`engine.py` 加(`async`,与 `on_report` 并列):
```python
    async def on_bbs_report(self, patch: TaskNodePatch, root_verified: bool = False) -> NodeOpResult:
        """BBS 接力步⑤回投:collector-free——仅翻 scoped 节点终态(SSOT update_task_node_info),
        不跑 _on_pass_collect/_on_fail_collect/_drain(避免框架经 owner-bot 重规划抢占接力,对齐 spec §10.4)。
        root_verified=True → 根 acceptance PASS→DONE + 图 DONE。最后清根 bbs_owner 释放 claim。"""
        with self._lock_for(patch.task_id):
            graph = self._graph.query_task_dashboard(patch.task_id)
            root = next((n for n in graph.tasks if n.node_id == patch.task_id), None)
            if root is None or root.run_info.extend_props.get("bbs_owner") != patch.assignee:
                # 持有者校验:用 patch.assignee 传 bot_id(调用方设)
                raise TaskStateError(f"on_bbs_report: 非claim持有者 task={patch.task_id}")
            result = self._graph.update_task_node_info(patch)   # acceptance→DONE/FAILED,或 output_patch/exec_error fold
            if root_verified:
                self._graph.update_task_node_info(TaskNodePatch(
                    task_id=patch.task_id, node_id=patch.task_id,
                    acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.PASS)))  # 根 PLANNING→DONE
                self._graph.update_task_graph_info(patch.task_id, TaskGraphPatch(status=Status.DONE))
            # 清根 bbs_owner 释放
            self._graph.update_task_node_info(TaskNodePatch(
                task_id=patch.task_id, node_id=patch.task_id, extend_props_patch={"bbs_owner": None}))
            return result
```

`task_service.py` 加:
```python
    async def report_bbs_result(self, task_id: str, node_id: str, bot_id: str,
                                acceptance_result: "AcceptanceResult | None" = None,
                                output_patch: dict | None = None, exec_error: str | None = None,
                                root_verified: bool = False) -> NodeOpResult:
        """BBS 接力步⑤:回投 scoped 节点终态 + 释放 claim。collector-free。"""
        patch = TaskNodePatch(task_id=task_id, node_id=node_id, assignee=bot_id,
                              acceptance_result=acceptance_result, output_patch=output_patch, exec_error=exec_error)
        return await self._engine.on_bbs_report(patch, root_verified=root_verified)
```

`api/task_service.py` Protocol 加:
```python
    async def report_bbs_result(self, task_id: str, node_id: str, bot_id: str,
                                acceptance_result: "AcceptanceResult | None" = None,
                                output_patch: dict | None = None, exec_error: str | None = None,
                                root_verified: bool = False) -> NodeOpResult: ...
```

- [ ] **Step 4: 跑测试确认通过**

Run: `src/backend/.venv/bin/python -m pytest tests/community/core/task/test_bbs_report.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/backend/src/agentclaw/community/core/task/{task_center/{engine.py,task_service.py},api/task_service.py} tests/community/core/task/test_bbs_report.py
git commit -m "feat(task): on_bbs_report collector-free result path + claim release"
```

---

### Task 8: `POST /api/task/bbs/result` 路由

**Files:**
- Modify: `adapters/http/task/schemas.py`(`BbsResultDTO` + `AcceptanceResultDTO` 若无)
- Modify: `adapters/http/task/router.py`(路由)
- Modify: `adapters/http/task/translator.py`(`acceptance_result_from_dto` 若无)
- Test: `tests/community/core/task/test_bbs_result_route.py`

**Interfaces:**
- Consumes: `TaskService.report_bbs_result`(Task 7)
- Produces: `POST /api/task/bbs/result` → `ApiResponse[dict]`;`TaskStateError→409`

- [ ] **Step 1: 写失败测试**

```python
# tests/community/core/task/test_bbs_result_route.py
def test_result_route_root_verified_done(client, bbs_task_with_claimed_node):
    task_id, node_id, bot = bbs_task_with_claimed_node
    r = client.post("/api/task/bbs/result", json={
        "task_id": task_id, "node_id": node_id, "bot_id": bot,
        "acceptance_result": {"verdict": "PASS", "acceptances_metric": [], "gaps": []},
        "root_verified": True})
    assert r.status_code == 200
    # dashboard 应 DONE
    d = client.get("/api/task/dashboard", params={"task_id": task_id}).json()["data"]
    assert d["status"] == "DONE"

def test_result_route_non_owner_409(client, bbs_task_with_claimed_node):
    task_id, node_id, bot = bbs_task_with_claimed_node
    r = client.post("/api/task/bbs/result", json={"task_id": task_id, "node_id": node_id, "bot_id": "botOTHER",
        "acceptance_result": {"verdict": "PASS", "acceptances_metric": [], "gaps": []}})
    assert r.status_code == 409
```
> `bbs_task_with_claimed_node` fixture:构造 bbs_mode + 根 PLANNING + claim botA + attach scoped → 返回 `(task_id, node_id, botA)`(Task 4/6 fixture 模式)。

- [ ] **Step 2: 跑测试确认失败**

Run: `src/backend/.venv/bin/python -m pytest tests/community/core/task/test_bbs_result_route.py -v`
Expected: FAIL(路由不存在)

- [ ] **Step 3: 最小实现**

`schemas.py` 加(若已有 `AcceptanceResultDTO` 则复用):
```python
class AcceptanceResultDTO(BaseModel):
    verdict: Literal["PASS", "FAIL"]
    acceptances_metric: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)

class BbsResultDTO(BaseModel):
    """POST /api/task/bbs/result 请求体。"""
    task_id: str
    node_id: str
    bot_id: str
    acceptance_result: AcceptanceResultDTO | None = None
    output_patch: dict[str, Any] | None = None
    exec_error: str | None = None
    root_verified: bool = False
```

`translator.py` 加(若无):
```python
def acceptance_result_from_dto(dto: AcceptanceResultDTO) -> AcceptanceResult:
    return AcceptanceResult(verdict=AcceptanceVerdict(dto.verdict),
                            acceptances_metric=list(dto.acceptances_metric), gaps=list(dto.gaps))
```

`router.py` 加:
```python
@router.post("/bbs/result", response_model=ApiResponse[dict[str, Any]])
async def bbs_result(
    body: BbsResultDTO,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> ApiResponse[dict[str, Any]]:
    """BBS 接力步⑤:回投 scoped 节点终态 + 释放 claim(collector-free)。"""
    ar = acceptance_result_from_dto(body.acceptance_result) if body.acceptance_result else None
    try:
        await service.report_bbs_result(body.task_id, body.node_id, body.bot_id,
                                        acceptance_result=ar, output_patch=body.output_patch,
                                        exec_error=body.exec_error, root_verified=body.root_verified)
    except TaskStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return ApiResponse(success=True, message="OK", error_code=200, data={"ok": True})
```

- [ ] **Step 4: 跑测试确认通过**

Run: `src/backend/.venv/bin/python -m pytest tests/community/core/task/test_bbs_result_route.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/backend/src/agentclaw/community/adapters/http/task/{schemas.py,router.py,translator.py} tests/community/core/task/test_bbs_result_route.py
git commit -m "feat(task): POST /api/task/bbs/result route (collector-free report + claim release)"
```

---

### Task 9: drain 守卫——`_prepare_into` 跳过 `run_mode=="bbs"`

**Files:**
- Modify: `core/task/task_center/engine.py:462-514`(`_prepare_into`)
- Test: `tests/community/core/task/test_bbs_drain_guard.py`

**Interfaces:**
- Consumes: `TaskNode.run_info.run_mode`
- Produces: `_prepare_into` 不把 `run_mode=="bbs"` 的 PENDING 节点纳入派发(防框架自动消费 bot 自挂的 bbs 节点)

- [ ] **Step 1: 写失败测试**

```python
# tests/community/core/task/test_bbs_drain_guard.py
import pytest
from agentclaw.community.core.task.task_graph.task_graph_service import TaskGraphService
from agentclaw.community.core.task.task_center.engine import ExecutionEngine
from agentclaw.community.core.task.domain.models import (TaskInfo, TaskSpec, Metadata, Goal, AcceptanceCriteria, Context,
    TaskGraphPatch, TaskNode, RuntimeInfo, Status)

@pytest.mark.asyncio
async def test_bbs_pending_node_not_auto_dispatched(task_graph_service_with_bbs_pending):
    """一个 run_mode=bbs 的 PENDING 叶不应被 _prepare_into 纳入派发(不会被 dispatch/drain 翻 RUNNING)。"""
    # 构造 bbs_mode + 根 PLANNING + 一个手工 PENDING bbs 叶(模拟 bot 挂前/异常态)
    engine = ExecutionEngine(task_graph_service_with_bbs_pending)
    # 触发 _prepare_into 扫描(经 on_pass/on_execute 间接 or 直接调 _prepare_into(task_id, []))
    await engine._prepare_into(task_id, [])  # side 空,仅扫 PENDING 候选
    graph = task_graph_service_with_bbs_pending.query_task_dashboard(task_id)
    bbs_leaf = next(n for n in graph.tasks if n.run_info.run_mode == "bbs")
    assert bbs_leaf.status == Status.PENDING  # 未被自动翻 RUNNING
    assert bbs_leaf.run_info.assignee == "botA"  # 未被 dispatcher 改写
```
> 实现者:构造一个 root PLANNING + 一个 `TaskNode(status=PENDING, run_info=RuntimeInfo(run_mode="bbs", assignee="botA"))` 的图(经 `add_task_nodes` 或直接构造)。`_prepare_into` 直接调(side=[])验证跳过。

- [ ] **Step 2: 跑测试确认失败**

Run: `src/backend/.venv/bin/python -m pytest tests/community/core/task/test_bbs_drain_guard.py -v`
Expected: FAIL(bbs 叶被 _prepare_into 受理/翻态或 dispatching 被设)

- [ ] **Step 3: 最小实现**

`_prepare_into` 内,在筛选 toDo(扫 PENDING 候选)的循环里,跳过 `run_mode=="bbs"`:
```python
            # 守卫(FR-EXT-06):bbs 节点由 bot 经 bbs/attach 自驱,框架不自动派发/翻态
            if n.run_info.run_mode == "bbs":
                continue
```
插在 `_prepare_into` 遍历候选节点、判断 `dispatching`/`dispatch_error` 跳过的同一处(实现者定位 `for n in ...` 候选筛选循环,在现有 `if n.run_info.extend_props.get("dispatching"): continue` 旁加此守卫)。

- [ ] **Step 4: 跑测试确认通过**

Run: `src/backend/.venv/bin/python -m pytest tests/community/core/task/test_bbs_drain_guard.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/backend/src/agentclaw/community/core/task/task_center/engine.py tests/community/core/task/test_bbs_drain_guard.py
git commit -m "feat(task): _prepare_into skips run_mode=bbs nodes (relay guard)"
```

---

### Task 10: harness bbs-lease 到期分支——清 `bbs_owner` + 标终态,不重派

**Files:**
- Modify: `core/task/task_harness/harness.py:92-123`(`_poll_once` RUNNING 扫)
- Test: `tests/community/core/task/test_bbs_harness_expire.py`

**Interfaces:**
- Consumes: `run_mode=="bbs"` 判定;`TaskNodePatch`(acceptance FAIL 标终态);root `bbs_owner` 清除
- Produces: RUNNING bbs 节点 SLA 到期 → 清根 `bbs_owner` + scoped 节点 `FAILED`(via acceptance `FAIL` gaps=`["bbs_lease_expired"]`),**不**走 `PENDING` 重派

- [ ] **Step 1: 写失败测试**

```python
# tests/community/core/task/test_bbs_harness_expire.py
import pytest
from agentclaw.community.core.task.task_harness.harness import TaskHarness
from agentclaw.community.core.task.domain.models import Status

def test_bbs_lease_expire_clears_owner_and_marks_terminal_not_redispatch(
        task_graph_service_with_bbs_running, fake_clock):
    """bbs RUNNING 节点超 SLA → 根 bbs_owner 清空 + scoped 节点 FAILED;断言不重派(非 PENDING)。"""
    graph_svc = task_graph_service_with_bbs_running
    harness = TaskHarness(graph_svc, clock=fake_clock, sleep=lambda *_: None,
                          default_sla_timeout=10.0, default_pending_timeout=10.0, interval=0)
    # 起一个 bbs RUNNING 节点 + 根 bbs_owner=botA
    fake_clock.advance(11.0)  # 超 SLA
    harness._poll_once()
    g = graph_svc.query_task_dashboard(task_id)
    root = next(n for n in g.tasks if n.node_id == task_id)
    assert root.run_info.extend_props.get("bbs_owner") is None
    scoped = next(n for n in g.tasks if n.run_info.run_mode == "bbs")
    assert scoped.status == Status.FAILED  # 标终态,非 PENDING 重派
```
> fixture 构造 bbs_mode + 根 PLANNING(bbs_owner=botA)+ 一个 `RUNNING` 的 `run_mode="bbs"` scoped 节点(`start_time` 设为 fake_clock 旧值)。`fake_clock` 仿现有 harness 测试的 clock 注入模式(`harness.py` 构造支持 `clock=`/`sleep=`)。

- [ ] **Step 2: 跑测试确认失败**

Run: `src/backend/.venv/bin/python -m pytest tests/community/core/task/test_bbs_harness_expire.py -v`
Expected: FAIL(bbs 节点被当普通 RUNNING 重派为 PENDING,owner 未清)

- [ ] **Step 3: 最小实现**

`_poll_once` RUNNING 扫分支(lines 92-123):在判定 `now - t0 > sla` 即将追加 reset patch 前,分支 `run_mode=="bbs"`:
```python
                if mode == "bbs":
                    # BBS lease 到期(FR-EXT-06):清根 bbs_owner + scoped 节点标终态(FAIL),不重派
                    patches.append(TaskNodePatch(
                        task_id=tid, node_id=nid,
                        acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.FAIL, gaps=["bbs_lease_expired"])))
                    patches.append(TaskNodePatch(
                        task_id=tid, node_id=tid,  # 根:清 bbs_owner
                        extend_props_patch={"bbs_owner": None}))
                    continue
```
插在现有 `if now - t0 > sla:` 块内、追加 `status=PENDING` reset patch 之前(实现者定位 lines 113-120 的 append 处,在其前加 `if mode == "bbs":` 分支)。需在 harness.py 顶部 import `AcceptanceResult`/`AcceptanceVerdict`(若未导入)。

- [ ] **Step 4: 跑测试确认通过**

Run: `src/backend/.venv/bin/python -m pytest tests/community/core/task/test_bbs_harness_expire.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/backend/src/agentclaw/community/core/task/task_harness/harness.py tests/community/core/task/test_bbs_harness_expire.py
git commit -m "feat(task): harness bbs lease-expire clears owner + marks terminal (no redispatch)"
```

---

### Task 11: 内容 skill `bbs-relay-pickup`

**Files:**
- Create: `<skill 落点>/bbs-relay-pickup/SKILL.md`(落点路径 plan 评审;候选 `src/backend/skills/bbs-relay-pickup/` 或独立 skills repo,对齐 e2e `tests/community/core/task/singlebox_e2e/skills/` 范本)
- Create: `references/task-api.md`、`references/judge-rubric.md`、`references/idempotency.md`
- Test: 人工/skill 场景奢验(见 Task 12 E2E)

**Interfaces:**
- Consumes: `/api/task/list`(+`bbs_mode`)、`/api/task/dashboard`、`/bbs/claim`、`/bbs/attach`、`/bbs/result`
- Produces: 跨引擎可移植内容 skill

- [ ] **Step 1: 写 SKILL.md(frontmatter + 正文流程门)**

```markdown
---
name: bbs-relay-pickup
description: 被唤醒时从 task API 发现 BBS 升级任务、CAS 占根、自判剩余、自挂 scoped 节点、执行、经回投写回
allowed_tools: [exec]
---

# BBS 自主接力

被唤醒后按序执行(一次唤醒 = 一个 scoped 节点):

1. **发现**:`GET /api/task/list` → 取 `bbs_mode==true` 的任务;`GET /api/task/dashboard?task_id=` 取整图。
2. **占根**:`POST /api/task/bbs/claim {task_id, bot_id: <自己>}`。**409 = 别的 bot 已占 → 换任务**。
3. **自判**:读根 `goal`+`acceptances` + 已成 DONE 叶子 + 前序 scoped 节点 `run_info.output`(checkpoint),判"剩余里我能做哪部分"(full/partial/skip)。判据见 `references/judge-rubric.md`。
4. **挂节点 + 干**:`POST /api/task/bbs/attach {task_id, parent_node_id: <根>, task_spec: <你能做的那部分>, bot_id: <自己>}` → 拿 `node_id`。**claim 成功才允许 attach**。用原生能力执行这一个节点。长活周期性 `POST /api/task/bbs/result` 带 `output_patch` 作 checkpoint。
5. **写回**:`POST /api/task/bbs/result {task_id, node_id, bot_id, acceptance_result, root_verified}`:
   - 全做完且根目标满足 → `acceptance_result.verdict=PASS, root_verified=true` → 图 DONE。
   - 仅完成部分 → `verdict=FAIL, gaps=[...], output_patch={...checkpoint...}, root_verified=false` → 释放 claim 供下个 bot 接力。
6. **边界**:无可做/pass 完 → 结束本次唤醒,等下次。

**不可绕过**:claim 成功才 attach;attach 必须挂 `run_mode="bbs"` 节点;写回必经 `bbs/result`。详见 `references/idempotency.md`。
```

- [ ] **Step 2: 写 `references/task-api.md`**(路由清单 + `bbs/result` envelope 构造样例,含 `acceptance_result`/`output_patch`/`root_verified`)

- [ ] **Step 3: 写 `references/judge-rubric.md`**(full/partial/skip 判据:根 goal vs DONE 叶子 + 自身能力;partial 时 `gaps` 描述 + `output_patch` checkpoint 约定)

- [ ] **Step 4: 写 `references/idempotency.md`**(claim CAS/409 换任务;harness SLA lease(不续租,崩溃到期被清,重 claim 接力);接力读 DONE+checkpoint 不重做;深度闸 BBS_MAX_DEPTH→HUNG)

- [ ] **Step 5: 提交**

```bash
git add <skill 落点>/bbs-relay-pickup/
git commit -m "feat(skill): add bbs-relay-pickup content skill + references"
```

---

### Task 12: E2E singlebox —— claim race / 接力 / 崩溃 lease / 图级 HUNG skip

**Files:**
- Create: `tests/community/core/task/singlebox_e2e/test_bbs_relay_e2e.py`
- Modify: `tests/community/core/task/singlebox_e2e/conftest.py`(若需 BBS role bot/skill 装配;范本 `test_task_integration_e2e.py`)

**Interfaces:**
- Consumes: Task 1-11 全部;singlebox e2e 基建(`SingleboxBotProvisioner`、HTTP facade、`SINGLEBOX_TASK_E2E=1` 门)
- Produces: E2E 覆盖 spec §6 场景 A/C/D/G + 接力 B

- [ ] **Step 1: 写 E2E(claim race)**

```python
# tests/community/core/task/singlebox_e2e/test_bbs_relay_e2e.py
import pytest
pytestmark = pytest.mark.skipif(os.environ.get("SINGLEBOX_TASK_E2E") != "1",
                                reason="需 SINGLEBOX_TASK_E2E=1 singlebox 环境")

def test_two_bots_claim_same_bbs_task_exactly_one_wins(client, two_provisioned_bots):
    """场景 C:两 bot 同时 bbs/claim 同一 bbs 任务 → 恰一 200、一 409。"""
    task_id = _submit_bbs_task(client)  # 经 /api/task/execute 提交 + 触发升 BBS(或直接置 bbs_mode+根PLANNING)
    r1 = client.post("/api/task/bbs/claim", json={"task_id": task_id, "bot_id": "botA"})
    r2 = client.post("/api/task/bbs/claim", json={"task_id": task_id, "bot_id": "botB"})
    assert {r1.status_code, r2.status_code} == {200, 409}
```

- [ ] **Step 2: 写 E2E(接力 B + 崩溃 D)**

```python
def test_partial_handoff_relay(client, provisioned_bots):
    """场景 B:botA claim→attach→result(FAIL+gaps+output_patch)→释放;botB claim→读 DONE/ checkpoint→续做→root_verified DONE。"""
    # botA 走 claim→attach→result(FAIL, gaps, output_patch={progress:30}, root_verified=false)
    # botB 走 claim→读 dashboard→attach(续)→result(PASS, root_verified=true)
    assert client.get("/api/task/dashboard", params={"task_id": tid}).json()["data"]["status"] == "DONE"

def test_crash_lease_relay(client, fake_clock_or_sla_short, provisioned_bots):
    """场景 D:botA claim+attach 后不 result(模拟崩溃)→harness SLA 到期清 owner+标终态→botB claim 接力。"""
    # botA claim+attach,不 report;推进 clock > SLA;触发 harness _poll_once(或等 daemon 扫)
    # botB claim 成功(owner 已清);读图续做
    ...
```

- [ ] **Step 3: 写 E2E(图级 HUNG skip G)**

```python
def test_graph_hung_skipped(client, provisioned_bots):
    """场景 G:任务图级 HUNG(root_stuck)→bot discover/claim 发现根不可委托→skip(不 attach)。"""
    # 构造一个 graph.status=HUNG 的 bbs 任务;bot claim 后 attach 应 409(GraphIntegrityError,根非 PLANNING)
    ...
```

- [ ] **Step 4: 跑 E2E**

Run: `SINGLEBOX_TASK_E2E=1 src/backend/.venv/bin/python -m pytest tests/community/core/task/singlebox_e2e/test_bbs_relay_e2e.py -v`
Expected: PASS(实现者按 singlebox 装配补 fixture:`_submit_bbs_task`、`provisioned_bots` 等,范本 `test_task_integration_e2e.py`)

- [ ] **Step 5: 提交**

```bash
git add tests/community/core/task/singlebox_e2e/test_bbs_relay_e2e.py tests/community/core/task/singlebox_e2e/conftest.py
git commit -m "test(task): BBS relay e2e (claim race / handoff relay / crash lease / graph-HUNG skip)"
```

---

## Self-Review(plan 自检,对 spec)

**1. Spec 覆盖:**
- FR-PICK-01 发现 → Task 1(`bbs_mode` 直出)+ Task 11 skill 步1 ✓
- FR-PICK-02 CAS 占根 → Task 3+4 ✓
- FR-PICK-03 自判 → Task 11 skill 步3 + judge-rubric ✓
- FR-PICK-04 挂节点+执行 → Task 5+6 + Task 9 守卫 ✓
- FR-PICK-05 写回 → Task 7+8 ✓(refinement:`on_bbs_report` collector-free,见上)
- FR-PICK-06 pass 边界+接力 → Task 7(root_verified)+ Task 11 ✓
- FR-IDEM-01 CAS → Task 3+4 ✓
- FR-IDEM-02 lease=harness → Task 10 ✓
- FR-IDEM-03 接力不重做 → Task 7(output_patch 保留)+ Task 11 ✓
- FR-IDEM-04 深度闸 → Task 2+5 ✓
- FR-EXT-01~06 → Task 3/5/7(01/02/03 服务端)、Task 4/6/8(路由)、Task 9/10(06 守卫)、Task 1(05 bbs_mode)、Task 2+5(04 409 扩展)✓
- FR-SKILL → Task 11 ✓
- AC-03/04/05/06/07/08/11 → Task 3/9/4/10/11/7/9 对应 ✓
- 场景 A/C/D/G/B → Task 12 ✓;E/H(长活分段)由 Task 7 output_patch + Task 10 体现,Task 12 可补(标 "可扩")。

**2. 占位扫描:** 已审,无 TBD/TODO。部分测试 fixture 标"实现者照 X 模式"——给出明确范本文件 + 构造步骤,非占位。

**3. 类型一致:** `claim_bbs_task`/`attach_bbs_node`/`report_bbs_result` 在 Protocol/facade/TaskGraphService/engine 间签名一致;`on_bbs_report(patch, root_verified)` 一致;`bbs_owner`/`bbs_relay_count`/`bbs_claim_at`/`bbs_lease_expired`/`bbs_relay_exhausted` 命名贯穿。

**计划-级 refinement(需回填 spec):** 上方"Plan-level refinements"4 条——`on_bbs_report` collector-free、`root_verified` 收口信号、`bbs_relay_count` 深度闸、claim 仅校验 `bbs_mode`。实现完建议同步 spec FR-EXT-03/FR-PICK-05/§10.4 措辞。

---

## 执行交接

计划已存 `src/backend/specs/2026-08-09-task-goal-driven-task-runner-bbs/plan.md`(配套 `tasks.md` 为任务清单索引)。两种执行方式:

**1. Subagent-Driven(推荐)** — 每 task 派独立 subagent,task 间评审,迭代快。
**2. Inline Execution** — 本会话用 executing-plans 批量执行,带 checkpoint 评审。

选哪种?