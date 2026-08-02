"""图操作**层**集成测试(非 tick 驱动的端到端)。

定位 —— 测的是"图操作 + State + 纯函数"这一层,不是执行驱动链:

测到的真实实现(✓):
- 图操作写/读口 ``add_node`` / ``update_state`` / ``retrieve_state`` / ``_ensure_subtask_state``;
- ``mark_graph_status`` / ``_advance_phase`` / ``mark_terminal`` / ``_render_kind``;
- 纯函数 ``aggregate_verdict``;以及**真实** ``DecomposerService.decompose_subtasks``
  (E2E-5/6 靠它验 depth=父+1 的真实语义,非 mock)。
- 任务/分解**内容**用真实需求 case(交付用户登录功能,见 ``_login_case``)。

**故意绕过、不在此测**的(✗ 交给 ``test_e2e_tick``):
- **执行驱动**:节点序列由测试替 scheduler 用 ``add_node`` 手拼出来,**不走真实
  ``tick``/``_advance_node``/``_bot_search``/``_decomposition``/``_dispatch``。
- **skill 验收回投**:_set_subtask_done / _accept_fail_round **直接戳 Node/SubtaskState**
  (置 status、append AttemptedRecord/GapRecord),**绕过真实 ``on_event`` fold**
  (NODE_ACCEPTED/NODE_REJECTED/NODE_FAILED 的落态回写)。此处按"图操作层"职责只验
  写口产出,验收 fold 的真实通道在 ``test_e2e_tick`` 走 ``on_event`` 验。
- **聚合/终验**:`aggregate_verdict` 由测试**直接调用并断言**,不走真实
  ``_detect_and_aggregate`` / ``_maybe_goal_verify`` 的 tick 扫图触发。

即:这些用例验证"给定某图操作序列 + 某 State,图写口/读口/纯函数行为正确",用来隔离
锁定图操作层契约;**完整执行流程(谁驱动落节点、验收经事件回投折叠、tick 扫图聚合终验)
的端到端推演在 ``test_e2e_tick``**。

覆盖 E2E-1 happy / E2E-3 搜推未匹配→分解→中间层聚合→终验 / E2E-6 递归上限→hang→升 BBS /
E2E-7 不升→FAILED / E2E-2 协作群 / E2E-4 验收fail重路由命中 / E2E-5 fail→未匹配→递归拆解 /
E2E-8 goal-verify FAIL(BBS 前)回 gap / E2E-9 goal-verify FAIL(BBS 后)终态 / E2E-10 搜推先行
负断言 / E2E-11 并行混合分支 / E2E-12 看门狗节点级 escalation。
"""
from __future__ import annotations

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    AcceptanceCriteriaKind,
    AttemptedRecord,
    AttemptOutcome,
    GapRecord,
    GraphStatus,
    NodeType,
    Plan,
    RouteClass,
    RunMode,
    StateSemantics,
    SubTaskSpec,
    TaskGoal,
    TaskStatus,
)
from agentclaw.community.core.task.protocols import DispatchResult, aggregate_verdict
from agentclaw.community.core.task.services import TaskService
from agentclaw.community.plugins.community.task.in_memory_repos import (
    InMemoryTaskEventRepo,
    InMemoryTaskRepo,
)
from agentclaw.community.plugins.community.task.panel_publisher import (
    RecordingPanelPublisher,
)

from tests.community.core.task.services._login_case import ACCEPTANCES, OBJECTIVE, TOP_CHILDREN


def _service() -> TaskService:
    return TaskService(InMemoryTaskRepo(), InMemoryTaskEventRepo(), RecordingPanelPublisher())


