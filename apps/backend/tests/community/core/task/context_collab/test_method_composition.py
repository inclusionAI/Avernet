"""L5 组合矩阵 + MethodCompositionEdge 单测(Task 3)。

覆盖:
- ``COMPOSITION_MATRIX`` 逐行固化:Single/Group/Swarm × NEST/SUPPORT/
  DELEGATE_TO 允许任意核心方法(含复合值 ``Open + Group``);PARALLELIZE
  仅同 kind;ESCALATE 升级链;Swarm→escalate 显式空行;
- ``validate_composition`` 未知父/子方法拒绝、无行父×关系组合拒绝;
- ``CompositionRelation.from_wire`` 的 ``delegate-to`` 别名与未知值拒绝;
- ``detect_cycle`` 正反例(3 节点环/DAG/重复边/自环/空输入);
- ``MethodCompositionEdge.from_dict/to_dict`` 往返 + 缺字段/非法枚举拒绝;
- 矩阵与 ``METHOD_PROFILE_REGISTRY`` 声明的 composable_relations 对齐
  (含 Open 只作为子方法侧参与、不作为矩阵父方法的非对称性)。
"""
import dataclasses

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    MethodKind,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_composition import (
    COMPOSITION_MATRIX,
    CompositionEdgeState,
    CompositionRelation,
    MethodCompositionEdge,
    detect_cycle,
    validate_composition,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_profile import (
    METHOD_PROFILE_REGISTRY,
)

# 全部 wire 取值(含复合值 Open + Group)——nest/support/delegate_to 行
# 允许"任意核心方法"作为子方法,复合法同样合法。
ALL_METHOD_WIRES = frozenset(kind.value for kind in MethodKind)
# 矩阵父方法只有三个核心方法;Open 依非对称裁决不作为父方法出现。
PARENT_WIRES = ("Single", "Group", "Swarm")


def _edge_dict() -> dict:
    """构造一份合法的 MethodCompositionEdge dict(§3.3 字段 + §6.3 审计)。"""
    return {
        "edge_id": "edge-1",
        "collab_context_id": "ctx-1",
        "parent_binding": "binding-parent",
        "child_binding": "binding-child",
        "relation_kind": "delegate_to",
        "state": "ACTIVE",
        # 边直接以 ACTIVE 创建(§3.3 无 BOUND 前态),transitioned_from 留空
        # 表示"无前序边状态";时间戳由 store 层管理,这里仅是 ISO 字符串。
        "transitioned_from": "",
        "transition_reason": "delegate selected task",
        "transitioned_by": "principal-1",
        "transitioned_at": "2026-11-12T10:00:00Z",
    }


# ---------------------------------------------------------------------------
# 矩阵逐行固化
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("parent", PARENT_WIRES)
@pytest.mark.parametrize(
    "relation",
    [
        CompositionRelation.NEST,
        CompositionRelation.SUPPORT,
        CompositionRelation.DELEGATE_TO,
    ],
)
def test_matrix_any_parent_nest_support_delegate_allows_any_child(parent, relation):
    # 任意父方法 × nest/support/delegate_to → 任意核心方法,含复合值。
    assert COMPOSITION_MATRIX[(parent, relation)] == ALL_METHOD_WIRES


@pytest.mark.parametrize("parent", PARENT_WIRES)
@pytest.mark.parametrize(
    "relation",
    [
        CompositionRelation.NEST,
        CompositionRelation.SUPPORT,
        CompositionRelation.DELEGATE_TO,
    ],
)
def test_validate_accepts_any_child_for_nest_support_delegate(parent, relation):
    # 全部子方法 wire 值(含 Open 与 Open + Group)都应通过结构校验。
    for child in ALL_METHOD_WIRES:
        validate_composition(parent, relation, child)


@pytest.mark.parametrize(
    "parent,allowed",
    [
        ("Group", frozenset({"Group"})),
        ("Swarm", frozenset({"Swarm"})),
    ],
)
def test_matrix_parallelize_same_kind_only(parent, allowed):
    # PARALLELIZE 并行副本语义:仅允许同 kind,禁止跨 kind 伪装扩容。
    assert COMPOSITION_MATRIX[(parent, CompositionRelation.PARALLELIZE)] == allowed


def test_matrix_has_no_parallelize_row_for_single():
    # Single 不平行化:该父×关系组合不存在,任何子方法都拒绝。
    with pytest.raises(ContextCollabValidationError):
        validate_composition("Single", CompositionRelation.PARALLELIZE, "Single")


@pytest.mark.parametrize(
    "parent,allowed",
    [
        ("Single", frozenset({"Group", "Swarm"})),
        ("Group", frozenset({"Swarm"})),
    ],
)
def test_matrix_escalate_upgrade_chain(parent, allowed):
    assert COMPOSITION_MATRIX[(parent, CompositionRelation.ESCALATE)] == allowed


def test_matrix_escalate_rejects_non_upgrade_children():
    # Single 升级不能指向自身或 Open(只能升到更大参与规模)。
    for child in ("Single", "Open", "Open + Group"):
        with pytest.raises(ContextCollabValidationError):
            validate_composition("Single", CompositionRelation.ESCALATE, child)
    for child in ("Single", "Group", "Open", "Open + Group"):
        with pytest.raises(ContextCollabValidationError):
            validate_composition("Group", CompositionRelation.ESCALATE, child)


def test_matrix_swarm_escalate_is_explicit_empty_row():
    # Swarm 已是最大参与规模:显式空行(不是省略),任何子方法都拒绝。
    assert ("Swarm", CompositionRelation.ESCALATE) in COMPOSITION_MATRIX
    assert COMPOSITION_MATRIX[("Swarm", CompositionRelation.ESCALATE)] == frozenset()
    for child in ALL_METHOD_WIRES:
        with pytest.raises(ContextCollabValidationError):
            validate_composition("Swarm", CompositionRelation.ESCALATE, child)


def test_matrix_covers_exactly_the_pinned_rows():
    # 钉死行集合:9 行任意父 × nest/support/delegate_to + 2 行 parallelize
    # + 3 行 escalate(含 Swarm 空行),既无遗漏也无额外行。
    expected = {
        (parent, relation)
        for parent in PARENT_WIRES
        for relation in (
            CompositionRelation.NEST,
            CompositionRelation.SUPPORT,
            CompositionRelation.DELEGATE_TO,
        )
    } | {
        ("Group", CompositionRelation.PARALLELIZE),
        ("Swarm", CompositionRelation.PARALLELIZE),
        ("Single", CompositionRelation.ESCALATE),
        ("Group", CompositionRelation.ESCALATE),
        ("Swarm", CompositionRelation.ESCALATE),
    }
    assert set(COMPOSITION_MATRIX) == expected


# ---------------------------------------------------------------------------
# validate_composition 拒绝路径
# ---------------------------------------------------------------------------


def test_validate_rejects_unknown_parent_and_child():
    # 父/子都必须是合法 MethodKind wire 值。
    with pytest.raises(ContextCollabValidationError):
        validate_composition("Team", CompositionRelation.NEST, "Group")
    with pytest.raises(ContextCollabValidationError):
        validate_composition("Group", CompositionRelation.NEST, "Team")


@pytest.mark.parametrize("parent", ["Open", "Open + Group"])
def test_validate_rejects_parent_without_matrix_row(parent):
    # Open(含顶层复合 Open + Group)不发起组合:即使 wire 值合法,
    # 矩阵中也没有以其为父的行,报错需点出父×关系组合。
    with pytest.raises(
        ContextCollabValidationError, match=r"Open.*nest"
    ):
        validate_composition(parent, CompositionRelation.NEST, "Group")


# ---------------------------------------------------------------------------
# CompositionRelation wire 解析
# ---------------------------------------------------------------------------


def test_composition_relation_from_wire_accepts_delegate_to_alias():
    # 操作族用 "delegate-to"(连字符),wire 枚举值是 "delegate_to"。
    assert (
        CompositionRelation.from_wire("delegate-to") is CompositionRelation.DELEGATE_TO
    )
    for relation in (
        CompositionRelation.NEST,
        CompositionRelation.DELEGATE_TO,
        CompositionRelation.SUPPORT,
        CompositionRelation.PARALLELIZE,
        CompositionRelation.ESCALATE,
    ):
        assert CompositionRelation.from_wire(relation.value) is relation


def test_composition_relation_from_wire_rejects_unknown():
    for value in ("attach", "delegate", "NEST", ""):
        with pytest.raises(ContextCollabValidationError):
            CompositionRelation.from_wire(value)


# ---------------------------------------------------------------------------
# detect_cycle
# ---------------------------------------------------------------------------


def test_detect_cycle_positive_three_node():
    assert detect_cycle([("a", "b"), ("b", "c"), ("c", "a")]) is True


def test_detect_cycle_positive_in_second_component():
    # 环不在首个连通分量里,迭代 DFS 也必须发现。
    assert detect_cycle([("a", "b"), ("x", "y"), ("y", "x")]) is True


def test_detect_cycle_positive_self_loop_and_repeated_edges():
    assert detect_cycle([("a", "a")]) is True
    # 重复边不破坏检测:重复出现的有向边仍构成环。
    assert detect_cycle([("a", "b"), ("b", "a"), ("b", "a")]) is True


def test_detect_cycle_negative_dag():
    assert (
        detect_cycle([("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")]) is False
    )


def test_detect_cycle_negative_repeated_edges_are_safe():
    assert detect_cycle([("a", "b"), ("a", "b"), ("b", "c"), ("a", "c")]) is False


def test_detect_cycle_negative_empty_input():
    assert detect_cycle([]) is False
    assert detect_cycle([("a", "b")]) is False


# ---------------------------------------------------------------------------
# MethodCompositionEdge 值对象
# ---------------------------------------------------------------------------


def test_edge_from_dict_round_trip():
    data = _edge_dict()
    edge = MethodCompositionEdge.from_dict(data)
    assert edge.edge_id == "edge-1"
    assert edge.collab_context_id == "ctx-1"
    assert edge.parent_binding == "binding-parent"
    assert edge.child_binding == "binding-child"
    assert edge.relation_kind is CompositionRelation.DELEGATE_TO
    assert edge.state is CompositionEdgeState.ACTIVE
    assert edge.transition_reason == "delegate selected task"
    assert edge.transitioned_by == "principal-1"
    assert edge.transitioned_at == "2026-11-12T10:00:00Z"
    assert edge.to_dict() == data


def test_edge_from_dict_normalizes_delegate_to_alias():
    data = _edge_dict()
    data["relation_kind"] = "delegate-to"
    edge = MethodCompositionEdge.from_dict(data)
    # to_dict 输出 canonical 枚举值,而不是操作族别名。
    assert edge.relation_kind is CompositionRelation.DELEGATE_TO
    assert edge.to_dict()["relation_kind"] == "delegate_to"


def test_edge_from_dict_accepts_detached_state():
    data = _edge_dict()
    data["state"] = "DETACHED"
    edge = MethodCompositionEdge.from_dict(data)
    assert edge.state is CompositionEdgeState.DETACHED
    assert edge.to_dict()["state"] == "DETACHED"


@pytest.mark.parametrize(
    "missing",
    [
        "edge_id",
        "collab_context_id",
        "parent_binding",
        "child_binding",
        "relation_kind",
        "state",
        "transitioned_from",
        "transition_reason",
        "transitioned_by",
        "transitioned_at",
    ],
)
def test_edge_from_dict_requires_all_fields(missing):
    # 四个标识字段 + 审计四字段缺一不可(§3.3 + §6.3)。
    data = _edge_dict()
    del data[missing]
    with pytest.raises(ContextCollabValidationError):
        MethodCompositionEdge.from_dict(data)


@pytest.mark.parametrize(
    "key,value",
    [
        ("edge_id", ""),
        ("edge_id", "  "),
        ("collab_context_id", None),
        ("parent_binding", 123),
        ("child_binding", []),
    ],
)
def test_edge_from_dict_rejects_invalid_identifier_fields(key, value):
    # 标识字段必须是非空字符串。
    data = _edge_dict()
    data[key] = value
    with pytest.raises(ContextCollabValidationError):
        MethodCompositionEdge.from_dict(data)


@pytest.mark.parametrize(
    "key,value",
    [
        ("relation_kind", "attach"),
        ("relation_kind", "delegate_to "),
        ("state", "BOUND"),
        ("state", "active"),
        ("state", None),
    ],
)
def test_edge_from_dict_rejects_invalid_enum_wire_values(key, value):
    # relation/state 必须是合法 wire 枚举值(relation 另接受 delegate-to 别名)。
    data = _edge_dict()
    data[key] = value
    with pytest.raises(ContextCollabValidationError):
        MethodCompositionEdge.from_dict(data)


def test_edge_is_frozen():
    edge = MethodCompositionEdge.from_dict(_edge_dict())
    with pytest.raises(dataclasses.FrozenInstanceError):
        edge.state = CompositionEdgeState.DETACHED


# ---------------------------------------------------------------------------
# 矩阵 ↔ Profile 声明对齐(防漂移自检,ACCS-105 §8)
# ---------------------------------------------------------------------------


def test_matrix_agrees_with_profile_declarations():
    # 三个核心方法的非空行关系集合必须逐一等于 Profile 声明的
    # composable_relations;Swarm→escalate 的显式空行是禁止性声明,
    # 不计入"可发起的组合关系",与 Swarm Profile 无 escalate 一致。
    for name in ("Single", "Group", "Swarm"):
        profile = METHOD_PROFILE_REGISTRY[name]
        matrix_relations = {
            relation
            for (parent, relation) in COMPOSITION_MATRIX
            if parent == name and COMPOSITION_MATRIX[(parent, relation)]
        }
        assert matrix_relations == set(profile.composable_relations), name

    # Open 的非对称裁决:Open 不作为矩阵父方法(顶层 Open + Group 由
    # ACCS 组装,不是"Open 方法发起的组合");其 composable_relations
    # 声明的是 Open 作为子方法侧可参与的关系,矩阵里这三类关系的
    # 每个核心方法父行都允许 Open 作为子方法出现。
    assert "Open" not in {parent for parent, _relation in COMPOSITION_MATRIX}
    open_profile = METHOD_PROFILE_REGISTRY["Open"]
    assert set(open_profile.composable_relations) == {
        "nest",
        "delegate_to",
        "support",
    }
    for relation in (
        CompositionRelation.NEST,
        CompositionRelation.DELEGATE_TO,
        CompositionRelation.SUPPORT,
    ):
        for parent in PARENT_WIRES:
            assert "Open" in COMPOSITION_MATRIX[(parent, relation)]
