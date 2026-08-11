"""P0 单测 — 领域模型与支撑 DTO (v4).

覆盖 tasks.md P0.7:
- serde roundtrip (frozen dataclass 可序列化为 dict 并无损重建)
- AcceptanceCriteria.tag scope 合法值校验
- Status 5 态合法性 + 各枚举成员完备 (v4: 删 SPAWNING)
- 无 phase / NodeType / Edge / depth / dependencies_satisfied 等"派生或遗留"残留在模型字段
"""
from __future__ import annotations

import dataclasses
from dataclasses import FrozenInstanceError

import pytest

from agentclaw.community.core.task_goal_driven import models as m


# ---------------------------------------------------------------------------
# fixtures / builders
# ---------------------------------------------------------------------------


def _spec(acceptances: list[m.AcceptanceCriteria] | None = None) -> m.TaskSpec:
    return m.TaskSpec(
        metadata=m.Metadata(id="t1", title="尽调", instruction="产出报告"),
        context=m.Context(background="bg", constraints=["c1"]),
        goal=m.Goal(objective="o", acceptances=acceptances or []),
        sla=m.SLA(timeout_ms=3600000, priority=1),
    )


def _node(node_id: str = "n_root", parent: list[str] | None = None, spec: m.TaskSpec | None = None) -> m.TaskNode:
    return m.TaskNode(node_id=node_id, depends_on=parent if parent is not None else [], task_spec=spec or _spec())


# ---------------------------------------------------------------------------
# serde roundtrip
# ---------------------------------------------------------------------------


def test_frozen_dataclasses_are_immutable():
    node = _node()
    with pytest.raises(FrozenInstanceError):
        node.node_id = "other"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        node.run_info.run_mode = m.RunMode.SINGLE_BOT  # type: ignore[misc]


def test_asdict_contains_all_fields_at_every_depth():
    node = _node("N_market", ["n_root"], _spec([m.AcceptanceCriteria(id="ac", description="d", tag="node")]))
    d = dataclasses.asdict(node)
    assert d["node_id"] == "N_market"
    assert d["depends_on"] == ["n_root"]
    assert d["status"] == m.Status.PENDING
    assert d["task_spec"]["metadata"]["id"] == "t1"
    assert d["task_spec"]["goal"]["acceptances"][0]["tag"] == "node"
    assert d["run_info"]["run_mode"] is None
    assert d["run_info"]["output"] == {}
    assert d["run_info"]["collab_mode"] is None


def test_serde_roundtrip_rebuilds_equal_instance():
    original = m.TaskExecutionGraph(
        status=m.Status.RUNNING,
        loop_round=1,
        output={"k": "v"},
        tasks=[_node("n_root")],
        extend_props={"ek": "ev"},
    )
    dumped = dataclasses.asdict(original)
    rebuilt = m.TaskExecutionGraph(
        status=dumped["status"],
        loop_round=dumped["loop_round"],
        output=dumped["output"],
        tasks=[m.TaskNode(**_node_kwargs(t)) for t in dumped["tasks"]],
        extend_props=dumped["extend_props"],
    )
    assert rebuilt == original
    # frozen -> 重建后是新对象,不是同一对象
    assert rebuilt is not original
    assert rebuilt.tasks[0] is not original.tasks[0]


def _node_kwargs(d: dict) -> dict:
    """从 asdict(node) 的 dict 重建 TaskNode 构造 kwargs (还原嵌套 dataclass)."""
    return {
        "node_id": d["node_id"],
        "depends_on": d["depends_on"],
        "task_spec": _spec_from_dict(d["task_spec"]),
        "status": d["status"],
        "run_info": m.RuntimeInfo(**d["run_info"]),
    }


def _spec_from_dict(d: dict) -> m.TaskSpec:
    return m.TaskSpec(
        metadata=m.Metadata(**d["metadata"]),
        context=m.Context(**d["context"]),
        goal=m.Goal(
            objective=d["goal"]["objective"],
            acceptances=[m.AcceptanceCriteria(**a) for a in d["goal"]["acceptances"]],
        ),
        sla=m.SLA(**d["sla"]),
    )


# ---------------------------------------------------------------------------
# AcceptanceCriteria.tag scope 合法值
# ---------------------------------------------------------------------------


def test_scope_enum_values_are_node_subtree_task():
    assert {s.value for s in m.Scope} == {"node", "subtree", "task"}
    assert m.Scope("task") is m.Scope.TASK
    assert str(m.Scope.NODE) == "node"


def test_acceptance_criteria_tag_accepts_any_str():
    # 领域模型 owner 可将 tag 当通用标签;构造期不约束
    ac = m.AcceptanceCriteria(id="ac", description="d", tag="custom_tag")
    assert ac.tag == "custom_tag"