def _task_with_graph(svc: TaskService, objective: str = OBJECTIVE, acceptances: int = 1):
    """建真实需求 task(目标=OBJECTIVE,真实验收)+ spawn_build_dag 图(根 subtask=目标)。"""
    t = svc.create(title=objective, background=objective)
    task = svc.get(t.id)
    acs = [
        AcceptanceCriteria(kind=AcceptanceCriteriaKind.OUTPUT, properties={"label": ACCEPTANCES[i].properties["label"]})
        if i < len(ACCEPTANCES)
        else AcceptanceCriteria(kind=AcceptanceCriteriaKind.OUTPUT, properties={"label": f"ac{i}"})
        for i in range(acceptances)
    ]
    task.spec.goal = TaskGoal(objective=objective, acceptances=acs)
    svc._task_repo.save(task)
    task = svc.get(t.id)
    # 根 subtask = 任务目标(真实内容)
    p = Plan(sub_tasks=[SubTaskSpec(node_id="n_root", spec=objective)], confidence=0.9)
    svc.finalize_plan(t.id, p)
    task = svc.get(t.id)
    svc.spawn_build_dag(task)
    return task


def _set_subtask_done(svc, task_id, node_id):
    """mock skill 回投:叶子 subtask 验收 PASS → SubtaskState.status=DONE(重读+存)。"""
    from agentclaw.community.core.task.domain.models import NodeStatus

    task = svc.get(task_id)
    st = svc._ensure_subtask_state(task, node_id)
    st.status = NodeStatus.DONE
    svc._task_repo.save(task)


def _accept_fail_round(svc, task_id, node_id, executor, round_, unmet=("ac0",)):
    """mock skill 回投:叶子 subtask 验收 FAIL。

    落 ``Node.attempted_executors``(本执行方 fail)+ ``SubtaskState.gap_records``
    (结构化 gap,供下轮 retrieve-state 带上轮上下文重路由)。节点身份不变。
    """
    from agentclaw.community.core.task.domain.models import NodeStatus

    task = svc.get(task_id)
    node = svc._find_node(task, node_id)
    assert node is not None
    node.attempted_executors.append(
        AttemptedRecord(executor_id=executor, paradigm=RunMode.SINGLE_BOT, round=round_, outcome=AttemptOutcome.FAIL)
    )
    st = svc._ensure_subtask_state(task, node_id)
    st.gap_records.append(GapRecord(node_id=node_id, round=round_, unmet_criteria=list(unmet), verdict=AttemptOutcome.FAIL))
    st.status = NodeStatus.FAILED
    svc._task_repo.save(task)


def _set_run_mode(svc, task_id, node_id, run_mode, bot_ids=None):
    """落 COOP_GROUP / SINGLE_BOT run_mode 到节点 + SubtaskState.execution_context。"""
    task = svc.get(task_id)
    node = svc._find_node(task, node_id)
    assert node is not None
    node.run_mode = run_mode
    if node.assignee is None:
        node.assignee = (bot_ids or ["group1"])[0]
    st = svc._ensure_subtask_state(task, node_id)
    st.execution_context["run_mode"] = run_mode.value
    if bot_ids:
        st.execution_context["group_members"] = list(bot_ids)
    svc._task_repo.save(task)


class _SpyDriver:
    """记录节点级 escalation(E2E-12,watchdog C5 → escalate_to_bbs)。"""

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


class _OwnerResolver:
    """mock owner 解析:E2E-2 group owner / task-owner。"""

    def __init__(self, group_owner="master-bot", task_owner="task-owner-bot"):
        self._group_owner = group_owner
        self._task_owner = task_owner

    def resolve_group_owner(self, group_id):
        return self._group_owner

    def resolve_task_owner(self, task_id):
        return self._task_owner


def _ancestors(node_id, edges):
    """图边上溯祖先集合(BFS)。"""
    parents = {e.from_node for e in edges if e.to_node == node_id}
    seen = set()
    stack = list(parents)
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        for e in edges:
            if e.to_node == cur and e.from_node not in seen:
                stack.append(e.from_node)
    return seen


# --- E2E-1 单 bot happy path(基线链路:搜推命中,不分解)----------------------

