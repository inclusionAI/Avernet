"""L5 组合关系:CompositionRelation / 组合矩阵 / 环检测 / MethodCompositionEdge。

组合矩阵按 spec §3.3 表格逐格固化为版本化合同(spec §3.3 作者裁决):
任意核心方法为父时 NEST/SUPPORT/DELEGATE_TO 允许任意子方法;PARALLELIZE
仅同 kind(并行副本语义,禁止跨 kind 伪装扩容);ESCALATE 只能升级到更大
参与规模(Single→Group/Swarm、Group→Swarm、Swarm 无出边);Open 不作为
父方法——顶层 ``Open + Group`` 由 ACCS 组装,Open 的 composable_relations
声明其作为子方法侧的参与方式。组合边只存形态,由 store 层管理时间与
审计落值(§6.3),本模块不建持久化。
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from enum import Enum

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    MethodKind,
)

# 操作族使用连字符别名 "delegate-to",wire 枚举值为 "delegate_to"。
_DELEGATE_TO_ALIAS = "delegate-to"


class CompositionRelation(str, Enum):
    """组合关系种类(spec §3.3);wire 值与操作族映射逐一对应。"""

    NEST = "nest"
    DELEGATE_TO = "delegate_to"
    SUPPORT = "support"
    PARALLELIZE = "parallelize"
    ESCALATE = "escalate"

    @classmethod
    def from_wire(cls, value: str) -> "CompositionRelation":
        """从 wire 字符串恢复枚举;接受 ``delegate-to`` 别名,未知值抛校验错。"""
        if value == _DELEGATE_TO_ALIAS:
            return cls.DELEGATE_TO
        try:
            return cls(value)
        except ValueError:
            raise ContextCollabValidationError(
                f"unknown composition relation wire value: {value!r}"
            ) from None


class CompositionEdgeState(str, Enum):
    """组合边状态;DETACHED 保留审计,不物理删除(spec §3.3 detach)。"""

    ACTIVE = "ACTIVE"
    DETACHED = "DETACHED"

    @staticmethod
    def from_wire(value: str) -> "CompositionEdgeState":
        """从 wire 字符串恢复枚举;未知值抛 ``ContextCollabValidationError``。"""
        try:
            return CompositionEdgeState(value)
        except ValueError:
            raise ContextCollabValidationError(
                f"unknown composition edge state wire value: {value!r}"
            ) from None


# 任意核心方法(含复合值 Open + Group)——作为 nest/support/delegate_to
# 的子方法全部合法;"任意"行因此直接取 MethodKind 全部 wire 值。
_ALL_METHOD_WIRES = frozenset(kind.value for kind in MethodKind)

# 矩阵父方法:仅有三个核心方法剧本;Open 依非对称裁决不发起组合。
_PARENT_METHOD_WIRES = ("Single", "Group", "Swarm")
_OPEN_RELATIONS = (
    CompositionRelation.NEST,
    CompositionRelation.SUPPORT,
    CompositionRelation.DELEGATE_TO,
)

COMPOSITION_MATRIX: dict[tuple[str, CompositionRelation], frozenset[str]] = {
    # 任意父方法 × NEST/SUPPORT/DELEGATE_TO → 任意核心方法
    # (ACCS-301 §7:selected Agent 可将子 Task 委托给 Single、Group 或 Swarm)。
    **{
        (parent, relation): _ALL_METHOD_WIRES
        for parent in _PARENT_METHOD_WIRES
        for relation in _OPEN_RELATIONS
    },
    # PARALLELIZE 仅同 kind,禁止跨 kind 伪装扩容。
    ("Group", CompositionRelation.PARALLELIZE): frozenset({"Group"}),
    ("Swarm", CompositionRelation.PARALLELIZE): frozenset({"Swarm"}),
    # ESCALATE 只能升级到更大参与规模。
    ("Single", CompositionRelation.ESCALATE): frozenset({"Group", "Swarm"}),
    ("Group", CompositionRelation.ESCALATE): frozenset({"Swarm"}),
    # Swarm 已是最大参与规模:显式空行(不是省略),任何子方法都拒绝。
    ("Swarm", CompositionRelation.ESCALATE): frozenset(),
}


def validate_composition(
    parent_method: str,
    relation: CompositionRelation,
    child_method: str,
) -> None:
    """按组合矩阵校验父×关系×子组合;违规抛 ``ContextCollabValidationError``。

    父/子方法必须是合法 ``MethodKind`` wire 值;矩阵中不存在的
    (父, 关系) 行同样拒绝(报错点名该父×关系组合,如 Open 发起 nest、
    Single 发起 parallelize)。
    """
    parent_kind = MethodKind.from_wire(parent_method)
    child_kind = MethodKind.from_wire(child_method)
    allowed = COMPOSITION_MATRIX.get((parent_kind.value, relation))
    if allowed is None:
        raise ContextCollabValidationError(
            f"composition matrix has no row for parent={parent_kind.value!r} "
            f"relation={relation.value!r}"
        )
    if child_kind.value not in allowed:
        raise ContextCollabValidationError(
            f"child method {child_kind.value!r} is not allowed under "
            f"parent={parent_kind.value!r} via relation={relation.value!r}"
        )


def detect_cycle(pairs: Sequence[tuple[str, str]]) -> bool:
    """判断有向边集合(父→子的子方法流)中是否存在环。

    迭代 DFS(不递归,深度不受输入规模限制);空输入返回 ``False``;
    重复边安全——同一有向边多次出现不影响判定。仅当给定边中确实
    存在有向环时返回 ``True``。
    """
    adjacency: dict[str, list[str]] = {}
    for parent, child in pairs:
        adjacency.setdefault(parent, []).append(child)
    visiting: set[str] = set()
    done: set[str] = set()
    for start in adjacency:
        if start in done:
            continue
        visiting.add(start)
        stack: list[tuple[str, Iterator[str]]] = [(start, iter(adjacency[start]))]
        while stack:
            node, children = stack[-1]
            child = next(children, None)
            if child is None:
                stack.pop()
                visiting.discard(node)
                done.add(node)
                continue
            if child in visiting:
                return True
            if child not in done and child in adjacency:
                visiting.add(child)
                stack.append((child, iter(adjacency[child])))
    return False


def _required_str(data: dict, key: str, owner: str) -> str:
    """取必填非空字符串字段;缺失或空串抛校验错误。"""
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContextCollabValidationError(
            f"{owner}.{key} must be a non-empty string, got: {value!r}"
        )
    return value


def _audit_str(data: dict, key: str, owner: str) -> str:
    """取审计字段(§6.3);键必须存在且为字符串,允许空串(如边直接以
    ACTIVE 创建时 transitioned_from 无前序边状态)。"""
    value = data.get(key)
    if not isinstance(value, str):
        raise ContextCollabValidationError(
            f"{owner}.{key} must be a string, got: {value!r}"
        )
    return value


@dataclass(frozen=True)
class MethodCompositionEdge:
    """组合关系边(spec §3.3 字段 + §6.3 审计四字段)。

    组合边限定同一 ``collab_context_id`` 内,不跨 Collet;DETACHED 保留
    审计不物理删除。``transitioned_at`` 为 ISO 字符串,由 store 层管理
    时间,本对象不做时钟假设。
    """

    edge_id: str
    collab_context_id: str
    parent_binding: str
    child_binding: str
    relation_kind: CompositionRelation
    state: CompositionEdgeState
    transitioned_from: str
    transition_reason: str
    transitioned_by: str
    transitioned_at: str

    @classmethod
    def from_dict(cls, data: dict) -> "MethodCompositionEdge":
        """从 dict 恢复;标识四字段必须非空,relation 接受 ``delegate-to``
        别名,全部声明字段缺一不可,非法枚举 wire 值抛校验错误。"""
        return cls(
            edge_id=_required_str(data, "edge_id", "MethodCompositionEdge"),
            collab_context_id=_required_str(
                data, "collab_context_id", "MethodCompositionEdge"
            ),
            parent_binding=_required_str(data, "parent_binding", "MethodCompositionEdge"),
            child_binding=_required_str(data, "child_binding", "MethodCompositionEdge"),
            relation_kind=CompositionRelation.from_wire(
                _required_str(data, "relation_kind", "MethodCompositionEdge")
            ),
            state=CompositionEdgeState.from_wire(
                _required_str(data, "state", "MethodCompositionEdge")
            ),
            transitioned_from=_audit_str(
                data, "transitioned_from", "MethodCompositionEdge"
            ),
            transition_reason=_audit_str(
                data, "transition_reason", "MethodCompositionEdge"
            ),
            transitioned_by=_audit_str(data, "transitioned_by", "MethodCompositionEdge"),
            transitioned_at=_audit_str(
                data, "transitioned_at", "MethodCompositionEdge"
            ),
        )

    def to_dict(self) -> dict:
        """输出全部字段;relation 归一为 canonical 枚举值(不是操作族别名)。"""
        return {
            "edge_id": self.edge_id,
            "collab_context_id": self.collab_context_id,
            "parent_binding": self.parent_binding,
            "child_binding": self.child_binding,
            "relation_kind": self.relation_kind.value,
            "state": self.state.value,
            "transitioned_from": self.transitioned_from,
            "transition_reason": self.transition_reason,
            "transitioned_by": self.transitioned_by,
            "transitioned_at": self.transitioned_at,
        }
