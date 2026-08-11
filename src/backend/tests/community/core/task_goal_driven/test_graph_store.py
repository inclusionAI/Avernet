"""P1 单测 — TaskGraphStore (内部 SSOT 写网关, v4).

覆盖 tasks.md P1.x:状态流转合法性、cascade_rollback 净化+隔离 (v4: 仅人工)、
dependencies_satisfied 拓扑 (v4 精确化: DONE 满足 / 分解子满足 / 否则未满足)、
node_depth 派生、compute_output_projection 三 scope、
DONE 传播到 graph.output、全图 DONE 闸门、异常。

v4 变更:
- 无 SPAWNING;add_task_graph 不改父状态。
- _deps_satisfied 精确化: 父 DONE → 满足;父非 DONE 但我是分解子 → 满足;否则未满足。
- query_nodes(ready) 排除有分解子的节点。
- cascade_rollback 仅人工 (自动 reroute 不调用)。
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.task_goal_driven import models as m
from agentclaw.community.core.task_goal_driven.graph_store import (
    GraphNotFoundError,
    InvalidScopeTag,
    InvalidStateTransition,
    NodeNotFoundError,
    TaskGraphStore,
    TaskGraphStoreError,
)


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------


def _spec(ac_id: str = "ac", tag: str = "task") -> m.TaskSpec:
    return m.TaskSpec(
        metadata=m.Metadata(id="DD_001", title="尽调", instruction="产出报告"),
        context=m.Context(background="bg"),
        goal=m.Goal(objective="o", acceptances=[m.AcceptanceCriteria(id=ac_id, description="d", tag=tag)]),
        sla=m.SLA(timeout_ms=3600000, priority=1),
    )


def _info(max_depth: int = 3, spec: m.TaskSpec | None = None) -> m.TaskInfo:
    return m.TaskInfo(
        task_spec=spec or _spec(),
        source_channel_type="bot",
        source_channel_id="owner_bot_01",
        execution_config={"MAX_DEPTH": max_depth},
    )


def _node(node_id: str, parents: list[str], tag: str = "task", spec: m.TaskSpec | None = None) -> m.TaskNode:
    return m.TaskNode(node_id=node_id, depends_on=list(parents), task_spec=spec or _spec(tag=tag))


def _store_with_root() -> tuple[TaskGraphStore, str]:
    store = TaskGraphStore()
    store.initialize_graph(_info())
    return store, "DD_001"


def _dispatch_done(store: TaskGraphStore, task_id: str, node_id: str, output: dict | None = None) -> None:
    """模拟 dispatch + 回投 PASS:RUNNING -> DONE(output)."""
    store.patch_node_runtime_info(task_id, node_id, m.NodeRuntimePatch(status=m.Status.RUNNING, assignee="bot_x"))
    store.patch_node_runtime_info(
        task_id,
        node_id,
        m.NodeRuntimePatch(
            status=m.Status.DONE,
            output_patch=output or {},
            acceptance_result=m.AcceptanceResult(verdict=m.AcceptanceVerdict.PASS, verifier="bot_x"),
        ),
    )


# ===========================================================================
# initialize_graph
# ===========================================================================


def test_initialize_graph_creates_running_graph_with_root():
    store, task_id = _store_with_root()
    graph = store.get_graph(task_id)
    assert graph.status == m.Status.RUNNING
    assert graph.loop_round == 0
    assert len(graph.tasks) == 1
    root = graph.tasks[0]
    assert root.node_id == "n_root"
    assert root.depends_on == []
    assert root.status == m.Status.PENDING
    assert root.run_info.run_mode is None
    # execution_config 持久在 extend_props
    assert store.execution_config(task_id) == {"MAX_DEPTH": 3}


def test_initialize_graph_duplicate_raises():
    store = TaskGraphStore()
    store.initialize_graph(_info())
    with pytest.raises(TaskGraphStoreError):
        store.initialize_graph(_info())


# ===========================================================================
# add_task_graph (v4: 不改父状态;记录分解子)
# ===========================================================================


def test_add_task_graph_does_not_change_parent_status_and_adds_children():
    """v4: add_task_graph 不改父状态 (无 SPAWNING);"委托中" = 有分解子."""
    store, task_id = _store_with_root()
    children = [_node("N_market", ["n_root"], tag="node"), _node("N_tech", ["n_root"], tag="node")]
    store.add_task_graph(task_id, "n_root", children)
    graph = store.get_graph(task_id)
    # 父保持 PENDING (无 SPAWNING)
    root = next(n for n in graph.tasks if n.node_id == "n_root")
    assert root.status == m.Status.PENDING
    n_market = next(n for n in graph.tasks if n.node_id == "N_market")
    assert n_market.depends_on == ["n_root"]   # 不被篡改
    assert n_market.status == m.Status.PENDING
    # 分解子关系已记录
    assert [k.node_id for k in store.decomposition_children(task_id, "n_root")] == ["N_market", "N_tech"]


def test_add_task_graph_rejects_duplicate_node():
    store, task_id = _store_with_root()
    store.add_task_graph(task_id, "n_root", [_node("N_market", ["n_root"])])
    with pytest.raises(TaskGraphStoreError):
        store.add_task_graph(task_id, "n_root", [_node("N_market", ["n_root"])])


def test_add_task_graph_unknown_parent_raises():
    store, task_id = _store_with_root()
    with pytest.raises(NodeNotFoundError):
        store.add_task_graph(task_id, "ghost", [_node("N_x", ["ghost"])])


# ===========================================================================
# dependencies_satisfied (v4 精确化: DONE 满足 / 分解子满足 / 否则未满足)
# ===========================================================================


def test_deps_satisfied_root_children_ready_when_parent_pending():
    """v4: 分解子不阻塞 — 父 PENDING (无 SPAWNING) 但子是分解子 → 就绪."""
    store, task_id = _store_with_root()
    store.add_task_graph(task_id, "n_root", [
        _node("N_market", ["n_root"], tag="node"),
        _node("N_tech", ["n_root"], tag="node"),
        _node("N_aggregate", ["N_market", "N_tech"], tag="task"),
    ])
    ready = store.query_nodes(task_id, m.NodeQueryCriteria(status=m.Status.PENDING, dependencies_satisfied=True))
    ids = {n.node_id for n in ready}
    # n_root 有分解子 → 被排除 (委托中);N_market/N_tech 就绪 (分解子);N_aggregate 未就绪
    assert ids == {"N_market", "N_tech"}


def test_deps_satisfied_aggregate_ready_after_parents_done():
    store, task_id = _store_with_root()
    store.add_task_graph(task_id, "n_root", [
        _node("N_market", ["n_root"], tag="node"),
        _node("N_tech", ["n_root"], tag="node"),
        _node("N_aggregate", ["N_market", "N_tech"], tag="task"),
    ])
    _dispatch_done(store, task_id, "N_market")
    _dispatch_done(store, task_id, "N_tech")
    ready = store.query_nodes(task_id, m.NodeQueryCriteria(status=m.Status.PENDING, dependencies_satisfied=True))
    assert {n.node_id for n in ready} == {"N_aggregate"}


def test_deps_satisfied_root_excluded_from_ready_when_has_children():
    """v4: query_nodes(ready) 排除有分解子的节点 (根 n_root 有子 → 不在 ready)."""
    store, task_id = _store_with_root()
    store.add_task_graph(task_id, "n_root", [_node("N_market", ["n_root"], tag="node")])
    ready = store.query_nodes(task_id, m.NodeQueryCriteria(status=m.Status.PENDING, dependencies_satisfied=True))
    # n_root 有分解子 → 被排除;只有 N_market 就绪
    assert {n.node_id for n in ready} == {"N_market"}


def test_deps_satisfied_root_with_no_parents_is_ready():
    """根 (无子) → 就绪 (引擎负责先规划它,不会直接 dispatch)."""
    store, task_id = _store_with_root()
    ready = store.query_nodes(task_id, m.NodeQueryCriteria(status=m.Status.PENDING, dependencies_satisfied=True))
    assert {n.node_id for n in ready} == {"n_root"}


def test_deps_satisfied_decomposition_child_ready_even_parent_failed():
    """v4 model B: FAILED 父的分解子仍就绪 (委托中,不阻塞)."""
    store, task_id = _store_with_root()
    store.add_task_graph(task_id, "n_root", [_node("N_tech", ["n_root"], tag="node")])
    # N_tech FAILED + 有补救子
    store.patch_node_runtime_info(task_id, "N_tech", m.NodeRuntimePatch(status=m.Status.RUNNING))
    store.patch_node_runtime_info(
        task_id, "N_tech",
        m.NodeRuntimePatch(status=m.Status.FAILED,
                           acceptance_result=m.AcceptanceResult(verdict=m.AcceptanceVerdict.FAIL, gaps=["x"], verifier="v")))
    store.add_task_graph(task_id, "N_tech", [_node("N_tech_deep", ["N_tech"], tag="node")])
    # N_tech_deep 是 N_tech 的分解子 → 就绪 (尽管 N_tech 非 DONE)
    ready = store.query_nodes(task_id, m.NodeQueryCriteria(status=m.Status.PENDING, dependencies_satisfied=True))
    assert "N_tech_deep" in {n.node_id for n in ready}


# ===========================================================================
# patch_node_runtime_info
# ===========================================================================


def test_patch_running_sets_start_done_sets_end_and_outputs():
    store, task_id = _store_with_root()
    store.add_task_graph(task_id, "n_root", [_node("N_market", ["n_root"], tag="node")])
    r1 = store.patch_node_runtime_info(
        task_id, "N_market",
        m.NodeRuntimePatch(status=m.Status.RUNNING, run_mode=m.RunMode.SINGLE_BOT, assignee="bot_market"))
    node = store.get_node(task_id, "N_market")
    assert node.status == m.Status.RUNNING
    assert node.run_info.run_mode == m.RunMode.SINGLE_BOT
    assert node.run_info.assignee == "bot_market"
    assert node.run_info.start_time is not None
    assert node.run_info.end_time is None
    assert r1.node_status == m.Status.RUNNING

    r2 = store.patch_node_runtime_info(
        task_id, "N_market",
        m.NodeRuntimePatch(status=m.Status.DONE, output_patch={"market": "data"}))
    node = store.get_node(task_id, "N_market")
    assert node.status == m.Status.DONE
    assert node.run_info.end_time is not None
    assert node.run_info.output == {"market": "data"}
    assert r2.node_status == m.Status.DONE


def test_patch_output_merge_is_incremental():
    store, task_id = _store_with_root()
    store.add_task_graph(task_id, "n_root", [_node("N_market", ["n_root"], tag="node")])
    store.patch_node_runtime_info(task_id, "N_market", m.NodeRuntimePatch(status=m.Status.RUNNING))
    store.patch_node_runtime_info(task_id, "N_market", m.NodeRuntimePatch(status=m.Status.RUNNING, output_patch={"a": 1}))
    store.patch_node_runtime_info(task_id, "N_market", m.NodeRuntimePatch(status=m.Status.RUNNING, output_patch={"b": 2}))
    node = store.get_node(task_id, "N_market")
    assert node.run_info.output == {"a": 1, "b": 2}


def test_patch_collab_mode_and_extend_props():
    store, task_id = _store_with_root()
    store.add_task_graph(task_id, "n_root", [_node("N_tech", ["n_root"], tag="node")])
    store.patch_node_runtime_info(
        task_id, "N_tech",
        m.NodeRuntimePatch(status=m.Status.RUNNING, run_mode=m.RunMode.COOP_GROUP,
                          assignee="g_tech", collab_mode=m.CollabMode.MANAGER_WORKER))
    node = store.get_node(task_id, "N_tech")
    assert node.run_info.collab_mode == m.CollabMode.MANAGER_WORKER


def test_patch_done_propagates_to_graph_output():
    store, task_id = _store_with_root()
    store.add_task_graph(task_id, "n_root", [_node("N_market", ["n_root"], tag="node")])
    _dispatch_done(store, task_id, "N_market", output={"market": "data"})
    assert store.get_graph(task_id).output == {"market": "data"}


def test_patch_illegal_transition_raises():
    """v4: PENDING→DONE 合法 (传播);PENDING→FAILED 非法."""
    store, task_id = _store_with_root()
    store.add_task_graph(task_id, "n_root", [_node("N_market", ["n_root"], tag="node")])
    # PENDING → FAILED is NOT allowed
    with pytest.raises(InvalidStateTransition):
        store.patch_node_runtime_info(task_id, "N_market", m.NodeRuntimePatch(status=m.Status.FAILED))


def test_patch_runtime_task_id_round_trips_via_extend_props():
    store, task_id = _store_with_root()
    store.add_task_graph(task_id, "n_root", [_node("N_market", ["n_root"], tag="node")])
    store.patch_node_runtime_info(task_id, "N_market",
                                  m.NodeRuntimePatch(status=m.Status.RUNNING,
                                                     extend_props_patch={"runtime_task_id": "run_m1"}))
    r = store.patch_node_runtime_info(task_id, "N_market", m.NodeRuntimePatch(status=m.Status.DONE))
    assert r.runtime_task_id == "run_m1"


def test_patch_pending_to_done_allowed_for_propagation():
    """v4: PENDING → DONE 合法 (root 传播: 分解子全 DONE → root DONE)."""
    store, task_id = _store_with_root()
    store.add_task_graph(task_id, "n_root", [_node("N_market", ["n_root"], tag="node")])
    _dispatch_done(store, task_id, "N_market")
    # root PENDING → DONE (传播)
    store.patch_node_runtime_info(task_id, "n_root", m.NodeRuntimePatch(status=m.Status.DONE))
    assert store.get_node(task_id, "n_root").status == m.Status.DONE


# ===========================================================================
# node_depth
# ===========================================================================


def test_node_depth_case_topology():
    store, task_id = _store_with_root()
    store.add_task_graph(task_id, "n_root", [
        _node("N_market", ["n_root"], tag="node"),
        _node("N_tech", ["n_root"], tag="node"),
        _node("N_aggregate", ["N_market", "N_tech"], tag="task"),
        _node("N_verify", ["N_aggregate"], tag="task"),
    ])
    assert store.node_depth(task_id, "n_root") == 0
    assert store.node_depth(task_id, "N_market") == 1
    assert store.node_depth(task_id, "N_tech") == 1
    assert store.node_depth(task_id, "N_aggregate") == 2
    assert store.node_depth(task_id, "N_verify") == 3


# ===========================================================================
# compute_output_projection
# ===========================================================================


def _seed_case_graph(store: TaskGraphStore, task_id: str) -> None:
    store.add_task_graph(task_id, "n_root", [
        _node("N_market", ["n_root"], tag="node"),
        _node("N_tech", ["n_root"], tag="node"),
        _node("N_aggregate", ["N_market", "N_tech"], tag="task"),
        _node("N_verify", ["N_aggregate"], tag="task"),
    ])


def test_projection_node_scope_reads_direct_parents():
    store, task_id = _store_with_root()
    _seed_case_graph(store, task_id)
    _dispatch_done(store, task_id, "N_market", output={"market": "m"})
    _dispatch_done(store, task_id, "N_tech", output={"tech": "t"})
    # N_aggregate 的 AC tag=task,但 node-scope 仍应读到直系父产出 (by_scope.node)
    proj = store.compute_output_projection(task_id, "N_aggregate")
    assert proj["by_scope"]["node"] == {"market": "m", "tech": "t"}
    # N_aggregate 实际只用 task scope -> inputs = task-scope (此刻全图 DONE output)
    assert proj["inputs"] == {"market": "m", "tech": "t"}


def test_projection_task_scope_reads_full_done_output():
    store, task_id = _store_with_root()
    _seed_case_graph(store, task_id)
    _dispatch_done(store, task_id, "N_market", output={"market": "m"})
    _dispatch_done(store, task_id, "N_tech", output={"tech": "t"})
    _dispatch_done(store, task_id, "N_aggregate", output={"report": "draft"})
    proj = store.compute_output_projection(task_id, "N_verify")
    assert proj["by_scope"]["task"] == {"market": "m", "tech": "t", "report": "draft"}
    assert proj["inputs"] == {"market": "m", "tech": "t", "report": "draft"}


def test_projection_subtree_scope_reads_descendants():
    store, task_id = _store_with_root()
    _seed_case_graph(store, task_id)
    _dispatch_done(store, task_id, "N_market", output={"market": "m"})
    _dispatch_done(store, task_id, "N_tech", output={"tech": "t"})
    _dispatch_done(store, task_id, "N_aggregate", output={"report": "draft"})
    # n_root 之子树 DONE 产出应聚合
    proj = store.compute_output_projection(task_id, "n_root")
    assert proj["by_scope"]["subtree"] == {"market": "m", "tech": "t", "report": "draft"}


def test_projection_invalid_tag_raises():
    store, task_id = _store_with_root()
    store.add_task_graph(task_id, "n_root", [_node("N_bad", ["n_root"], tag="weird_scope")])
    with pytest.raises(InvalidScopeTag):
        store.compute_output_projection(task_id, "N_bad")


# ===========================================================================
# cascade_rollback (v4: 仅人工 rollback_to_node;自动 reroute 不调用)
# ===========================================================================


def test_cascade_rollback_resets_descendants_and_isolates_unrelated():
    store, task_id = _store_with_root()
    _seed_case_graph(store, task_id)
    _dispatch_done(store, task_id, "N_market", output={"market": "m"})
    _dispatch_done(store, task_id, "N_tech", output={"tech": "t"})
    _dispatch_done(store, task_id, "N_aggregate", output={"report": "draft"})
    # N_verify 失败
    store.patch_node_runtime_info(task_id, "N_verify", m.NodeRuntimePatch(status=m.Status.RUNNING))
    store.patch_node_runtime_info(task_id, "N_verify",
                                  m.NodeRuntimePatch(status=m.Status.FAILED,
                                                     acceptance_result=m.AcceptanceResult(
                                                         verdict=m.AcceptanceVerdict.FAIL,
                                                         gaps=["tech深度不足"], verifier="bot_verifier")))
    # cascade rollback 从 N_aggregate (首个受污染下游根) 起 — v4: 仅人工
    store.cascade_rollback(task_id, "N_aggregate")
    g = store.get_graph(task_id)
    by = {n.node_id: n for n in g.tasks}
    # 受污染下游复位 PENDING,清空 output/acceptance
    assert by["N_aggregate"].status == m.Status.PENDING
    assert by["N_aggregate"].run_info.output == {}
    assert by["N_aggregate"].run_info.acceptance_result is None
    assert by["N_verify"].status == m.Status.PENDING
    assert by["N_verify"].run_info.acceptance_result is None
    # 无关 DONE 隔离保留
    assert by["N_market"].status == m.Status.DONE
    assert by["N_market"].run_info.output == {"market": "m"}
    assert by["N_tech"].status == m.Status.DONE


def test_cascade_rollback_rebuilds_graph_output_without_reset_nodes():
    store, task_id = _store_with_root()
    _seed_case_graph(store, task_id)
    _dispatch_done(store, task_id, "N_market", output={"market": "m"})
    _dispatch_done(store, task_id, "N_tech", output={"tech": "t"})
    _dispatch_done(store, task_id, "N_aggregate", output={"report": "draft"})
    store.cascade_rollback(task_id, "N_aggregate")
    # 复位 N_aggregate 后,全图 output 只剩 N_market/N_tech
    assert store.get_graph(task_id).output == {"market": "m", "tech": "t"}


def test_cascade_rollback_unknown_node_raises():
    store, task_id = _store_with_root()
    with pytest.raises(NodeNotFoundError):
        store.cascade_rollback(task_id, "ghost")


# ===========================================================================
# patch_graph_status / bump_loop_round
# ===========================================================================


def test_patch_graph_status_done_requires_all_done():
    store, task_id = _store_with_root()
    store.add_task_graph(task_id, "n_root", [_node("N_market", ["n_root"], tag="node")])
    with pytest.raises(TaskGraphStoreError):
        store.patch_graph_status(task_id, m.Status.DONE)
    _dispatch_done(store, task_id, "N_market")
    # n_root 仍 PENDING (v4: 无 SPAWNING 传播) → 不能 DONE
    with pytest.raises(TaskGraphStoreError):
        store.patch_graph_status(task_id, m.Status.DONE)


def test_patch_graph_status_failed_terminal():
    store, task_id = _store_with_root()
    store.patch_graph_status(task_id, m.Status.FAILED)
    assert store.get_graph(task_id).status == m.Status.FAILED


def test_bump_loop_round():
    store, task_id = _store_with_root()
    assert store.bump_loop_round(task_id) == 1
    assert store.bump_loop_round(task_id) == 2
    assert store.get_graph(task_id).loop_round == 2


# ===========================================================================
# query filters / snapshots
# ===========================================================================


def test_query_nodes_status_and_parent_filters():
    store, task_id = _store_with_root()
    _seed_case_graph(store, task_id)
    _dispatch_done(store, task_id, "N_market")
    done = store.query_nodes(task_id, m.NodeQueryCriteria(status=m.Status.DONE))
    assert {n.node_id for n in done} == {"N_market"}
    children_of_market = store.query_nodes(task_id, m.NodeQueryCriteria(parent_node_id="N_market"))
    assert {n.node_id for n in children_of_market} == {"N_aggregate"}


def test_get_graph_snapshot_is_independent():
    store, task_id = _store_with_root()
    g1 = store.get_graph(task_id)
    store.add_task_graph(task_id, "n_root", [_node("N_market", ["n_root"], tag="node")])
    g2 = store.get_graph(task_id)
    assert len(g1.tasks) == 1
    assert len(g2.tasks) == 2   # g1 不受后续变更影响 (frozen 快照)


def test_export_dashboard_view_equals_get_graph():
    store, task_id = _store_with_root()
    assert store.export_dashboard_view(task_id) == store.get_graph(task_id)


# ===========================================================================
# errors
# ===========================================================================


def test_graph_not_found():
    store = TaskGraphStore()
    with pytest.raises(GraphNotFoundError):
        store.get_graph("nope")
    with pytest.raises(GraphNotFoundError):
        store.query_nodes("nope", m.NodeQueryCriteria())


def test_node_not_found():
    store, task_id = _store_with_root()
    with pytest.raises(NodeNotFoundError):
        store.get_node(task_id, "ghost")
    with pytest.raises(NodeNotFoundError):
        store.patch_node_runtime_info(task_id, "ghost", m.NodeRuntimePatch(status=m.Status.RUNNING))


def test_node_depth_unknown_node_raises():
    store, task_id = _store_with_root()
    with pytest.raises(NodeNotFoundError):
        store.node_depth(task_id, "ghost")


def test_decomposition_children_only_spawned_not_data_consumers():
    # 分解子 = add_task_graph spawn 的;数据消费方 (N_aggregate 依赖 N_market) 不算分解子
    store, task_id = _store_with_root()
    _seed_case_graph(store, task_id)
    market_kids = store.decomposition_children(task_id, "N_market")
    assert [k.node_id for k in market_kids] == []   # N_market 未被 MISS 拆解
    # 给 N_market 补一个 MISS 拆解子图
    store.add_task_graph(task_id, "N_market", [_node("N_market_sub1", ["N_market"], tag="node")])
    market_kids = store.decomposition_children(task_id, "N_market")
    assert [k.node_id for k in market_kids] == ["N_market_sub1"]
    # N_aggregate 数据消费 N_market 但不是分解子
    assert "N_aggregate" not in [k.node_id for k in market_kids]


# ===========================================================================
# 人工 rollback mini case (v4: cascade_rollback 仅人工触发)
# ===========================================================================


def test_manual_rollback_resets_downstream():
    """v4: cascade_rollback 仅人工 rollback_to_node 触发 (自动 reroute 不调用)."""
    store, task_id = _store_with_root()
    _seed_case_graph(store, task_id)
    for nid, out in [("N_market", {"market": "m"}), ("N_tech", {"tech": "t"}),
                     ("N_aggregate", {"report": "draft"})]:
        _dispatch_done(store, task_id, nid, output=out)
    store.patch_node_runtime_info(task_id, "N_verify", m.NodeRuntimePatch(status=m.Status.RUNNING))
    store.patch_node_runtime_info(task_id, "N_verify", m.NodeRuntimePatch(status=m.Status.FAILED))
    # 人工 rollback N_aggregate: 复位 N_aggregate + N_verify
    store.cascade_rollback(task_id, "N_aggregate")
    ready = store.query_nodes(task_id, m.NodeQueryCriteria(status=m.Status.PENDING, dependencies_satisfied=True))
    assert {n.node_id for n in ready} == {"N_aggregate"}   # 下游复位就绪;N_market/N_tech 保留 DONE