def test_e2e1_single_bot_happy_path():
    svc = _service()
    task = _task_with_graph(svc, acceptances=1)
    # 链路:recognition→clarify→execute-start→bot-search(搜“设计登录API契约”命中架构bot)
    # →dispatch→exec-accept DONE
    design = TOP_CHILDREN[0]
    svc.add_node(task.id, SubTaskSpec(node_id="n_rec", spec="识别需求:交付登录功能"), None, NodeType.RECOGNITION)
    svc.add_node(task.id, SubTaskSpec(node_id="n_clar", spec="澄清:补全登录验收"), "n_rec", NodeType.CLARIFY)
    svc.add_node(task.id, SubTaskSpec(node_id="n_exec", spec="启动执行"), "n_clar", NodeType.EXECUTE_START)
    svc.add_node(task.id, SubTaskSpec(node_id="n_search", spec=f"搜索执行方:{design.spec}"), "n_exec", NodeType.BOT_SEARCH)
    svc.add_node(task.id, SubTaskSpec(node_id="n_disp", spec=f"派发:{design.spec}"), "n_search", NodeType.DISPATCH, executor="arch-bot")
    leaf = "n_disp"
    _set_subtask_done(svc, task.id, leaf)  # mock:架构 bot 自判子任务 DONE
    # 任务终验:task-owner 读 State 聚合
    task = svc.get(task.id)
    verdict, unmet = aggregate_verdict(task.spec.goal.acceptances, [{"outcome": AttemptOutcome.PASS}])
    assert verdict is AttemptOutcome.PASS and unmet == []
    node_types = {n.node_id: n.node_type for n in task.execution_graph.nodes}
    assert node_types["n_search"] is NodeType.BOT_SEARCH
    assert node_types["n_disp"] is NodeType.DISPATCH
    assert task.execution_graph.state.subtasks[leaf].status.value == "done"


# --- E2E-3 搜推未匹配→分解→子任务各自命中→中间层聚合→终验(主链路)---------

def test_e2e3_search_miss_decompose_aggregate_verify():
    svc = _service()
    task = _task_with_graph(svc, acceptances=1)
    # 顶层搜推未匹配 → decomposition:3 个真实并行子任务
    svc.add_node(task.id, SubTaskSpec(node_id="n_search_top", spec=f"搜索执行方:{OBJECTIVE}(未匹配)"), "n_root", NodeType.BOT_SEARCH)
    svc.add_node(task.id, SubTaskSpec(node_id="n_decomp", spec=f"分解:{OBJECTIVE}"), "n_search_top", NodeType.DECOMPOSITION)
    children = [TOP_CHILDREN[0], TOP_CHILDREN[1], TOP_CHILDREN[2]]
    for c in children:
        svc.add_node(task.id, SubTaskSpec(node_id=c.node_id, spec=c.spec), "n_decomp", NodeType.BOT_SEARCH, executor=c.node_id)
    child_ids = [c.node_id for c in children]
    # 并行无 DEPENDENCY 边(只挂父 n_decomp)
    task = svc.get(task.id)
    child_set = set(child_ids)
    for e in task.execution_graph.edges:
        if e.from_node in child_set:
            assert e.to_node not in child_set
    for cid in child_ids:
        _set_subtask_done(svc, task.id, cid)  # 各真实子任务验收 PASS
    # 中间层聚合:父 owner 读 State 聚合
    child_results = [{"outcome": AttemptOutcome.PASS} for _ in child_ids]
    parent_acs = [AcceptanceCriteria(kind=AcceptanceCriteriaKind.OUTPUT, properties={"label": "登录功能交付"})]
    verdict, unmet = aggregate_verdict(parent_acs, child_results)
    assert verdict is AttemptOutcome.PASS
    # 父 subtask 闭合 → DONE;EXEC_AGGREGATE 节点已落图
    task = svc.get(task.id)
    parent = svc._ensure_subtask_state(task, "n_decomp")
    from agentclaw.community.core.task.domain.models import NodeStatus

    parent.status = NodeStatus.DONE
    svc._task_repo.save(task)
    svc.add_node(task.id, SubTaskSpec(node_id="n_agg", spec=f"聚合验收:{OBJECTIVE}"), "n_decomp", NodeType.EXEC_AGGREGATE)
    task = svc.get(task.id)
    agg = next(n for n in task.execution_graph.nodes if n.node_id == "n_agg")
    assert agg.node_type is NodeType.EXEC_AGGREGATE
    assert svc._render_kind(NodeType.EXEC_AGGREGATE) == "control-gate"
    assert parent.status is NodeStatus.DONE
    verdict, _ = aggregate_verdict(task.spec.goal.acceptances, [{"outcome": AttemptOutcome.PASS}])
    assert verdict is AttemptOutcome.PASS