def test_scope_membership_guard_rejects_unknown_tag():
    # 模拟 compute_output_projection 的 scope 语义校验
    valid = {s.value for s in m.Scope}

    def is_valid_scope(tag: str) -> bool:
        return tag in valid

    assert is_valid_scope("node")
    assert is_valid_scope("subtree")
    assert is_valid_scope("task")
    assert not is_valid_scope("custom_tag")
    assert not is_valid_scope("")


# ---------------------------------------------------------------------------
# v4: 5 态合法性 + 枚举完备 (删 SPAWNING)
# ---------------------------------------------------------------------------


def test_status_has_exactly_five_states():
    names = {s.name for s in m.Status}
    assert names == {"PENDING", "RUNNING", "DONE", "FAILED", "HUNG"}
    assert len(list(m.Status)) == 5


def test_status_values_are_snake_case():
    values = {s.value for s in m.Status}
    assert values == {"pending", "running", "done", "failed", "hung"}
    assert m.Status("running") is m.Status.RUNNING


def test_status_no_spawning():
    """v4: SPAWNING 已删除;"委托中" = 结构派生 (decomposition_children)."""
    assert not hasattr(m.Status, "SPAWNING")
    with pytest.raises(ValueError):
        m.Status("spawning")


def test_acceptance_verdict_members():
    assert {v.name for v in m.AcceptanceVerdict} == {"PASS", "FAIL"}


def test_collab_mode_aligned_with_bcs_group_strategy():
    # 对齐 BCS GroupStrategy{Chat, ManagerWorker, StateMachine}
    assert {c.name for c in m.CollabMode} == {"CHAT", "MANAGER_WORKER", "STATE_MACHINE"}
    assert m.CollabMode("manager_worker") is m.CollabMode.MANAGER_WORKER


def test_run_mode_members():
    assert {r.name for r in m.RunMode} == {"SINGLE_BOT", "COOP_GROUP", "BBS"}


def test_search_outcome_and_dispatch_and_executor_enums():
    assert {s.name for s in m.SearchOutcome} == {"HIT_SINGLE", "HIT_GROUP", "HIT_MULTI_BOTS", "MISS"}
    assert {d.name for d in m.DispatchKind} == {"DISPATCHED", "MISS"}
    assert {e.name for e in m.ExecutorStatus} == {"DONE", "STUCK", "FAILED"}


# ---------------------------------------------------------------------------
# 无遗留/派生字段残留在模型 (design Q1/Q6): depth / dependencies_satisfied / phase / NodeType / Edge
# ---------------------------------------------------------------------------


def test_task_node_fields_no_derived_or_legacy():
    fields = {f.name for f in dataclasses.fields(m.TaskNode)}
    assert fields == {"node_id", "depends_on", "task_spec", "status", "run_info"}
    # 关键反断言: depth 与 dependencies_satisfied 是核内派生,不在模型
    assert "depth" not in fields
    assert "dependencies_satisfied" not in fields
    assert "phase" not in fields
    assert "node_type" not in fields
    assert "edges" not in fields


def test_runtime_info_fields_include_collab_mode_no_depth():
    fields = {f.name for f in dataclasses.fields(m.RuntimeInfo)}
    assert "collab_mode" in fields          # ★ 框架侧扩展
    assert "depth" not in fields            # 核内派生,不入模
    assert "run_mode" in fields
    assert "acceptance_result" in fields
    assert "extend_props" in fields


def test_runtime_info_collab_mode_defaults_none_for_non_coop():
    ri = m.RuntimeInfo()
    assert ri.collab_mode is None
    assert ri.run_mode is None
    assert ri.output == {}


def test_groupformation_workflow_yaml_optional():
    chat = m.GroupFormation(collab_mode=m.CollabMode.CHAT, member_bots=["b1"], lead_bot="b1")
    assert chat.workflow_yaml is None
    sm = m.GroupFormation(
        collab_mode=m.CollabMode.STATE_MACHINE,
        member_bots=["b1", "b2"],
        lead_bot="b1",
        workflow_yaml="states: [s1, s2]",
    )
    assert sm.workflow_yaml == "states: [s1, s2]"


def test_task_graph_info_is_readonly_projection_alias():
    # 看板只读投影 = TaskExecutionGraph,零新增类型
    assert m.TaskGraphInfo is m.TaskExecutionGraph


def test_no_legacy_attributes_on_module():
    legacy = {"phase", "NodeType", "Edge", "SubGraphPlan", "ExecutorOutcome"}
    for name in legacy:
        assert not hasattr(m, name), f"模型模块不应残留遗留符号: {name}"
