"""v2 端到端(走 TaskScheduler.tick,plan §20 完整 tick 驱动补充)。

与 ``test_v2_e2e.py``(图操作层)互补:本文件经真实 ``TaskScheduler._tick_v2``
驱动 NodeType 动作链,搜推/分解/派发/执行 全 mock,skill 判验以"直接置 SubtaskState
DONE"mock(对齐 plan §19.1)。覆盖:
- happy path 全程 tick(规划链→搜推命中→派发→skill 回投→终验 DONE/VERIFIED)
- 搜推未匹配→分解→子各命中→exec-aggregate 触发→终验
- 递归上限→mark-hang→AWAITING_HUMAN_ACCEPT
"""
from __future__ import annotations


from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    AcceptanceCriteriaKind,
    GraphStatus,
    NodeStatus,
    NodeType,
    Plan,
    RouteClass,
    RunMode,
    SubTaskSpec,
    TaskGoal,
    TaskStatus,
    TaskState,
)
from agentclaw.community.core.task.protocols import (
    BotCandidate,
    DispatchResult,
    RouteRecommendation,
)
from agentclaw.community.core.task.services import TaskService
from agentclaw.community.core.task.services.task_scheduler import TaskScheduler
from agentclaw.community.plugins.community.task.in_memory_repos import (
    InMemoryTaskEventRepo,
    InMemoryTaskRepo,
)
from agentclaw.community.plugins.community.task.panel_publisher import (
    RecordingPanelPublisher,
)


# --- fakes(对齐 test_scheduler.py 套路,v2 补 decompose_subtasks)------------

class FakeDiscover:
    """按 node_id 编程返回命中/未匹配(搜推先行 mock)。"""

    def __init__(self, hits: dict[str, list[str]] | None = None, default_hit: bool = True):
        self._hits = hits or {}
        self._default_hit = default_hit
        self.calls: list[str] = []

    def recommend(self, task_id: str, node_id: str) -> RouteRecommendation:
        self.calls.append(node_id)
        bots = self._hits.get(node_id)
        if bots is None:
            bots = ["bot1"] if self._default_hit else []
        cands = [BotCandidate(bot_id=b, fit_score=0.9) for b in bots]
        return RouteRecommendation(
            route_class=RouteClass.C1 if cands else RouteClass.C5,
            run_mode=RunMode.SINGLE_BOT,
            candidates=cands,
            confidence=0.9 if cands else 0.0,
        )


class FakeDecomposer:
    """decompose_subtasks(新签名)+ decompose(旧,过渡)。按 spec 分号句数返 children。"""

    def __init__(self, depth_override: int | None = None):
        self._depth_override = depth_override
        self.sub_calls: list[tuple[str, int]] = []

    def decompose(self, task_id: str) -> Plan:
        return Plan(
            sub_tasks=[
                SubTaskSpec(node_id="n_c1", spec="child a"),
                SubTaskSpec(node_id="n_c2", spec="child b"),
            ],
            confidence=0.6,
        )

    def decompose_subtasks(self, spec: str, state: TaskState) -> list[SubTaskSpec]:
        parent_depth = int(state.public.get("__decompose_parent_depth__", -1))
        child_depth = self._depth_override if self._depth_override is not None else (parent_depth + 1 if parent_depth >= 0 else 0)
        self.sub_calls.append((spec, child_depth))
        # 受控返 2 children(忽略 spec 文本;真实分解器按 spec 分句)
        return [
            SubTaskSpec(node_id="dc1", spec="child 1", run_mode=RunMode.SINGLE_BOT, depth=child_depth),
            SubTaskSpec(node_id="dc2", spec="child 2", run_mode=RunMode.SINGLE_BOT, depth=child_depth),
        ]


class FakeDriver:
    def __init__(self):
        self.dispatched: list[str] = []
        self.redispatched: list[tuple[str, RouteClass]] = []
        self.escalated: list[str] = []

    def dispatch_node(self, task_id, node_id):
        self.dispatched.append(node_id)
        return DispatchResult(node_id=node_id, executor_id=f"bot-{node_id}", run_mode=RunMode.SINGLE_BOT)

    def redispatch(self, task_id, node_id, route_class):
        self.redispatched.append((node_id, route_class))
        return DispatchResult(node_id=node_id, executor_id="bot-r", run_mode=RunMode.SINGLE_BOT)

    def escalate_to_bbs(self, task_id, reason=""):
        self.escalated.append(reason)
        return DispatchResult(node_id="", executor_id="", run_mode=RunMode.BBS)