# --- E2E-6 递归上限→hang→人确认升 BBS→同图延续 ----------------------------

def test_e2e6_recursion_limit_mark_hang_escalate_bbs():
    svc = _service()
    task = _task_with_graph(svc)
    from agentclaw.community.core.task.domain.models import TaskState
    from agentclaw.community.core.task.services.decomposer_service import DecomposerService

    state = TaskState(public={"__decompose_parent_depth__": 99})  # 父深度 99 → children depth=100(超 MAX=3)
    subs = DecomposerService().decompose_subtasks("搭建测试环境; 编写用例", state)
    assert all(s.depth >= 3 for s in subs)  # 触上限
    svc.add_node(task.id, SubTaskSpec(node_id="n_hang", spec="挂起等人确认:登录功能递归过深"), "n_root", NodeType.MARK_HANG)
    svc.mark_graph_status(svc.get(task.id), GraphStatus.AWAITING_HUMAN_ACCEPT)
    task = svc.get(task.id)
    assert task.execution_graph.graph_status is GraphStatus.AWAITING_HUMAN_ACCEPT
    hang_node = next(n for n in task.execution_graph.nodes if n.node_id == "n_hang")
    assert hang_node.node_type is NodeType.MARK_HANG
    assert svc._render_kind(NodeType.MARK_HANG) == "system-bridge"
    svc.mark_graph_status(task, GraphStatus.ON_PLAZA)
    task = svc.get(task.id)
    assert task.execution_graph.graph_status is GraphStatus.ON_PLAZA
    svc.add_node(task.id, SubTaskSpec(node_id="n_bbs", spec="BBS 继续执行登录功能交付"), "n_hang", NodeType.BBS_DISPATCH)
    task = svc.get(task.id)
    bbs = next(n for n in task.execution_graph.nodes if n.node_id == "n_bbs")
    assert bbs.node_type is NodeType.BBS_DISPATCH
    assert any(n.node_type is NodeType.MARK_HANG for n in task.execution_graph.nodes)
    assert any(n.node_type is NodeType.BBS_DISPATCH for n in task.execution_graph.nodes)


# --- E2E-7 不升→FAILED(三终止分支)-----------------------------------------

def test_e2e7_hang_no_escalate_failed():
    svc = _service()
    task = _task_with_graph(svc)
    svc.add_node(task.id, SubTaskSpec(node_id="n_hang", spec="挂起等人确认:登录功能递归过深"), "n_root", NodeType.MARK_HANG)
    svc.mark_graph_status(svc.get(task.id), GraphStatus.AWAITING_HUMAN_ACCEPT)
    task = svc.get(task.id)
    task.status = TaskStatus.FAILED  # 人确认不升 → FAILED 终态
    svc._task_repo.save(task)
    task = svc.get(task.id)
    assert task.status is TaskStatus.FAILED
    assert not any(n.node_type is NodeType.BBS_DISPATCH for n in task.execution_graph.nodes)


# --- E2E-2 协作群 happy path(COOP_GROUP,owner 解析)-----------------------

