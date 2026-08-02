"""v2 端到端执行链路(图操作层,tasks T-25/T-27/T-30 代表;plan §20)。

注:Phase 4 scheduler tick 的 action-node 重写尚未落地,故 E2E 此处通过**已实现的
v2 图操作写口**(``add_node``/``update_state``/``retrieve_state``)+ ``aggregate_verdict``
+ ``mark_graph_status`` 串起执行链路,搜推/skill 回投以"直接置 SubtaskState + 调纯函数"
mock(对齐 plan §19.1 的"skill 判验 = 编程化置结果")。完整 tick 驱动的 E2E 见
Phase 4 后补。覆盖 E2E-1 happy / E2E-3 搜推未匹配→分解→中间层聚合→终验 / E2E-6 递归上限→hang→升 BBS。
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


def _service() -> TaskService:
    return TaskService(InMemoryTaskRepo(), InMemoryTaskEventRepo(), RecordingPanelPublisher())


def _task_with_graph(svc: TaskService, objective: str = "obj", acceptances: int = 1):
    """建 task(带 goal)+ 空图(spawn_build_dag 出 graph;占位 subtask n_root)。"""
    t = svc.create(title="t", background=objective)
    task = svc.get(t.id)
    acs = [
        AcceptanceCriteria(kind=AcceptanceCriteriaKind.OUTPUT, properties={"label": f"ac{i}"})
        for i in range(acceptances)
    ]
    task.spec.goal = TaskGoal(objective=objective, acceptances=acs)
    svc._task_repo.save(task)
    task = svc.get(t.id)
    p = Plan(sub_tasks=[SubTaskSpec(node_id="n_root", spec="root subtask")], confidence=0.9)
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


# --- E2E-1 单 bot happy path(基线链路)----------------------------------------


def test_e2e1_single_bot_happy_path():
    svc = _service()
    task = _task_with_graph(svc, objective="do one thing", acceptances=1)
    # 链路:recognition→clarify→execute-start→bot-search 命中→dispatch→exec-accept DONE
    svc.add_node(task.id, SubTaskSpec(node_id="n_rec", spec="recog"), None, NodeType.RECOGNITION)
    svc.add_node(task.id, SubTaskSpec(node_id="n_clar", spec="clarify"), "n_rec", NodeType.CLARIFY)
    svc.add_node(task.id, SubTaskSpec(node_id="n_exec", spec="execute"), "n_clar", NodeType.EXECUTE_START)
    svc.add_node(task.id, SubTaskSpec(node_id="n_search", spec="bot-search→bot1"), "n_exec", NodeType.BOT_SEARCH)
    svc.add_node(task.id, SubTaskSpec(node_id="n_disp", spec="dispatch→bot1"), "n_search", NodeType.DISPATCH, executor="bot1")
    leaf = "n_disp"
    # mock:bot1 自判子任务 DONE(单 bot=self 验收)
    _set_subtask_done(svc, task.id, leaf)
    # 任务终验:task-owner 读 State(根 subtask 产出验收 + Task goal.acceptances)聚合
    task = svc.get(task.id)
    child_results = [{"outcome": AttemptOutcome.PASS}]
    verdict, unmet = aggregate_verdict(task.spec.goal.acceptances, child_results)
    assert verdict is AttemptOutcome.PASS and unmet == []
    # 图拓扑 + render_kind
    node_types = {n.node_id: n.node_type for n in task.execution_graph.nodes}
    assert node_types["n_search"] is NodeType.BOT_SEARCH
    assert node_types["n_disp"] is NodeType.DISPATCH
    assert task.execution_graph.state.subtasks[leaf].status.value == "done"


# --- E2E-3 搜推未匹配→分解→子任务各自命中→中间层聚合→终验(主链路)-------------


def test_e2e3_search_miss_decompose_aggregate_verify():
    svc = _service()
    task = _task_with_graph(svc, objective="build feature X", acceptances=1)
    # 顶层搜推未匹配 → decomposition:3 children 并行
    svc.add_node(task.id, SubTaskSpec(node_id="n_search_top", spec="bot-search→未匹配"), "n_root", NodeType.BOT_SEARCH)
    svc.add_node(task.id, SubTaskSpec(node_id="n_decomp", spec="decomposition"), "n_search_top", NodeType.DECOMPOSITION)
    children = ["n_c1", "n_c2", "n_c3"]
    for cid in children:
        svc.add_node(task.id, SubTaskSpec(node_id=cid, spec=f"child {cid}"), "n_decomp", NodeType.BOT_SEARCH, executor=cid)
    # children 并行无 DEPENDENCY 边(只挂父 n_decomp),断言同层无边互连
    task = svc.get(task.id)
    child_set = set(children)
    for e in task.execution_graph.edges:
        if e.from_node in child_set:
            assert e.to_node not in child_set
    # mock:各 child 验收 PASS
    for cid in children:
        _set_subtask_done(svc, task.id, cid)
    # 中间层聚合:父 owner 读 State(children 产出验收 + 父 targets_acceptance)聚合
    child_results = [{"outcome": AttemptOutcome.PASS} for _ in children]
    parent_acs = [AcceptanceCriteria(kind=AcceptanceCriteriaKind.OUTPUT, properties={"label": "parent"})]
    verdict, unmet = aggregate_verdict(parent_acs, child_results)
    assert verdict is AttemptOutcome.PASS
    # 父 subtask 闭合 → SubtaskState.status=DONE;EXEC_AGGREGATE 节点已落图
    task = svc.get(task.id)
    parent = svc._ensure_subtask_state(task, "n_decomp")
    from agentclaw.community.core.task.domain.models import NodeStatus

    parent.status = NodeStatus.DONE
    svc._task_repo.save(task)
    svc.add_node(task.id, SubTaskSpec(node_id="n_agg", spec="exec-aggregate"), "n_decomp", NodeType.EXEC_AGGREGATE)
    task = svc.get(task.id)
    agg = next(n for n in task.execution_graph.nodes if n.node_id == "n_agg")
    assert agg.node_type is NodeType.EXEC_AGGREGATE
    assert svc._render_kind(NodeType.EXEC_AGGREGATE) == "control-gate"
    assert parent.status is NodeStatus.DONE
    # 终验:goal-verify
    verdict, _ = aggregate_verdict(task.spec.goal.acceptances, [{"outcome": AttemptOutcome.PASS}])
    assert verdict is AttemptOutcome.PASS


# --- E2E-6 递归上限→hang→人确认升 BBS→同图延续 --------------------------------


def test_e2e6_recursion_limit_mark_hang_escalate_bbs():
    svc = _service()
    task = _task_with_graph(svc)
    # decomposition 产出 children depth≥MAX(=3)→ 拒 add_node → MARK_HANG
    from agentclaw.community.core.task.domain.models import TaskState
    from agentclaw.community.core.task.services.decomposer_service import DecomposerService

    state = TaskState(public={"__decompose_parent_depth__": 99})  # 父深度 99 → children depth=100(超 MAX=3)
    subs = DecomposerService().decompose_subtasks("a; b", state)
    assert all(s.depth >= 3 for s in subs)  # 触上限
    # 系统落 MARK_HANG + graph AWAITING_HUMAN_ACCEPT(不直接升 BBS)
    svc.add_node(task.id, SubTaskSpec(node_id="n_hang", spec="mark-hang"), "n_root", NodeType.MARK_HANG)
    svc.mark_graph_status(svc.get(task.id), GraphStatus.AWAITING_HUMAN_ACCEPT)
    task = svc.get(task.id)
    assert task.execution_graph.graph_status is GraphStatus.AWAITING_HUMAN_ACCEPT
    hang_node = next(n for n in task.execution_graph.nodes if n.node_id == "n_hang")
    assert hang_node.node_type is NodeType.MARK_HANG
    assert svc._render_kind(NodeType.MARK_HANG) == "system-bridge"
    # 人确认升 BBS → AWAITING_HUMAN_ACCEPT → ON_PLAZA(guard 通过)→ BBS_DISPATCH
    svc.mark_graph_status(task, GraphStatus.ON_PLAZA)
    task = svc.get(task.id)
    assert task.execution_graph.graph_status is GraphStatus.ON_PLAZA
    svc.add_node(task.id, SubTaskSpec(node_id="n_bbs", spec="bbs-dispatch"), "n_hang", NodeType.BBS_DISPATCH)
    task = svc.get(task.id)
    bbs = next(n for n in task.execution_graph.nodes if n.node_id == "n_bbs")
    assert bbs.node_type is NodeType.BBS_DISPATCH
    # 同图延续(同一 TaskExecutionGraph,无新图)
    assert any(n.node_type is NodeType.MARK_HANG for n in task.execution_graph.nodes)
    assert any(n.node_type is NodeType.BBS_DISPATCH for n in task.execution_graph.nodes)


# --- E2E-7 不升→FAILED(三终止分支)-------------------------------------------


def test_e2e7_hang_no_escalate_failed():
    svc = _service()
    task = _task_with_graph(svc)
    svc.add_node(task.id, SubTaskSpec(node_id="n_hang", spec="mark-hang"), "n_root", NodeType.MARK_HANG)
    svc.mark_graph_status(svc.get(task.id), GraphStatus.AWAITING_HUMAN_ACCEPT)
    # 人确认不升 → task FAILED(终态);断言无 BBS_DISPATCH
    task = svc.get(task.id)
    task.status = TaskStatus.FAILED
    svc._task_repo.save(task)
    task = svc.get(task.id)
    assert task.status is TaskStatus.FAILED
    assert not any(n.node_type is NodeType.BBS_DISPATCH for n in task.execution_graph.nodes)


# --- E2E-2 协作群 happy path(COOP_GROUP,owner 解析)-------------------------


def test_e2e2_coop_group_happy_path():
    svc = _service()
    task = _task_with_graph(svc, objective="multi-bot task", acceptances=1)
    owner = _OwnerResolver(group_owner="master-bot", task_owner="task-owner-bot")
    # 搜推 C3(群 cover≥1)→ dispatch 群;节点 run_mode=COOP_GROUP
    svc.add_node(task.id, SubTaskSpec(node_id="n_search", spec="bot-search→群1"), "n_root", NodeType.BOT_SEARCH)
    svc.add_node(task.id, SubTaskSpec(node_id="n_disp", spec="dispatch→群1"), "n_search", NodeType.DISPATCH, executor="group1")
    _set_run_mode(svc, task.id, "n_disp", RunMode.COOP_GROUP, bot_ids=["master-bot", "member-b"])
    # 群 owner-bot 判验子任务(master-bot)=exec-accept DONE
    group_owner = owner.resolve_group_owner("group1")
    assert group_owner == "master-bot"
    _set_subtask_done(svc, task.id, "n_disp")
    # 验收归 group owner;终验归 task-owner(plan §4.2 / spec O-7②)
    child_results = [{"outcome": AttemptOutcome.PASS, "judged_by": group_owner}]
    verdict, _ = aggregate_verdict(
        [AcceptanceCriteria(kind=AcceptanceCriteriaKind.OUTPUT, properties={"label": "group"})],
        child_results,
    )
    assert verdict is AttemptOutcome.PASS
    verdict, _ = aggregate_verdict(task.spec.goal.acceptances, [{"outcome": AttemptOutcome.PASS}])
    assert verdict is AttemptOutcome.PASS
    task = svc.get(task.id)
    disp = next(n for n in task.execution_graph.nodes if n.node_id == "n_disp")
    assert disp.run_mode is RunMode.COOP_GROUP
    assert svc._render_kind(NodeType.DISPATCH) == "system-bridge"


# --- E2E-4 验收 fail → 重路由命中(节点身份不变)-----------------------------


def test_e2e4_accept_fail_reroute_hit_node_identity_unchanged():
    svc = _service()
    task = _task_with_graph(svc, objective="do thing", acceptances=1)
    svc.add_node(task.id, SubTaskSpec(node_id="n_search", spec="bot-search→bot1"), "n_root", NodeType.BOT_SEARCH)
    svc.add_node(task.id, SubTaskSpec(node_id="n_disp", spec="dispatch→bot1"), "n_search", NodeType.DISPATCH, executor="bot1")
    leaf = "n_disp"
    # 轮1:exec-accept REJECTED(gap 落 SubtaskState.gap_records + Node.attempted_executors)
    _accept_fail_round(svc, task.id, leaf, executor="bot1", round_=1)
    # 重路由:retrieve-state 带上轮 gap → 重搜命中 botxxx → 同 node redispatch(身份不变)
    st_view = svc.retrieve_state(task.id, leaf)
    assert st_view["subtask"]["gap_records"][-1]["unmet_criteria"] == ["ac0"]
    svc.update_state(task.id, leaf, {"execution_context": {"last_gap": "ac0"}}, StateSemantics.MERGE)
    # 同 node_id 再派给 botxxx(不经 add_node 建新节点 → 身份不变)
    task = svc.get(task.id)
    node = svc._find_node(task, leaf)
    node.attempted_executors.append(
        AttemptedRecord(executor_id="botxxx", paradigm=RunMode.SINGLE_BOT, round=2, route_class=RouteClass.C2)
    )
    svc._task_repo.save(task)
    # 轮2:exec-accept DONE
    _set_subtask_done(svc, task.id, leaf)
    task = svc.get(task.id)
    node = svc._find_node(task, leaf)
    assert len(node.attempted_executors) == 2  # bot1(fail) + botxxx(hit)
    assert [a.executor_id for a in node.attempted_executors] == ["bot1", "botxxx"]
    # 检索上下文含上轮 gap
    assert svc.retrieve_state(task.id, leaf)["subtask"]["execution_context"]["last_gap"] == "ac0"
    # 图里只有一个 n_disp 节点(身份不变,非新节点)
    assert sum(1 for n in task.execution_graph.nodes if n.node_id == leaf) == 1
    # 终验 PASS
    verdict, _ = aggregate_verdict(task.spec.goal.acceptances, [{"outcome": AttemptOutcome.PASS}])
    assert verdict is AttemptOutcome.PASS


# --- E2E-5 验收 fail → 重路由未匹配 → 递归拆解(depth+1)----------------------


def test_e2e5_accept_fail_reroute_miss_recursive_decompose_depth_plus_one():
    svc = _service()
    task = _task_with_graph(svc, objective="deep task", acceptances=1)
    from agentclaw.community.core.task.domain.models import TaskState
    from agentclaw.community.core.task.services.decomposer_service import DecomposerService

    svc.add_node(task.id, SubTaskSpec(node_id="n_search", spec="bot-search→bot1"), "n_root", NodeType.BOT_SEARCH)
    svc.add_node(task.id, SubTaskSpec(node_id="n_disp", spec="dispatch→bot1"), "n_search", NodeType.DISPATCH, executor="bot1")
    _accept_fail_round(svc, task.id, "n_disp", executor="bot1", round_=1)
    # 重路由未匹配 → decomposition;父 n_disp depth 设入 state → children depth=父+1
    task = svc.get(task.id)
    parent_st = svc._ensure_subtask_state(task, "n_disp")
    parent_depth = parent_st.depth  # 根层 = 0
    state = TaskState(public={"__decompose_parent_depth__": parent_depth})
    subs = DecomposerService().decompose_subtasks("s5-1; s5-2; s5-3", state)
    assert len(subs) == 3
    assert all(s.depth == parent_depth + 1 for s in subs)  # depth+1
    # 落 DECOMPOSITION 节点 + 3 children(挂 n_disp 的兄弟级,父为重路由探测节点)
    svc.add_node(task.id, SubTaskSpec(node_id="n_search2", spec="reroute bot-search→未匹配"), "n_disp", NodeType.BOT_SEARCH)
    svc.add_node(task.id, SubTaskSpec(node_id="n_decomp2", spec="decomposition"), "n_search2", NodeType.DECOMPOSITION)
    for s in subs:
        svc.add_node(task.id, SubTaskSpec(node_id=s.node_id, spec=s.spec, depth=s.depth), "n_decomp2", NodeType.BOT_SEARCH, executor=s.node_id)
    task = svc.get(task.id)
    children = [s.node_id for s in subs]
    for cid in children:
        assert task.execution_graph.state.subtasks[cid].depth == parent_depth + 1


# --- E2E-8 goal-verify FAIL(BBS 前)→ 回 gap(不直接 FAILED)----------------


def test_e2e8_goal_verify_fail_before_bbs_loops_gap_not_failed():
    svc = _service()
    task = _task_with_graph(svc, objective="verify-able goal", acceptances=1)
    # 根 subtask 聚合 DONE
    svc.add_node(task.id, SubTaskSpec(node_id="n_agg", spec="exec-aggregate"), "n_root", NodeType.EXEC_AGGREGATE)
    _set_subtask_done(svc, task.id, "n_agg")
    # graph 未升 BBS(ON_PLAZA);goal-verify REJECTED
    task = svc.get(task.id)
    assert task.execution_graph.graph_status is GraphStatus.ON_PLAZA
    verdict, unmet = aggregate_verdict(task.spec.goal.acceptances, [{"outcome": AttemptOutcome.FAIL}])
    assert verdict is AttemptOutcome.FAIL and unmet  # goal 未达成
    # 回 gap:task 进 REVIEWING(终验)→ 回 EXECUTING(新一轮),不落 FAILED(O-P2/FR-LOOP-01)
    svc._advance_phase(task, TaskStatus.EXECUTING)  # EXECUTING→EXECUTING(loop_round++)合法自环
    svc._advance_phase(task, TaskStatus.REVIEWING)  # 进终验
    assert task.status is TaskStatus.REVIEWING
    # 终验 FAIL → 回 gap(REVIEWING→EXECUTING,合法边),非 FAILED
    svc._advance_phase(task, TaskStatus.EXECUTING)
    assert task.status is TaskStatus.EXECUTING  # 回环继跑,触发新一轮 bot-search/decomposition
    assert task.status is not TaskStatus.FAILED
    # 触新一轮探测(decomposition 节点续图)— 断言可续图非终态
    svc.add_node(task.id, SubTaskSpec(node_id="n_search_gap", spec="gap bot-search"), "n_agg", NodeType.BOT_SEARCH)
    assert any(n.node_id == "n_search_gap" for n in svc.get(task.id).execution_graph.nodes)


# --- E2E-9 goal-verify FAIL(BBS 后)→ FAILED 终态 --------------------------


def test_e2e9_goal_verify_fail_after_bbs_failed_terminal():
    svc = _service()
    task = _task_with_graph(svc, objective="bbs goal", acceptances=1)
    # 已升 BBS:MARK_HANG → AWAITING_HUMAN_ACCEPT → ON_PLAZA → BBS_DISPATCH
    svc.add_node(task.id, SubTaskSpec(node_id="n_hang", spec="mark-hang"), "n_root", NodeType.MARK_HANG)
    svc.mark_graph_status(svc.get(task.id), GraphStatus.AWAITING_HUMAN_ACCEPT)
    svc.mark_graph_status(svc.get(task.id), GraphStatus.ON_PLAZA)
    svc.add_node(task.id, SubTaskSpec(node_id="n_bbs", spec="bbs-dispatch"), "n_hang", NodeType.BBS_DISPATCH)
    svc.add_node(task.id, SubTaskSpec(node_id="n_agg", spec="bbs exec-aggregate"), "n_bbs", NodeType.EXEC_AGGREGATE)
    _set_subtask_done(svc, task.id, "n_agg")
    # BBS 阶段 goal-verify REJECTED(graph 已 ON_PLAZA)→ Task FAILED 终态
    verdict, _ = aggregate_verdict(task.spec.goal.acceptances, [{"outcome": AttemptOutcome.FAIL}])
    assert verdict is AttemptOutcome.FAIL
    task = svc.get(task.id)
    # task 须在 EXECUTING(已运行经 BBS)方能落 FAILED 终态
    svc._advance_phase(task, TaskStatus.EXECUTING)
    svc.mark_terminal(task, TaskStatus.FAILED)
    task = svc.get(task.id)
    assert task.status is TaskStatus.FAILED  # 终态
    bbs_before = sum(1 for n in task.execution_graph.nodes if n.node_type is NodeType.BBS_DISPATCH)
    # 不再回环 / 不再 escalation:无第二个 BBS_DISPATCH
    assert bbs_before == 1


# --- E2E-10 搜推先行约束(负向断言)-----------------------------------------


def test_e2e10_search_first_invariant():
    """搜推先行(FR-GRAPH-14):DECOMPOSITION 必有先前 BOT_SEARCH 祖先;搜推命中无分解。"""
    svc = _service()
    task = _task_with_graph(svc, objective="search-first", acceptances=1)
    # 命中:bot-search 命中 → dispatch,无 DECOMPOSITION
    svc.add_node(task.id, SubTaskSpec(node_id="n_search_hit", spec="bot-search→bot1"), "n_root", NodeType.BOT_SEARCH)
    svc.add_node(task.id, SubTaskSpec(node_id="n_disp_hit", spec="dispatch→bot1"), "n_search_hit", NodeType.DISPATCH, executor="bot1")
    task = svc.get(task.id)
    assert not any(n.node_type is NodeType.DECOMPOSITION for n in task.execution_graph.nodes)
    # 未匹配:bot-search 未匹配 → DECOMPOSITION;断言 DECOMPOSITION 有 BOT_SEARCH 祖先
    svc.add_node(task.id, SubTaskSpec(node_id="n_search_miss", spec="bot-search→未匹配"), "n_disp_hit", NodeType.BOT_SEARCH)
    svc.add_node(task.id, SubTaskSpec(node_id="n_decomp", spec="decomposition"), "n_search_miss", NodeType.DECOMPOSITION)
    task = svc.get(task.id)
    edges = task.execution_graph.edges
    decomp = next(n for n in task.execution_graph.nodes if n.node_id == "n_decomp")
    anc = _ancestors(decomp.node_id, edges)
    bot_search_ancestors = {
        n.node_id for n in task.execution_graph.nodes
        if n.node_type is NodeType.BOT_SEARCH and n.node_id in anc
    }
    assert bot_search_ancestors  # 搜推先行:分解前必有搜推
    # 未匹配且 depth<MAX → 不落 BBS_DISPATCH / MARK_HANG,走 decomposition
    assert not any(n.node_type is NodeType.BBS_DISPATCH for n in task.execution_graph.nodes)
    assert not any(n.node_type is NodeType.MARK_HANG for n in task.execution_graph.nodes)


# --- E2E-11 并行无依赖 + 混合分支(同层多子任务不同走向)---------------------


def test_e2e11_parallel_mixed_branches_no_interdependency():
    svc = _service()
    task = _task_with_graph(svc, objective="mixed", acceptances=1)
    svc.add_node(task.id, SubTaskSpec(node_id="n_search", spec="bot-search→未匹配"), "n_root", NodeType.BOT_SEARCH)
    svc.add_node(task.id, SubTaskSpec(node_id="n_decomp", spec="decomposition"), "n_search", NodeType.DECOMPOSITION)
    children = ["c_a", "c_b", "c_c"]
    for cid in children:
        svc.add_node(task.id, SubTaskSpec(node_id=cid, spec=f"child {cid}"), "n_decomp", NodeType.BOT_SEARCH, executor=cid)
    task = svc.get(task.id)
    # 同层 3 children 无相互 DEPENDENCY 边(只各自挂父 n_decomp)
    child_set = set(children)
    for e in task.execution_graph.edges:
        assert not (e.from_node in child_set and e.to_node in child_set)
    # 分支演进:c_a 命中直派;c_b 未匹配再拆;c_c 验收 fail 重路由命中
    svc.add_node(task.id, SubTaskSpec(node_id="c_a_disp", spec="dispatch"), "c_a", NodeType.DISPATCH, executor="bot-a")
    svc.add_node(task.id, SubTaskSpec(node_id="c_b_dec", spec="decompose c_b"), "c_b", NodeType.DECOMPOSITION)
    svc.add_node(task.id, SubTaskSpec(node_id="c_b1", spec="child b1"), "c_b_dec", NodeType.BOT_SEARCH, executor="c_b1")
    _accept_fail_round(svc, task.id, "c_c", executor="bot-c", round_=1)
    svc.add_node(task.id, SubTaskSpec(node_id="c_c_search2", spec="reroute search c_c"), "c_c", NodeType.BOT_SEARCH)
    svc.add_node(task.id, SubTaskSpec(node_id="c_c_disp2", spec="redispatch c_c"), "c_c_search2", NodeType.DISPATCH, executor="bot-c2")
    # 三分支各自闭合全 DONE 后父才聚合
    for leaf in ("c_a_disp", "c_b1", "c_c_disp2"):
        _set_subtask_done(svc, task.id, leaf)
    task = svc.get(task.id)
    # 父聚合前:Children 至少有一个未闭合时不应聚合 PASS
    unmet_children = [
        {"outcome": AttemptOutcome.PASS} for _ in ("c_a_disp", "c_b1", "c_c_disp2")
    ]
    parent_acs = [AcceptanceCriteria(kind=AcceptanceCriteriaKind.OUTPUT, properties={"label": "parent"})]
    verdict, _ = aggregate_verdict(parent_acs, unmet_children)
    assert verdict is AttemptOutcome.PASS  # 三者全闭合 → 父聚合 PASS
    # 并行无依赖再次断言(含新增子图)
    for e in task.execution_graph.edges:
        assert not (e.from_node in child_set and e.to_node in child_set)
    # 三走向各异:有 dispatch / 有 decomposition / 有 re-search+redispatch
    types_present = {n.node_type for n in task.execution_graph.nodes}
    assert NodeType.DECOMPOSITION in types_present
    assert NodeType.DISPATCH in types_present


# --- E2E-12 看门狗超时 → probe → redrive → 节点级 escalation(C5)-----------


def test_e2e12_watchdog_probe_redrive_node_escalation():
    svc = _service()
    task = _task_with_graph(svc, objective="stuck leaf", acceptances=1)
    driver = _SpyDriver()
    svc.add_node(task.id, SubTaskSpec(node_id="n_search", spec="bot-search→bot1"), "n_root", NodeType.BOT_SEARCH)
    svc.add_node(task.id, SubTaskSpec(node_id="n_disp", spec="dispatch→bot1"), "n_search", NodeType.DISPATCH, executor="bot1")
    # 叶子长 RUNNING:watchdog 计数留 Node.properties(plan §17A.7 / §18.1-14)
    # 模拟 watchdog tick:running_ticks 推进 → PROBE → REDRIVE → 仍失败 → route C5
    leaf_task = svc.get(task.id)
    leaf_node = svc._find_node(leaf_task, "n_disp")
    leaf_node.properties["running_ticks"] = leaf_node.properties.get("running_ticks", 0) + 3
    leaf_node.properties["probe_count"] = leaf_node.properties.get("probe_count", 0) + 1
    leaf_node.properties["redrive_count"] = leaf_node.properties.get("redrive_count", 0) + 1
    svc._task_repo.save(leaf_task)
    # probe 返回超时/失败 → route C5 → 节点级 escalate_to_bbs(区别于 goal-FAIL 的 AWAITING_HUMAN_ACCEPT)
    result = driver.escalate_to_bbs(task.id, reason="watchdog C5 timeout on n_disp")
    assert result.run_mode is RunMode.BBS
    assert driver.escalated == ["watchdog C5 timeout on n_disp"]
    # 计数已推进
    saved = svc.get(task.id)
    saved_node = svc._find_node(saved, "n_disp")
    assert saved_node.properties["running_ticks"] >= 3
    assert saved_node.properties["probe_count"] >= 1
    assert saved_node.properties["redrive_count"] >= 1
    # C5 节点级 escalation 不走 goal-FAIL 的 AWAITING_HUMAN_ACCEPT(graph_status 不变)
    assert saved.execution_graph.graph_status is GraphStatus.ON_PLAZA