class FakeExecution:
    def __init__(self):
        self.single_bots: list[tuple[str, str, str]] = []
        self.coop_groups: list[tuple[str, str, list[str]]] = []
        self.redispatches: list[tuple[str, str, str]] = []
        self.probes: list[tuple[str, str, str]] = []
        self.bbss: list[tuple[str, str, str]] = []

    def dispatch_single_bot(self, task_id, node_id, bot_id):
        self.single_bots.append((task_id, node_id, bot_id))
        return DispatchResult(node_id=node_id, executor_id=bot_id, run_mode=RunMode.SINGLE_BOT)

    def coop_group(self, task_id, node_id, bot_ids):
        self.coop_groups.append((task_id, node_id, list(bot_ids)))
        return DispatchResult(node_id=node_id, executor_id="", run_mode=RunMode.COOP_GROUP)

    def redispatch_node(self, task_id, node_id, bot_id):
        self.redispatches.append((task_id, node_id, bot_id))
        return DispatchResult(node_id=node_id, executor_id=bot_id, run_mode=RunMode.SINGLE_BOT)

    def probe(self, task_id, node_id, bot_id):
        self.probes.append((task_id, node_id, bot_id))
        return DispatchResult(node_id=node_id, executor_id=bot_id, run_mode=RunMode.SINGLE_BOT)

    def bbs(self, task_id, node_id, reason=""):
        self.bbss.append((task_id, node_id, reason))
        return DispatchResult(node_id=node_id, executor_id="", run_mode=RunMode.BBS)


def _svc() -> TaskService:
    return TaskService(InMemoryTaskRepo(), InMemoryTaskEventRepo(), RecordingPanelPublisher())


def _goal(acceptances: int = 1):
    return TaskGoal(
        objective="obj",
        acceptances=[
            AcceptanceCriteria(kind=AcceptanceCriteriaKind.OUTPUT, properties={"label": f"ac{i}"})
            for i in range(acceptances)
        ],
    )


def _v2_task(svc: TaskService, acceptances: int = 1):
    """建 task(带 goal)+ spawn_build_dag_v2 动作骨架。返回 task + 启动到 EXECUTING。"""
    t = svc.create(title="t", background="obj")
    task = svc.get(t.id)
    task.spec.goal = _goal(acceptances)
    svc._task_repo.save(task)  # noqa: SLF001
    task = svc.get(t.id)
    # DRAFTING → DEFINED(给一个最小 plan 过 finalize_plan 守旧契约;v2 走 spawn_build_dag_v2)
    svc.finalize_plan(task.id, Plan(sub_tasks=[SubTaskSpec(node_id="n_root", spec="root")], confidence=0.9))
    task = svc.get(task.id)
    task.status = TaskStatus.EXECUTING
    task.execution_graph = None  # 让 spawn_build_dag_v2 重建
    svc._task_repo.save(task)  # noqa: SLF001
    svc.spawn_build_dag_v2(task)
    # ON_PLAZA
    from agentclaw.community.core.task.domain.models import GraphStatus as GS

    task = svc.get(task.id)
    svc.mark_graph_status(task, GS.ON_PLAZA)
    return svc.get(task.id)


def _set_leaf_done(svc: TaskService, task_id: str, node_id: str):
    """mock skill 回投:叶子 DISPATCH 验收 PASS → Node + SubtaskState DONE。"""
    task = svc.get(task_id)
    node = svc._find_node(task, node_id)  # noqa: SLF001
    if node is not None:
        node.status = NodeStatus.DONE
    st = task.execution_graph.state.subtasks.get(node_id)
    if st is not None:
        st.status = NodeStatus.DONE
    svc._task_repo.save(task)  # noqa: SLF001


def _scheduler(svc, discover, decomposer=None, driver=None, execution=None):
    return TaskScheduler(
        svc,
        discover,
        driver or FakeDriver(),
        decomposer or FakeDecomposer(),
        execution or FakeExecution(),
    )


# --- happy path 全程 tick ---------------------------------------------------