def test_e2e2_coop_group_happy_path():
    svc = _service()
    task = _task_with_graph(svc, acceptances=1)
    owner = _OwnerResolver(group_owner="master-bot", task_owner="task-owner-bot")
    impl = TOP_CHILDREN[1]  # 实现后端登录校验逻辑 → 后端协作组
    svc.add_node(task.id, SubTaskSpec(node_id="n_search", spec=f"搜索执行方:{impl.spec}→后端协作组"), "n_root", NodeType.BOT_SEARCH)
    svc.add_node(task.id, SubTaskSpec(node_id="n_disp", spec=f"派发协作组:{impl.spec}"), "n_search", NodeType.DISPATCH, executor="group1")
    _set_run_mode(svc, task.id, "n_disp", RunMode.COOP_GROUP, bot_ids=["master-bot", "member-b"])
    group_owner = owner.resolve_group_owner("group1")
    assert group_owner == "master-bot"
    _set_subtask_done(svc, task.id, "n_disp")  # 群 owner-bot 判验 DONE
    child_results = [{"outcome": AttemptOutcome.PASS, "judged_by": group_owner}]
    verdict, _ = aggregate_verdict(
        [AcceptanceCriteria(kind=AcceptanceCriteriaKind.OUTPUT, properties={"label": "登录后端校验"})],
        child_results,
    )
    assert verdict is AttemptOutcome.PASS
    verdict, _ = aggregate_verdict(task.spec.goal.acceptances, [{"outcome": AttemptOutcome.PASS}])
    assert verdict is AttemptOutcome.PASS
    task = svc.get(task.id)
    disp = next(n for n in task.execution_graph.nodes if n.node_id == "n_disp")
    assert disp.run_mode is RunMode.COOP_GROUP
    assert svc._render_kind(NodeType.DISPATCH) == "system-bridge"


# --- E2E-4 验收 fail → 重路由命中(节点身份不变)---------------------------

def test_e2e4_accept_fail_reroute_hit_node_identity_unchanged():
    svc = _service()
    task = _task_with_graph(svc, acceptances=1)
    design = TOP_CHILDREN[0]
    svc.add_node(task.id, SubTaskSpec(node_id="n_search", spec=f"搜索执行方:{design.spec}"), "n_root", NodeType.BOT_SEARCH)
    svc.add_node(task.id, SubTaskSpec(node_id="n_disp", spec=f"派发:{design.spec}"), "n_search", NodeType.DISPATCH, executor="arch-bot")
    leaf = "n_disp"
    _accept_fail_round(svc, task.id, leaf, executor="arch-bot", round_=1)  # 轮1 REJECTED
    st_view = svc.retrieve_state(task.id, leaf)
    assert st_view["subtask"]["gap_records"][-1]["unmet_criteria"] == ["ac0"]
    svc.update_state(task.id, leaf, {"execution_context": {"last_gap": "ac0"}}, StateSemantics.MERGE)
    # 同 node 再派给另一执行方(身份不变)
    task = svc.get(task.id)
    node = svc._find_node(task, leaf)
    node.attempted_executors.append(
        AttemptedRecord(executor_id="arch-bot-2", paradigm=RunMode.SINGLE_BOT, round=2, route_class=RouteClass.C2)
    )
    svc._task_repo.save(task)
    _set_subtask_done(svc, task.id, leaf)  # 轮2 DONE
    task = svc.get(task.id)
    node = svc._find_node(task, leaf)
    assert len(node.attempted_executors) == 2
    assert [a.executor_id for a in node.attempted_executors] == ["arch-bot", "arch-bot-2"]
    assert svc.retrieve_state(task.id, leaf)["subtask"]["execution_context"]["last_gap"] == "ac0"
    assert sum(1 for n in task.execution_graph.nodes if n.node_id == leaf) == 1
    verdict, _ = aggregate_verdict(task.spec.goal.acceptances, [{"outcome": AttemptOutcome.PASS}])
    assert verdict is AttemptOutcome.PASS


# --- E2E-5 验收 fail → 重路由未匹配 → 递归拆解(depth+1)--------------------

