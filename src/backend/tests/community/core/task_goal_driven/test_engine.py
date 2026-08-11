"""P2 单测 — ExecutionEngine (TaskCenter 内部编排核,方案 C, v4).

覆盖 tasks.md P2.x:drive 步进式 + 串行化、MISS 深度闸门、FAIL 补救 (model B: 针对该节点
产子挂它下,不复位下游)、全图 DONE 收口、STUCK->HUNG、escalate_to_bbs。用 doubles 注入
(无执行主体),验证核心编排可独立单测。

v4 case:execute -> plan (步进: 只产 N_market/N_tech) -> dispatch -> N_market PASS ->
N_tech FAIL (gaps) -> plan 产补救 N_tech_deep 挂 N_tech 下 -> dispatch -> N_tech_deep PASS ->
传播 N_tech DONE -> plan 产 N_aggregate -> N_aggregate PASS -> plan 产 N_verify ->
N_verify PASS -> graph DONE。全程无 cascade_rollback (model B: 下游未入图,无需复位)。
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.task_goal_driven import models as m
from agentclaw.community.core.task_goal_driven.engine import ExecutionEngine
from agentclaw.community.core.task_goal_driven.graph_store import TaskGraphStore


# ---------------------------------------------------------------------------
# 领域 builders
# ---------------------------------------------------------------------------


def _spec(tag: str = "task") -> m.TaskSpec:
    return m.TaskSpec(
        metadata=m.Metadata(id="DD_001", title="尽调", instruction="产出报告"),
        context=m.Context(background="bg"),
        goal=m.Goal(objective="o", acceptances=[m.AcceptanceCriteria(id="ac", description="d", tag=tag)]),
        sla=m.SLA(timeout_ms=3600000, priority=1),
    )


def _info(max_depth: int = 3) -> m.TaskInfo:
    return m.TaskInfo(task_spec=_spec(), source_channel_type="bot",
                      source_channel_id="owner_bot_01", execution_config={"MAX_DEPTH": max_depth})


def _initial_first_layer() -> list[m.TaskNode]:
    """v4 步进式: 初始只产第一层叶."""
    return [
        m.TaskNode(node_id="N_market", depends_on=["n_root"], task_spec=_spec("node")),
        m.TaskNode(node_id="N_tech", depends_on=["n_root"], task_spec=_spec("node")),
    ]


def _statuses(store: TaskGraphStore, task_id: str) -> dict[str, str]:
    return {n.node_id: n.status.value for n in store.get_graph(task_id).tasks}


# ===========================================================================
# Fakes
# ===========================================================================


class FakePlanner:
    """v4 步进式 FakePlanner: MISS > FAIL > 前向 (初始/aggregate/verify)."""

    def __init__(self, miss_sub_prefix: str = "sub") -> None:
        self.calls: int = 0
        self._miss_prefix = miss_sub_prefix

    def plan(self, graph: m.TaskExecutionGraph) -> list[m.TaskNode]:
        self.calls += 1
        tasks = graph.tasks
        # 1) MISS: miss_events 非空
        miss = [n for n in tasks if n.run_info.extend_props.get("miss_events")]
        if miss:
            leaf = miss[0]
            return [
                m.TaskNode(node_id=f"{leaf.node_id}_{self._miss_prefix}1", depends_on=[leaf.node_id], task_spec=_spec()),
                m.TaskNode(node_id=f"{leaf.node_id}_{self._miss_prefix}2", depends_on=[leaf.node_id], task_spec=_spec()),
            ]

        # 2) FAIL (model B): FAILED + gaps,无已有子
        failed = [n for n in tasks
                  if n.status == m.Status.FAILED
                  and n.run_info.acceptance_result and n.run_info.acceptance_result.gaps
                  and not any(c.depends_on == [n.node_id] for c in tasks)]
        if failed:
            fn = failed[0]
            gaps = fn.run_info.acceptance_result.gaps
            return [m.TaskNode(node_id=f"remedy_{fn.node_id}_{i}", depends_on=[fn.node_id],
                               task_spec=_spec("node")) for i, _ in enumerate(gaps)]

        # 3) 前向步进
        root = next((n for n in tasks if not n.depends_on), tasks[0])
        # 初始: 根无子
        if not any(n.depends_on == [root.node_id] for n in tasks):
            return _initial_first_layer()
        # 第一层全 DONE → N_aggregate (去重)
        leaves = [n for n in tasks if n.depends_on == [root.node_id]]
        if leaves and all(lf.status == m.Status.DONE for lf in leaves):
            if not any(n.node_id == "N_aggregate" for n in tasks):
                return [m.TaskNode(node_id="N_aggregate",
                                   depends_on=[lf.node_id for lf in leaves], task_spec=_spec("task"))]
            agg = next((n for n in tasks if n.node_id == "N_aggregate"), None)
            if agg and agg.status == m.Status.DONE and not any(n.node_id == "N_verify" for n in tasks):
                return [m.TaskNode(node_id="N_verify", depends_on=["N_aggregate"], task_spec=_spec("task"))]
        return []


class FakeDispatcher:
    """DISPATCHED 节点置 RUNNING (契约);可配置 MISS 节点集."""

    def __init__(self, store: TaskGraphStore, miss: set[str] | None = None, task_id: str = "DD_001") -> None:
        self.store = store
        self.task_id = task_id
        self.miss = miss or set()
        self.dispatched: list[str] = []

    def dispatch(self, to_do_list: list[m.TaskNode]) -> list[m.DispatchOutcome]:
        out: list[m.DispatchOutcome] = []
        for n in to_do_list:
            if n.node_id in self.miss:
                out.append(m.DispatchOutcome(node_id=n.node_id, kind=m.DispatchKind.MISS))
                continue
            self.store.patch_node_runtime_info(
                self.task_id, n.node_id,
                m.NodeRuntimePatch(status=m.Status.RUNNING, run_mode=m.RunMode.SINGLE_BOT,
                                   assignee=f"bot_{n.node_id}",
                                   extend_props_patch={"runtime_task_id": f"run_{n.node_id}"}))
            self.dispatched.append(n.node_id)
            out.append(m.DispatchOutcome(node_id=n.node_id, kind=m.DispatchKind.DISPATCHED,
                                         runtime_task_id=f"run_{n.node_id}"))
        return out


class FakeBbs:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def run_bbs(self, node: m.TaskNode, output_projection: dict) -> str:
        rt = f"bbs_{node.node_id}"
        self.calls.append((node.node_id, output_projection))
        return rt


def _build(max_depth: int = 3, miss: set[str] | None = None, bbs: FakeBbs | None = None):
    store = TaskGraphStore()
    store.initialize_graph(_info(max_depth))
    planner = FakePlanner()
    disp = FakeDispatcher(store, miss=miss)
    eng = ExecutionEngine(store, planner, disp, bbs_executor=bbs)
    return store, eng, planner, disp


def _pass(ar_gaps: list[str] | None = None) -> m.AcceptanceResult:
    return m.AcceptanceResult(verdict=m.AcceptanceVerdict.PASS, gaps=ar_gaps or [], verifier="bot")


def _fail(gaps: list[str]) -> m.AcceptanceResult:
    return m.AcceptanceResult(verdict=m.AcceptanceVerdict.FAIL, gaps=gaps, verifier="bot_verifier")


# ===========================================================================
# drive 初始规划 + 分发 (v4 步进式)
# ===========================================================================


def test_drive_initial_plan_stepwise_only_first_layer():
    """v4: 初始 drive 只产第一层 (N_market/N_tech),不产 N_aggregate/N_verify."""
    store, eng, _, disp = _build()
    eng.drive("DD_001")
    st = _statuses(store, "DD_001")
    assert st["n_root"] == "pending"          # v4: 无 SPAWNING
    assert st["N_market"] == "running"
    assert st["N_tech"] == "running"
    # 步进式: N_aggregate/N_verify 不在图上
    assert "N_aggregate" not in st
    assert "N_verify" not in st
    assert set(disp.dispatched) == {"N_market", "N_tech"}


def test_drive_idempotent_no_state_change_on_repeat():
    store, eng, planner, _ = _build()
    eng.drive("DD_001")
    snap = _statuses(store, "DD_001")
    eng.drive("DD_001")
    eng.drive("DD_001")
    # v4: 状态不变 (plan 可能被 fixpoint 泵多次调用,但产 [] 无副作用)
    assert _statuses(store, "DD_001") == snap


def test_drive_on_terminal_graph_is_noop():
    store, eng, planner, _ = _build()
    store.patch_graph_status("DD_001", m.Status.FAILED)
    eng.drive("DD_001")
    assert planner.calls == 0
    assert store.get_graph("DD_001").status == m.Status.FAILED


# ===========================================================================
# report PASS 推进 + 步进式下游产生
# ===========================================================================


def test_report_pass_progresses_stepwise():
    """v4 步进式: N_market 单独 PASS 不产 N_aggregate (N_tech 还在跑)."""
    store, eng, _, _ = _build()
    eng.drive("DD_001")
    eng.report("DD_001", "N_market", output_patch={"market": "m"}, acceptance_result=_pass())
    st = _statuses(store, "DD_001")
    assert st["N_market"] == "done"
    # N_aggregate 还不该产生 (N_tech 未 DONE)
    assert "N_aggregate" not in st


def test_report_both_pass_produces_aggregate():
    """v4 步进式: N_market 和 N_tech 都 DONE → plan 产 N_aggregate 并派发."""
    store, eng, _, _ = _build()
    eng.drive("DD_001")
    eng.report("DD_001", "N_market", output_patch={"market": "m"}, acceptance_result=_pass())
    eng.report("DD_001", "N_tech", output_patch={"tech": "t"}, acceptance_result=_pass())
    st = _statuses(store, "DD_001")
    assert st["N_aggregate"] == "running"   # 自动产并派发


# ===========================================================================
# 完整 happy path (步进式)
# ===========================================================================


def test_full_happy_path_stepwise_to_graph_done():
    store, eng, _, _ = _build()
    eng.drive("DD_001")
    eng.report("DD_001", "N_market", output_patch={"market": "m"}, acceptance_result=_pass())
    eng.report("DD_001", "N_tech", output_patch={"tech": "t"}, acceptance_result=_pass())
    eng.report("DD_001", "N_aggregate", output_patch={"report": "draft"}, acceptance_result=_pass())
    eng.report("DD_001", "N_verify", acceptance_result=_pass())
    graph = store.get_graph("DD_001")
    assert graph.status == m.Status.DONE
    assert graph.loop_round == 0
    assert all(n.status == m.Status.DONE for n in graph.tasks)


# ===========================================================================
# FAIL 补救 (model B: 针对该节点产子挂它下,不复位下游)
# ===========================================================================


def test_fail_produces_remedy_under_failed_node():
    """v4 model B: N_tech FAIL → 补救 N_tech_deep 挂 N_tech 下 (非 root);loop_round++."""
    store, eng, _, _ = _build()
    eng.drive("DD_001")
    eng.report("DD_001", "N_market", output_patch={"market": "m"}, acceptance_result=_pass())
    # N_tech FAIL (带 gaps)
    eng.report("DD_001", "N_tech", acceptance_result=_fail(["tech深度不足"]))
    st = _statuses(store, "DD_001")
    assert "remedy_N_tech_0" in st                     # 补救入图
    assert st["remedy_N_tech_0"] == "running"          # 补救已派发
    # 补救挂 N_tech 下 (model B),不是 root
    node = store.get_node("DD_001", "remedy_N_tech_0")
    assert node.depends_on == ["N_tech"]
    assert store.get_graph("DD_001").loop_round == 1
    # N_aggregate 未入图 (N_tech 未 DONE)
    assert "N_aggregate" not in st


def test_fail_remedy_pass_propagates_failed_node_to_done():
    """v4 model B: 补救子 PASS → FAILED 节点传播 DONE → 下游产生."""
    store, eng, _, _ = _build()
    eng.drive("DD_001")
    eng.report("DD_001", "N_market", output_patch={"market": "m"}, acceptance_result=_pass())
    eng.report("DD_001", "N_tech", acceptance_result=_fail(["tech深度不足"]))
    eng.report("DD_001", "remedy_N_tech_0", output_patch={"tech_deep": "td"}, acceptance_result=_pass())
    st = _statuses(store, "DD_001")
    # 补救 DONE → N_tech 传播 DONE
    assert st["remedy_N_tech_0"] == "done"
    assert st["N_tech"] == "done"
    # N_market DONE + N_tech DONE → N_aggregate 自动产生并派发
    assert st["N_aggregate"] == "running"


def test_fail_full_reroute_path_completes_graph():
    """v4 model B 完整链路: FAIL → 补救 → 传播 → 下游 → 终验 → DONE."""
    store, eng, _, _ = _build()
    eng.drive("DD_001")
    eng.report("DD_001", "N_market", output_patch={"market": "m"}, acceptance_result=_pass())
    eng.report("DD_001", "N_tech", acceptance_result=_fail(["tech深度不足"]))
    eng.report("DD_001", "remedy_N_tech_0", output_patch={"tech_deep": "td"}, acceptance_result=_pass())
    eng.report("DD_001", "N_aggregate", output_patch={"report": "v2"}, acceptance_result=_pass())
    eng.report("DD_001", "N_verify", acceptance_result=_pass())
    graph = store.get_graph("DD_001")
    assert graph.status == m.Status.DONE
    assert graph.loop_round == 1
    assert all(n.status == m.Status.DONE for n in graph.tasks)


def test_fail_no_cascade_in_auto_path():
    """v4 model B: 自动 reroute 不触发 cascade_rollback (下游未入图,无需复位)."""
    store, eng, _, _ = _build()
    eng.drive("DD_001")
    eng.report("DD_001", "N_market", acceptance_result=_pass())
    eng.report("DD_001", "N_tech", acceptance_result=_fail(["x"]))
    # N_aggregate 从未入图,不存在复位
    st = _statuses(store, "DD_001")
    assert "N_aggregate" not in st


# ===========================================================================
# FAIL 深度闸门
# ===========================================================================


def test_fail_at_max_depth_hungs():
    """v4: FAIL+gaps,depth ≥ MAX → HUNG (引擎决策,不补救)."""
    store = TaskGraphStore()
    store.initialize_graph(_info(max_depth=1))
    planner = FakePlanner()
    disp = FakeDispatcher(store)
    eng = ExecutionEngine(store, planner, disp)
    eng.drive("DD_001")
    eng.report("DD_001", "N_market", acceptance_result=_pass())
    # N_tech FAIL, depth=1, MAX=1 → HUNG
    eng.report("DD_001", "N_tech", acceptance_result=_fail(["x"]))
    assert _statuses(store, "DD_001")["N_tech"] == "hung"
    assert "remedy_N_tech_0" not in _statuses(store, "DD_001")


# ===========================================================================
# MISS 深度闸门 + 拆解 (v4: miss_events)
# ===========================================================================


def test_miss_below_max_depth_decomposes_node():
    """v4: N_market MISS → depth<MAX → plan 拆解为子;miss_events 消费后清空."""
    store, eng, planner, _ = _build(max_depth=3, miss={"N_market"})
    eng.drive("DD_001")
    st = _statuses(store, "DD_001")
    # N_market 有分解子 → 被排除 ready;子节点入图并派发
    assert "N_market_sub1" in st and "N_market_sub2" in st
    assert st["N_market_sub1"] == "running"
    # N_market 保持 PENDING (无 SPAWNING),有分解子
    assert st["N_market"] == "pending"
    # miss_events 已消费 (清空)
    node = store.get_node("DD_001", "N_market")
    assert node.run_info.extend_props.get("miss_events") == []


def test_miss_at_max_depth_hungs():
    # N_market MISS, depth=1, MAX=1 → HUNG
    store, eng, _, _ = _build(max_depth=1, miss={"N_market"})
    eng.drive("DD_001")
    assert _statuses(store, "DD_001")["N_market"] == "hung"


def test_miss_decomposition_propagates_when_subtasks_done():
    """v4: MISS 拆解子全 PASS → N_market 传播 DONE (PENDING → DONE)."""
    store, eng, _, _ = _build(max_depth=3, miss={"N_market"})
    eng.drive("DD_001")
    eng.report("DD_001", "N_market_sub1", acceptance_result=_pass())
    eng.report("DD_001", "N_market_sub2", acceptance_result=_pass())
    assert _statuses(store, "DD_001")["N_market"] == "done"


# ===========================================================================
# STUCK -> HUNG -> BBS
# ===========================================================================


def test_report_stuck_sets_hung():
    store, eng, _, _ = _build()
    eng.drive("DD_001")
    eng.report_stuck("DD_001", "N_market")
    assert _statuses(store, "DD_001")["N_market"] == "hung"


def test_escalate_to_bbs_runs_and_sets_running():
    store, eng, _, _ = _build(bbs=FakeBbs())
    eng.drive("DD_001")
    eng.report_stuck("DD_001", "N_market")
    rt = eng.escalate_to_bbs("DD_001", "N_market")
    assert rt == "bbs_N_market"
    node = store.get_node("DD_001", "N_market")
    assert node.status == m.Status.RUNNING
    assert node.run_info.run_mode == m.RunMode.BBS
    assert node.run_info.extend_props.get("bbs_escalated") is True


def test_escalate_to_bbs_without_port_raises():
    store, eng, _, _ = _build()   # 无 bbs
    eng.drive("DD_001")
    eng.report_stuck("DD_001", "N_market")
    with pytest.raises(RuntimeError):
        eng.escalate_to_bbs("DD_001", "N_market")


def test_bbs_then_pass_completes():
    # STUCK->HUNG->BBS 接力->回投 PASS->DONE 全链路 (单节点图简化)
    store = TaskGraphStore()
    store.initialize_graph(m.TaskInfo(
        task_spec=m.TaskSpec(metadata=m.Metadata(id="T2", title="t", instruction="i"),
                             context=m.Context(background="b"),
                             goal=m.Goal(objective="o", acceptances=[m.AcceptanceCriteria(id="ac", description="d", tag="task")]),
                             sla=m.SLA(timeout_ms=3600000, priority=1)),
        source_channel_type="bot", source_channel_id="o", execution_config={"MAX_DEPTH": 3}))
    bbs = FakeBbs()

    class SoloPlanner:
        def plan(self, graph):
            return [m.TaskNode(node_id="N1", depends_on=["n_root"], task_spec=_spec("task"))] if len(graph.tasks) == 1 else []
    solo_disp = FakeDispatcher(store, task_id="T2")
    eng = ExecutionEngine(store, SoloPlanner(), solo_disp, bbs_executor=bbs)
    eng.drive("T2")
    eng.report_stuck("T2", "N1")
    eng.escalate_to_bbs("T2", "N1")
    eng.report("T2", "N1", acceptance_result=_pass())
    assert store.get_graph("T2").status == m.Status.DONE
    assert len(bbs.calls) == 1 and bbs.calls[0][0] == "N1"


# ===========================================================================
# 边界
# ===========================================================================


def test_no_remedy_from_planner_does_not_bump_loop():
    store = TaskGraphStore()
    store.initialize_graph(_info())
    bbs = FakeBbs()

    class EmptyFailPlanner:
        def plan(self, graph):
            # 初始拆解照常;FAIL 时不产补救 (空)
            tasks = graph.tasks
            root = next((n for n in tasks if not n.depends_on), tasks[0])
            if not any(n.depends_on == [root.node_id] for n in tasks):
                return _initial_first_layer()
            # FAIL 补救返回空
            return []
    disp = FakeDispatcher(store)
    eng = ExecutionEngine(store, EmptyFailPlanner(), disp, bbs_executor=bbs)
    eng.drive("DD_001")
    eng.report("DD_001", "N_market", acceptance_result=_pass())
    eng.report("DD_001", "N_tech", acceptance_result=_fail(["x"]))
    # 无补救 -> loop_round 不增,N_tech FAILED,图未终结
    assert store.get_graph("DD_001").loop_round == 0
    assert _statuses(store, "DD_001")["N_tech"] == "failed"


def test_fail_without_gaps_goes_hung_not_failed():
    """v4: FAIL 无 gaps (超时/崩溃) → HUNG,不补救."""
    store, eng, _, _ = _build()
    eng.drive("DD_001")
    # FAIL 无 gaps → HUNG (不触发 plan 补救)
    eng.report("DD_001", "N_market",
               acceptance_result=m.AcceptanceResult(verdict=m.AcceptanceVerdict.FAIL, verifier="bot"))
    assert _statuses(store, "DD_001")["N_market"] == "hung"
    assert "remedy_N_market_0" not in _statuses(store, "DD_001")
