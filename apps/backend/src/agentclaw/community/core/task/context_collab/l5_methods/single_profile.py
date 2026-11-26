"""L5 Single 方法剖面(ACCS-301):``SelectionState`` + ``SingleSelection``。

``SingleSelection`` 是 Single Profile 的内部权威对象(spec §4.1 YAML +
§6.3 审计四字段):某个 method binding 在给定 scope 内对 agent 参与者的
一次选定。状态词汇与 G4 注册机器 ``METHOD_MACHINE_REGISTRY["single_selection"]``
(PROPOSED ─single.select→ ACTIVE ─single.replace→ REPLACED /
ACTIVE ─single.release→ RELEASED)在模块加载期强制对齐——漂移即抛
``ContextCollabValidationError``(防漂移自检沿 ``method_state_machines``
注册即校验的先例)。Import 方向安全:本模块 import g4 状态机注册表,
g4 不反向 import 本模块。

操作族(Select/Replace/Release/Delegate)与存储唯一断言(同 binding +
scope 至多一个 ACTIVE,RFC-301 §9 断言 1)在 store/服务层(T9)实施,
本模块只有值对象与词汇。

字段校验助手(``_required_str`` / ``_optional_str`` / ``_audit_str`` /
``_str_tuple``)自 ``method_binding`` 原样 import 复用——跨模块拷贝是
值对象纪律间的唯一漂移通道(沿 ``method_state_machines`` 直接 import
G4 校验原语的先例)。所有 ``*_ref`` 只做格式校验(非空前缀形状),
存在性校验是 P2(spec §4.2 引用校验边界裁决)。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.method_state_machines import (
    METHOD_MACHINE_REGISTRY,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    _audit_str,
    _optional_str,
    _required_str,
    _str_tuple,
)


class SelectionState(str, Enum):
    """Single selection 状态(spec §4.1);wire 值逐字对齐 G4 注册机器。"""

    PROPOSED = "PROPOSED"
    ACTIVE = "ACTIVE"
    REPLACED = "REPLACED"
    RELEASED = "RELEASED"

    @classmethod
    def from_wire(cls, value: str) -> "SelectionState":
        """从 wire 字符串恢复枚举;未知值抛 ``ContextCollabValidationError``。"""
        try:
            return cls(value)
        except ValueError:
            raise ContextCollabValidationError(
                f"unknown single selection state wire value: {value!r}"
            ) from None


def _assert_state_machine_alignment() -> None:
    """模块加载期防漂移:枚举词汇必须与注册机器逐字一致(G4 纪律)。"""
    machine_states = {
        state.name for state in METHOD_MACHINE_REGISTRY["single_selection"].states
    }
    enum_states = {state.value for state in SelectionState}
    if machine_states != enum_states:
        raise ContextCollabValidationError(
            "SingleSelection state vocabulary drifted from the registered "
            f"'single_selection' G4 state machine: {sorted(machine_states)} "
            f"vs {sorted(enum_states)}"
        )


_assert_state_machine_alignment()


# from_dict 接受的封闭字段集;未知字段拒绝(沿 MethodBinding 同款纪律)。
_SELECTION_FIELDS = (
    "selection_id",
    "method_binding_id",
    "scope_ref",
    "selected_agent_participant_ref",
    "selected_by_ref",
    "requirement_refs",
    "selection_evidence_refs",
    "state",
    "valid_from",
    "valid_until",
    "transitioned_from",
    "transition_reason",
    "transitioned_by",
    "transitioned_at",
)


@dataclass(frozen=True)
class SingleSelection:
    """Single selection 值对象(spec §4.1 YAML + §6.3 审计四字段)。

    ``selected_agent_participant_ref`` 是被选定的 agent 参与者引用
    (Select 必须显式携带——Query 结果不自动成为 selection);``selected_by_ref``
    是做出选择的责任主体(PrincipalRef 序列化);``scope_ref`` 限定作用域,
    同 (method_binding_id, scope_ref) 至多一个 ACTIVE 的唯一断言在 store
    层实施。``valid_from`` / ``valid_until`` 为 ISO 字符串,时间由 store
    层管理,本对象不做时钟假设;审计四字段每次状态转换覆写于对象。
    """

    selection_id: str
    method_binding_id: str
    scope_ref: str
    selected_agent_participant_ref: str
    selected_by_ref: str
    state: SelectionState
    valid_from: str
    requirement_refs: tuple[str, ...] = ()
    selection_evidence_refs: tuple[str, ...] = ()
    valid_until: str | None = None
    transitioned_from: str = ""
    transition_reason: str = ""
    transitioned_by: str = ""
    transitioned_at: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "SingleSelection":
        """从 dict 恢复;字段集封闭(未知字段拒绝),校验规则:

        - ``selection_id`` / ``method_binding_id`` / ``scope_ref`` /
          ``selected_agent_participant_ref`` / ``selected_by_ref`` 非空;
        - ``state`` 经 ``SelectionState.from_wire``;``valid_from`` 非空;
          ``valid_until`` 可空(none 或非空串);
        - refs 元组缺失视为空,元素必须非空字符串;
        - 审计四字段(§6.3)必须存在且为字符串,允许空串。
        """
        if not isinstance(data, dict):
            raise ContextCollabValidationError(
                f"SingleSelection must be an object, got: {data!r}"
            )
        unknown = [key for key in data if key not in _SELECTION_FIELDS]
        if unknown:
            raise ContextCollabValidationError(
                f"unknown field(s) in SingleSelection: {sorted(unknown)}"
            )
        return cls(
            selection_id=_required_str(data, "selection_id", "SingleSelection"),
            method_binding_id=_required_str(
                data, "method_binding_id", "SingleSelection"
            ),
            scope_ref=_required_str(data, "scope_ref", "SingleSelection"),
            selected_agent_participant_ref=_required_str(
                data, "selected_agent_participant_ref", "SingleSelection"
            ),
            selected_by_ref=_required_str(data, "selected_by_ref", "SingleSelection"),
            state=SelectionState.from_wire(
                _required_str(data, "state", "SingleSelection")
            ),
            valid_from=_required_str(data, "valid_from", "SingleSelection"),
            requirement_refs=_str_tuple(data, "requirement_refs", "SingleSelection"),
            selection_evidence_refs=_str_tuple(
                data, "selection_evidence_refs", "SingleSelection"
            ),
            valid_until=_optional_str(data, "valid_until", "SingleSelection"),
            transitioned_from=_audit_str(
                data, "transitioned_from", "SingleSelection"
            ),
            transition_reason=_audit_str(data, "transition_reason", "SingleSelection"),
            transitioned_by=_audit_str(data, "transitioned_by", "SingleSelection"),
            transitioned_at=_audit_str(data, "transitioned_at", "SingleSelection"),
        )

    def to_dict(self) -> dict:
        """输出全部声明字段;枚举取 wire 值、元组降为列表,保证
        ``from_dict(to_dict(x)) == x``。"""
        return {
            "selection_id": self.selection_id,
            "method_binding_id": self.method_binding_id,
            "scope_ref": self.scope_ref,
            "selected_agent_participant_ref": self.selected_agent_participant_ref,
            "selected_by_ref": self.selected_by_ref,
            "requirement_refs": list(self.requirement_refs),
            "selection_evidence_refs": list(self.selection_evidence_refs),
            "state": self.state.value,
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
            "transitioned_from": self.transitioned_from,
            "transition_reason": self.transition_reason,
            "transitioned_by": self.transitioned_by,
            "transitioned_at": self.transitioned_at,
        }