def test_e2e5_accept_fail_reroute_miss_recursive_decompose_depth_plus_one():
    svc = _service()
    task = _task_with_graph(svc, acceptances=1)
    from agentclaw.community.core.task.domain.models import TaskState
    from agentclaw.community.core.task.services.decomposer_service import DecomposerService

    design = TOP_CHILDREN[0]
    svc.add_node(task.id, SubTaskSpec(node_id="n_search", spec=f"搜索执行方:{design.spec}"), "n_root", NodeType.BOT_SEARCH)
    svc.add_node(task.id, SubTaskSpec(node_id="n_disp", spec=f"派发:{design.spec}"), "n_search", NodeType.DISPATCH, executor="arch-bot")
    _accept_fail_round(svc, task.id, "n_disp", executor="arch-bot", round_=1)
    # 重路由未匹配 → decomposition;父 depth 入 state → children depth=父+1
    task = svc.get(task.id)
    parent_st = svc._ensure_subtask_state(task, "n_disp")
    parent_depth = parent_st.depth  # 根层 = 0
    state = TaskState(public={"__decompose_parent_depth__": parent_depth})
    subs = DecomposerService().decompose_subtasks("定义入参; 定义出参; 定义错误码", state)
    assert len(subs) == 3
    assert all(s.depth == parent_depth + 1 for s in subs)
    svc.add_node(task.id, SubTaskSpec(node_id="n_search2", spec=f"重路由搜索:{design.spec}(未匹配)"), "n_disp", NodeType.BOT_SEARCH)
    svc.add_node(task.id, SubTaskSpec(node_id="n_decomp2", spec=f"分解:{design.spec}"), "n_search2", NodeType.DECOMPOSITION)
    for s in subs:
        svc.add_node(task.id, SubTaskSpec(node_id=s.node_id, spec=s.spec, depth=s.depth), "n_decomp2", NodeType.BOT_SEARCH, executor=s.node_id)
    task = svc.get(task.id)
    children = [s.node_id for s in subs]
    for cid in children:
        assert task.execution_graph.state.subtasks[cid].depth == parent_depth + 1


# --- E2E-8 goal-verify FAIL(BBS 前)→ 回 gap(不直接 FAILED)--------------

def test_e2e8_goal_verify_fail_before_bbs_loops_gap_not_failed():
    svc = _service()
    task = _task_with_graph(svc, acceptances=1)
    svc.add_node(task.id, SubTaskSpec(node_id="n_agg", spec=f"聚合验收:{OBJECTIVE}"), "n_root", NodeType.EXEC_AGGREGATE)
    _set_subtask_done(svc, task.id, "n_agg")
    task = svc.get(task.id)
    assert task.execution_graph.graph_status is GraphStatus.ON_PLAZA
    verdict, unmet = aggregate_verdict(task.spec.goal.acceptances, [{"outcome": AttemptOutcome.FAIL}])
    assert verdict is AttemptOutcome.FAIL and unmet
    # 回 gap:REVIEWING → EXECUTING(新一轮),不落 FAILED
    svc._advance_phase(task, TaskStatus.EXECUTING)
    svc._advance_phase(task, TaskStatus.REVIEWING)
    assert task.status is TaskStatus.REVIEWING
    svc._advance_phase(task, TaskStatus.EXECUTING)
    assert task.status is TaskStatus.EXECUTING
    assert task.status is not TaskStatus.FAILED
    svc.add_node(task.id, SubTaskSpec(node_id="n_search_gap", spec=f"gap 新一轮搜索:{OBJECTIVE}"), "n_agg", NodeType.BOT_SEARCH)
    assert any(n.node_id == "n_search_gap" for n in svc.get(task.id).execution_graph.nodes)


# --- E2E-9 goal-verify FAIL(BBS 后)→ FAILED 终态 ------------------------

def test_e2e9_goal_verify_fail_after_bbs_failed_terminal():
    svc = _service()
    task = _task_with_graph(svc, acceptances=1)
    svc.add_node(task.id, SubTaskSpec(node_id="n_hang", spec="挂起等人确认:登录功能递归过深"), "n_root", NodeType.MARK_HANG)
    svc.mark_graph_status(svc.get(task.id), GraphStatus.AWAITING_HUMAN_ACCEPT)
    svc.mark_graph_status(svc.get(task.id), GraphStatus.ON_PLAZA)
    svc.add_node(task.id, SubTaskSpec(node_id="n_bbs", spec="BBS 继续执行登录功能交付"), "n_hang", NodeType.BBS_DISPATCH)
    svc.add_node(task.id, SubTaskSpec(node_id="n_agg", spec="BBS 阶段聚合验收"), "n_bbs", NodeType.EXEC_AGGREGATE)
    _set_subtask_done(svc, task.id, "n_agg")
    verdict, _ = aggregate_verdict(task.spec.goal.acceptances, [{"outcome": AttemptOutcome.FAIL}])
    assert verdict is AttemptOutcome.FAIL
    task = svc.get(task.id)
    svc._advance_phase(task, TaskStatus.EXECUTING)
    svc.mark_terminal(task, TaskStatus.FAILED)
    task = svc.get(task.id)
    assert task.status is TaskStatus.FAILED
    bbs_before = sum(1 for n in task.execution_graph.nodes if n.node_type is NodeType.BBS_DISPATCH)
    assert bbs_before == 1  # 不再回环 / 不再 escalation


