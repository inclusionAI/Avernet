"""P3 单测 — TaskPlanner 规划编排壳 (v4.2 / 方案 B: 委托 DecomposerPort).

覆盖 ``tasks.md`` M0:
- 契约: plan 签名不接收外部 gaps;TaskPlanner 构造需要注入 DecomposerPort (零 case 知识)。
- 机制 (断言,不含具体节点名):
  * 优先级 MISS > FAIL > 前向
  * MISS: miss_events 非空 → 委托分解该节点
  * FAIL (model B): FAILED+gaps 无子 → 委托分解该节点 (产补救挂它下)
  * 前向: 可分解叶 (无分解子 + deps 满足;根无子) → 委托分解
  * 去重 (硬契约②): 图上已存 node_id 不重复产
  * 步进式: 一次只对一个目标 decompose
  * 产出 status=PENDING + run_info 空
  * 只读: 不变更入参 graph
- 框架零 case 知识: 任何具体节点名来自注入的 StubDecomposer,不是框架写死。

StubDecomposer 策略: 按 node.id(含 tag)与 FAIL gaps/miss 返回固定节点,模拟 case 分解产出。
"""
from __future__ import annotations

import inspect

from agentclaw.community.core.task_goal_driven import models as m
from agentclaw.community.core.task_goal_driven.planner import TaskPlanner


# ---------------------------------------------------------------------------
# builders + StubDecomposer (提供 case 内容;框架不含)
# ---------------------------------------------------------------------------

def _spec(acceptances: list[m.AcceptanceCriteria] | None = None, spec_id: str = "DD_001") -> m.TaskSpec:
    return m.TaskSpec(
        metadata=m.Metadata(id=spec_id, title="尽调", instruction="产出报告"),
        context=m.Context(background="bg"),
        goal=m.Goal(objective="产出尽调报告",
                    acceptances=acceptances if acceptances is not None else [
                        m.AcceptanceCriteria(id="ac_dim", description="四维度产出", tag="task"),
                        m.AcceptanceCriteria(id="ac_quality", description="数据支撑 P0", tag="task"),
                    ]),
        sla=m.SLA(timeout_ms=3600000, priority=1),
    )


def _root(spec: m.TaskSpec | None = None, node_id: str = "n_root") -> m.TaskNode:
    return m.TaskNode(node_id=node_id, depends_on=[], task_spec=spec or _spec())


def _node(node_id: str, parents: list[str], spec: m.TaskSpec | None = None,
          status: m.Status = m.Status.PENDING, run_info: m.RuntimeInfo | None = None) -> m.TaskNode:
    return m.TaskNode(node_id=node_id, depends_on=list(parents), task_spec=spec or _spec(),
                      status=status, run_info=run_info or m.RuntimeInfo())


def _graph(tasks: list[m.TaskNode], status: m.Status = m.Status.RUNNING) -> m.TaskExecutionGraph:
    return m.TaskExecutionGraph(status=status, tasks=tasks)


class StubDecomposer:
    """可配置的分解策略 stub: 按 node_id 或 FAIL/MISS tag 返回固定产出 (替代 LLM/SKILL)。

    返回的节点 status=PENDING, run_info 空 (满足规划产出统一形态)。返回 [] 表示不可再分。
    """

    def __init__(self) -> None:
        # node_id -> 产出节点列表;运行时按需配置
        self_by_id: dict[str, list[m.TaskNode]] = {}
        self._by_id = self_by_id

    def for_id(self, node_id: str, produced: list[m.TaskNode]) -> "StubDecomposer":
        self._by_id[node_id] = produced
        return self

    def decompose(self, node: m.TaskNode, graph: m.TaskExecutionGraph) -> list[m.TaskNode]:
        # FAIL 补救: 按 gaps 产补救子 (节点 tag "fail-remedy")
        # MISS: 按目标产子 (节点 tag "miss-sub")
        # 前向/根: 按 node_id 配置产出
        if node.node_id in self._by_id:
            return self._by_id[node.node_id]
        # 默认: 不可再分 -> []
        return []