def test_v2_tick_single_bot_happy_path():
    svc = _svc()
    task = _v2_task(svc, acceptances=1)
    discover = FakeDiscover(default_hit=True)  # 搜推恒命中 bot1
    sched = _scheduler(svc, discover)
    # tick1:规划链 n_rec/n_clar/n_exec → DONE;n_search 搜推命中 → 加 n_search_disp
    r1 = sched.tick(task.id)
    assert r1["action"] == "ticked_v2"
    task = svc.get(task.id)
    types = {n.node_id: n.node_type for n in task.execution_graph.nodes}
    assert types["n_recognition"] is NodeType.RECOGNITION
    assert types["n_bot_search"] is NodeType.BOT_SEARCH
    # 规划三节点 DONE;bot_search DONE(命中);新增 dispatch 子节点
    assert any(n.node_type is NodeType.DISPATCH for n in task.execution_graph.nodes)
    disp = next(n for n in task.execution_graph.nodes if n.node_type is NodeType.DISPATCH)
    assert disp.assignee == "bot1"
    # tick2:dispatch → claim + fire;状态 RUNNING
    sched.tick(task.id)
    task = svc.get(task.id)
    assert disp.node_id in {n.node_id for n in task.execution_graph.nodes}
    assert task.execution_graph.state.subtasks[disp.node_id].status is NodeStatus.RUNNING or \
        svc.get(task.id).execution_graph.state.subtasks[disp.node_id].status is NodeStatus.RUNNING
    # mock skill 回投:叶子验收 DONE
    _set_leaf_done(svc, task.id, disp.node_id)
    # tick3:无未闭合 → goal-verify PASS → VERIFIED + DONE
    sched.tick(task.id)
    task = svc.get(task.id)
    assert task.execution_graph.graph_status is GraphStatus.VERIFIED
    assert task.status is TaskStatus.DONE


# --- 搜推未匹配→分解→子各命中→exec-aggregate→终验 ------------------------


def test_v2_tick_search_miss_decompose_aggregate():
    svc = _svc()
    task = _v2_task(svc, acceptances=1)
    # n_search 未匹配;后续各 child bot_search 命中
    discover = FakeDiscover(hits={"n_bot_search": []}, default_hit=True)
    decomposer = FakeDecomposer()
    sched = _scheduler(svc, discover, decomposer)
    # tick1:规划链 DONE;n_search 未匹配 → decomposition 子(n_search_decomposition)
    sched.tick(task.id)
    task = svc.get(task.id)
    assert any(n.node_type is NodeType.DECOMPOSITION for n in task.execution_graph.nodes)
    # tick2:decomposition → 2 children bot_search(由 FakeDecomposer.decompose_subtasks)
    sched.tick(task.id)
    task = svc.get(task.id)
    child_searches = [n for n in task.execution_graph.nodes if n.node_type is NodeType.BOT_SEARCH and n.node_id != "n_bot_search"]
    assert child_searches  # 分解出子搜推
    # 各 child 搜推命中 → 各加 dispatch 子(多 tick 推进;循环 tick 直到无 PENDING)
    for _ in range(6):
        sched.tick(task.id)
    task = svc.get(task.id)
    leaf_disp = [n for n in task.execution_graph.nodes if n.node_type is NodeType.DISPATCH]
    assert leaf_disp
    # mock 所有叶子验收 DONE
    for d in leaf_disp:
        _set_leaf_done(svc, task.id, d.node_id)
    # tick:exec-aggregate 触发 + 终验
    for _ in range(4):
        sched.tick(task.id)
    task = svc.get(task.id)
    assert any(n.node_type is NodeType.EXEC_AGGREGATE for n in task.execution_graph.nodes)
    assert task.execution_graph.graph_status is GraphStatus.VERIFIED
    assert task.status is TaskStatus.DONE


# --- 递归上限→mark-hang→AWAITING_HUMAN_ACCEPT ------------------------------


def test_v2_tick_recursion_limit_mark_hang():
    svc = _svc()
    task = _v2_task(svc, acceptances=1)
    # n_search 未匹配;decompose 产 depth≥MAX children → mark-hang
    from agentclaw.community.core.task.services.scheduler_v2_ops import MAX_RECURSION_DEPTH

    discover = FakeDiscover(hits={"n_bot_search": []}, default_hit=True)
    decomposer = FakeDecomposer(depth_override=MAX_RECURSION_DEPTH)  # children depth=MAX → hang
    sched = _scheduler(svc, discover, decomposer)
    sched.tick(task.id)  # 规划链 + n_search 未匹配 → decomposition
    sched.tick(task.id)  # decomposition → children depth≥MAX → mark-hang + AWAITING_HUMAN_ACCEPT
    task = svc.get(task.id)
    assert any(n.node_type is NodeType.MARK_HANG for n in task.execution_graph.nodes)
    assert task.execution_graph.graph_status is GraphStatus.AWAITING_HUMAN_ACCEPT
    assert not any(n.node_type is NodeType.BBS_DISPATCH for n in task.execution_graph.nodes)  # 不直升 BBS