# --- E2E-10 搜推先行约束(负向断言)---------------------------------------

def test_e2e10_search_first_invariant():
    """搜推先行(FR-GRAPH-14):DECOMPOSITION 必有先前 BOT_SEARCH 祖先;搜推命中无分解。"""
    svc = _service()
    task = _task_with_graph(svc, acceptances=1)
    design = TOP_CHILDREN[0]
    # 命中:bot-search 命中 → dispatch,无 DECOMPOSITION
    svc.add_node(task.id, SubTaskSpec(node_id="n_search_hit", spec=f"搜索执行方:{design.spec}→命中"), "n_root", NodeType.BOT_SEARCH)
    svc.add_node(task.id, SubTaskSpec(node_id="n_disp_hit", spec=f"派发:{design.spec}"), "n_search_hit", NodeType.DISPATCH, executor="arch-bot")
    task = svc.get(task.id)
    assert not any(n.node_type is NodeType.DECOMPOSITION for n in task.execution_graph.nodes)
    # 未匹配:bot-search 未匹配 → DECOMPOSITION;DECOMPOSITION 有 BOT_SEARCH 祖先
    tests = TOP_CHILDREN[2]
    svc.add_node(task.id, SubTaskSpec(node_id="n_search_miss", spec=f"搜索执行方:{tests.spec}(未匹配)"), "n_disp_hit", NodeType.BOT_SEARCH)
    svc.add_node(task.id, SubTaskSpec(node_id="n_decomp", spec=f"分解:{tests.spec}"), "n_search_miss", NodeType.DECOMPOSITION)
    task = svc.get(task.id)
    edges = task.execution_graph.edges
    decomp = next(n for n in task.execution_graph.nodes if n.node_id == "n_decomp")
    anc = _ancestors(decomp.node_id, edges)
    bot_search_ancestors = {
        n.node_id for n in task.execution_graph.nodes
        if n.node_type is NodeType.BOT_SEARCH and n.node_id in anc
    }
    assert bot_search_ancestors  # 搜推先行
    assert not any(n.node_type is NodeType.BBS_DISPATCH for n in task.execution_graph.nodes)
    assert not any(n.node_type is NodeType.MARK_HANG for n in task.execution_graph.nodes)


# --- E2E-11 并行无依赖 + 混合分支(同层多子任务不同走向)-------------------