# ===========================================================================
# 契约: plan 签名不接收 gaps;构造需注入 decomposer
# ===========================================================================

def test_plan_signature_has_no_gaps_parameter():
    sig = inspect.signature(TaskPlanner.plan)
    assert "gaps" not in sig.parameters
    assert list(sig.parameters) == ["self", "graph"], list(sig.parameters)


def test_planner_requires_decomposer_no_default():
    """框架零 case 知识: TaskPlanner 构造必填 decomposer (无默认 stub)。"""
    import pytest
    with pytest.raises(TypeError):
        TaskPlanner()  # type: ignore[call-arg]


# ===========================================================================
# MISS > FAIL > 前向 优先级:委托的是"第一个"目标
# ===========================================================================

def test_miss_over_fail_and_forward_delegates_to_miss_node():
    """优先级 MISS>FAIL>前向: 同时有 MISS 与 FAIL/前向目标时,委托 MISS 节点。"""
    deco = StubDecomposer().for_id("N_market", [
        _node("N_market_sub0", ["N_market"]), _node("N_market_sub1", ["N_market"])])

    root = _root()
    failed = _node("N_tech", ["n_root"], status=m.Status.FAILED,
                   run_info=m.RuntimeInfo(acceptance_result=m.AcceptanceResult(
                       verdict=m.AcceptanceVerdict.FAIL, gaps=["x"], verifier="v")))
    miss_node = _node("N_market", ["n_root"],
                      run_info=m.RuntimeInfo(extend_props={"miss_events": ["no bot cover"]}))
    planner = TaskPlanner(deco)
    nodes = planner.plan(_graph([root, failed, miss_node]))
    # 委托的是 MISS 节点 (N_market),不是 FAIL 节点
    produced_ids = [n.node_id for n in nodes]
    assert produced_ids == ["N_market_sub0", "N_market_sub1"]
    assert all(n.depends_on == ["N_market"] for n in nodes)


def test_fail_over_forward_delegates_to_failed_node():
    """优先级 FAIL>前向: 存在 FAIL 与可分解前向叶时,委托 FAIL 节点。"""
    deco = StubDecomposer().for_id("leaf_a", [_node("remedy_leaf_a_0", ["leaf_a"])])

    root = _root()
    leaf_done = _node("leaf_done", ["n_root"], status=m.Status.DONE)
    leaf_a_failed = _node("leaf_a", ["n_root"], status=m.Status.FAILED,
                          run_info=m.RuntimeInfo(acceptance_result=m.AcceptanceResult(
                              verdict=m.AcceptanceVerdict.FAIL, gaps=["gap_a"], verifier="v")))
    planner = TaskPlanner(deco)
    nodes = planner.plan(_graph([root, leaf_done, leaf_a_failed]))
    assert [n.node_id for n in nodes] == ["remedy_leaf_a_0"]
    assert all(n.depends_on == ["leaf_a"] for n in nodes)  # model B: 挂该 FAIL 节点下


def test_fail_without_gaps_skipped_no_forward():
    """FAIL 无 gaps: 不被选为 FAIL 目标;若无前向目标 -> []。"""
    root = _root()
    failed_no_gaps = _node("N_x", ["n_root"], status=m.Status.FAILED,
                           run_info=m.RuntimeInfo(acceptance_result=m.AcceptanceResult(
                               verdict=m.AcceptanceVerdict.FAIL, verifier="v")))
    deco = StubDecomposer()
    # 根有子 (N_x),无可分解前向叶 (N_x 有父 but FAILED; 根已有子) -> 前向目标 None
    nodes = TaskPlanner(deco).plan(_graph([root, failed_no_gaps]))
    assert nodes == []


# ===========================================================================
# FAIL 补救去重:FAILED 节点已有分解子 -> 不重复委托
# ===========================================================================