def test_e2e11_parallel_mixed_branches_no_interdependency():
    svc = _service()
    task = _task_with_graph(svc, acceptances=1)
    svc.add_node(task.id, SubTaskSpec(node_id="n_search", spec=f"搜索执行方:{OBJECTIVE}(未匹配)"), "n_root", NodeType.BOT_SEARCH)
    svc.add_node(task.id, SubTaskSpec(node_id="n_decomp", spec=f"分解:{OBJECTIVE}"), "n_search", NodeType.DECOMPOSITION)
    children = ["c_design", "c_impl", "c_test"]
    child_specs = {
        "c_design": TOP_CHILDREN[0].spec,
        "c_impl": TOP_CHILDREN[1].spec,
        "c_test": TOP_CHILDREN[2].spec,
    }
    for cid in children:
        svc.add_node(task.id, SubTaskSpec(node_id=cid, spec=child_specs[cid]), "n_decomp", NodeType.BOT_SEARCH, executor=cid)
    task = svc.get(task.id)
    child_set = set(children)
    for e in task.execution_graph.edges:
        assert not (e.from_node in child_set and e.to_node in child_set)
    # 三分支:c_design 命中直派;c_impl(后端)协作;c_test 未匹配再拆 + c 验收 fail 重路由
    svc.add_node(task.id, SubTaskSpec(node_id="c_design_disp", spec=f"派发:{child_specs['c_design']}"), "c_design", NodeType.DISPATCH, executor="arch-bot")
    svc.add_node(task.id, SubTaskSpec(node_id="c_impl_disp", spec=f"协作派发:{child_specs['c_impl']}"), "c_impl", NodeType.DISPATCH, executor="backend-group")
    svc.add_node(task.id, SubTaskSpec(node_id="c_test_dec", spec=f"分解:{child_specs['c_test']}"), "c_test", NodeType.DECOMPOSITION)
    svc.add_node(task.id, SubTaskSpec(node_id="c_test_c1", spec="搭建测试环境与mock用户数据"), "c_test_dec", NodeType.BOT_SEARCH, executor="test-bot")
    _accept_fail_round(svc, task.id, "c_impl_disp", executor="backend-group", round_=1)
    svc.add_node(task.id, SubTaskSpec(node_id="c_impl_search2", spec=f"重路由搜索:{child_specs['c_impl']}"), "c_impl_disp", NodeType.BOT_SEARCH)
    svc.add_node(task.id, SubTaskSpec(node_id="c_impl_disp2", spec=f"重派:{child_specs['c_impl']}"), "c_impl_search2", NodeType.DISPATCH, executor="backend-group-2")
    for leaf in ("c_design_disp", "c_test_c1", "c_impl_disp2"):
        _set_subtask_done(svc, task.id, leaf)
    task = svc.get(task.id)
    unmet_children = [
        {"outcome": AttemptOutcome.PASS} for _ in ("c_design_disp", "c_test_c1", "c_impl_disp2")
    ]
    parent_acs = [AcceptanceCriteria(kind=AcceptanceCriteriaKind.OUTPUT, properties={"label": "登录功能交付"})]
    verdict, _ = aggregate_verdict(parent_acs, unmet_children)
    assert verdict is AttemptOutcome.PASS
    for e in task.execution_graph.edges:
        assert not (e.from_node in child_set and e.to_node in child_set)
    types_present = {n.node_type for n in task.execution_graph.nodes}
    assert NodeType.DECOMPOSITION in types_present
    assert NodeType.DISPATCH in types_present


# --- E2E-12 看门狗超时 → probe → redrive → 节点级 escalation(C5)---------

def test_e2e12_watchdog_probe_redrive_node_escalation():
    svc = _service()
    task = _task_with_graph(svc, acceptances=1)
    driver = _SpyDriver()
    design = TOP_CHILDREN[0]
    svc.add_node(task.id, SubTaskSpec(node_id="n_search", spec=f"搜索执行方:{design.spec}"), "n_root", NodeType.BOT_SEARCH)
    svc.add_node(task.id, SubTaskSpec(node_id="n_disp", spec=f"派发:{design.spec}"), "n_search", NodeType.DISPATCH, executor="arch-bot")
    # 叶子长 RUNNING:watchdog 计数留 Node.properties
    leaf_task = svc.get(task.id)
    leaf_node = svc._find_node(leaf_task, "n_disp")
    leaf_node.properties["running_ticks"] = leaf_node.properties.get("running_ticks", 0) + 3
    leaf_node.properties["probe_count"] = leaf_node.properties.get("probe_count", 0) + 1
    leaf_node.properties["redrive_count"] = leaf_node.properties.get("redrive_count", 0) + 1
    svc._task_repo.save(leaf_task)
    # probe 失败 → C5 节点级 escalation(区别于 goal-FAIL 的 AWAITING_HUMAN_ACCEPT)
    result = driver.escalate_to_bbs(task.id, reason="watchdog C5 timeout on 派发设计登录API契约")
    assert result.run_mode is RunMode.BBS
    assert driver.escalated == ["watchdog C5 timeout on 派发设计登录API契约"]
    saved = svc.get(task.id)
    saved_node = svc._find_node(saved, "n_disp")
    assert saved_node.properties["running_ticks"] >= 3
    assert saved_node.properties["probe_count"] >= 1
    assert saved_node.properties["redrive_count"] >= 1
    assert saved.execution_graph.graph_status is GraphStatus.ON_PLAZA