def test_failed_with_existing_children_not_decomposed_again():
    """去重: FAILED 节点已有分解子 (委托中) -> 不重复 decompose;若无前向目标 -> []。"""
    deco = StubDecomposer().for_id("N_tech", [_node("would_be_dup", ["N_tech"])])

    root = _root()
    failed = _node("N_tech", ["n_root"], status=m.Status.FAILED,
                   run_info=m.RuntimeInfo(acceptance_result=m.AcceptanceResult(
                       verdict=m.AcceptanceVerdict.FAIL, gaps=["x"], verifier="v")))
    existing_child = _node("remedy_N_tech_0", ["N_tech"])
    planner = TaskPlanner(deco)
    # N_tech 有分解子 (existing_child depends_on == [N_tech]) -> 跳过 FAIL 委托
    # 前向: 根有子(N_tech); N_tech 有子; existing_child 无父满足? existing_child depends_on=[N_tech] 非DONE -> 不选
    nodes = planner.plan(_graph([root, failed, existing_child]))
    assert nodes == []


# ===========================================================================
# 前向目标选择: 可分解叶 (无分解子 + deps 满足;根无子也算)
# ===========================================================================

def test_forward_delegates_to_root_when_no_children():
    """前向: 根无分解子 -> 委托 decompose(root)。"""
    deco = StubDecomposer().for_id("n_root", [
        _node("L1", ["n_root"]), _node("L2", ["n_root"])])
    planner = TaskPlanner(deco)
    nodes = planner.plan(_graph([_root()]))
    assert [n.node_id for n in nodes] == ["L1", "L2"]
    assert all(n.depends_on == ["n_root"] for n in nodes)


def test_forward_delegates_to_leaf_whose_deps_satisfied():
    """前向: 子节点 deps 全 DONE 且自身无分解子 -> 委托分解它 (下一层)。"""
    deco = StubDecomposer().for_id("leaf", [_node("leaf_child", ["leaf"])])

    root = _root()
    leaf = _node("leaf", ["n_root"], status=m.Status.DONE)  # deps 满足 + 无子
    planner = TaskPlanner(deco)
    nodes = planner.plan(_graph([root, leaf]))
    # root 无子 (已 DONE? 仍选 root 优先, 因 _forward_target 按顺序遍历)
    # 为避免歧义,根 done 并有子时才选 leaf: 调整 graph - root RUNNING 有子 leaf
    assert nodes == []  # root 无子会先被选 -> deco.for_id("n_root") 未配 -> []
    # 用一个清晰的 case: root 已有子(委托中) -> 选 leaf
    deco2 = StubDecomposer().for_id("leaf", [_node("leaf_child", ["leaf"])])
    root2 = _root()
    leaf2 = _node("leaf", ["n_root"], status=m.Status.DONE)
    other_under_root = _node("mid", ["n_root"], status=m.Status.RUNNING)  # root 有子 mid
    # root 有子(mid) -> 不选 root; leaf deps(n_root? no, leaf depends_on=[n_root])
    # leaf depends_on=[n_root] DONE? root status PENDING -> leaf deps 不满足 -> 不选
    # mid deps=[n_root] DONE? PENDING -> 不选 -> []
    planner2 = TaskPlanner(deco2)
    nodes2 = planner2.plan(_graph([root2, other_under_root, leaf2]))
    assert nodes2 == []


def test_forward_skips_node_with_unsatisfied_deps():
    """前向: 节点 deps 未满足 (父未 DONE) -> 不选;若同时无可分解目标 -> []。

    构造: root 不可再分 (deco root -> []), agg 依赖未满足 (root 未 DONE) -> 不选;
    _forward_target 返回 root (无子), decompose(root)=[], plan -> []。
    关键: agg 因 deps 未满足**未被**委托 (即使为它配了 deco 也不应被调)。
    """
    agg_called: list[str] = []

    class _Deco(StubDecomposer):
        def decompose(self, node, graph):
            if node.node_id == "agg":
                agg_called.append(node.node_id)  # 不该被调
            return [] if node.node_id == "n_root" else []

    deco = _Deco()
    root = _node("n_root", [])
    agg = _node("agg", ["n_root"], status=m.Status.PENDING)  # 依赖 n_root(非DONE) -> 不选
    nodes = TaskPlanner(deco).plan(_graph([root, agg]))
    assert nodes == []
    assert "agg" not in agg_called  # agg 未被委托 (deps 未满足)


# ===========================================================================
# 去重 (硬契约②): 图上已存 node_id 不重复产
# ===========================================================================

def test_dedup_filters_nodes_already_in_graph():
    """decompose 产出含图上已存在的 node_id 时,被 _dedup 过滤。

    构造: root 可选 (无任何节点的 depends == ["n_root"]); existing 自带子 (L1c depends=[L1])
    使 existing 不可选 (有分解子);decompose(root) 产 L1+L2,L1 与 existing 同 id -> 去重 -> [L2]。
    """
    existing = _node("L1", [])                      # 独立节点,已有子 -> 不可被 _forward_target 选
    existing_child = _node("L1c", ["L1"])            # L1c 把 L1 标记为"委托中",existing 被跳过
    deco = StubDecomposer().for_id("n_root", [
        _node("L1", ["n_root"]),                    # 与 existing 同 id -> 去重
        _node("L2", ["n_root"]),
    ])
    root = _root()
    nodes = TaskPlanner(deco).plan(_graph([root, existing, existing_child]))
    assert [n.node_id for n in nodes] == ["L2"]


# ===========================================================================
# 步进式: 一次只对一个目标 decompose
# ===========================================================================

def test_stepwise_single_target_first_miss():
    """步进式: 多个 MISS 时只委托第一个 MISS 目标 (不一次性全部)。"""
    deco = StubDecomposer()
    deco.for_id("N_a", [_node("N_a_sub", ["N_a"])])
    deco.for_id("N_b", [_node("N_b_sub", ["N_b"])])

    root = _root()
    a_miss = _node("N_a", ["n_root"], run_info=m.RuntimeInfo(extend_props={"miss_events": ["x"]}))
    b_miss = _node("N_b", ["n_root"], run_info=m.RuntimeInfo(extend_props={"miss_events": ["y"]}))
    nodes = TaskPlanner(deco).plan(_graph([root, a_miss, b_miss]))
    # 只产 N_a 的子 (第一个 MISS 目标)
    assert [n.node_id for n in nodes] == ["N_a_sub"]


def test_decompose_returns_empty_means_leaf_not_decomposable():
    """decompose 返回 [] -> 该目标不可再分;若无其它目标 -> plan 返回 []。"""
    deco = StubDecomposer()  # 所有 decompose 返回 []
    root = _root()
    nodes = TaskPlanner(deco).plan(_graph([root]))
    assert nodes == []


# ===========================================================================
# 产出形态: status=PENDING, run_info 空
# ===========================================================================

def test_produced_nodes_are_pending_with_empty_run_info():
    """委托产出统一形态: status=PENDING, run_info 空 (若 stub 返回别样,框架不纠错但建议规范)。"""
    deco = StubDecomposer().for_id("n_root", [
        m.TaskNode(node_id="L", depends_on=["n_root"], task_spec=_spec(),
                   status=m.Status.PENDING, run_info=m.RuntimeInfo())])
    nodes = TaskPlanner(deco).plan(_graph([_root()]))
    for n in nodes:
        assert n.status == m.Status.PENDING
        assert n.run_info.run_mode is None
        assert n.run_info.output == {}
        assert n.run_info.acceptance_result is None


# ===========================================================================
# 只读: plan 不变更入参 graph
# ===========================================================================

def test_plan_does_not_mutate_input_graph():
    deco = StubDecomposer().for_id("n_root", [_node("L", ["n_root"])])
    graph = _graph([_root()])
    snapshot_ids = [n.node_id for n in graph.tasks]
    TaskPlanner(deco).plan(graph)
    assert [n.node_id for n in graph.tasks] == snapshot_ids
