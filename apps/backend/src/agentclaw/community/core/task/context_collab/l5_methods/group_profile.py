"""L5 Group 方法剖面(ACCS-302):``GroupRoster`` + ``GroupMemberBinding``。

``GroupRoster`` 是 binding 名下成员名册的乐观并发载体(spec §4.2
YAML):``revision`` 每次成员变更 +1(原子换人 ``ReplaceMember`` 为一次
bump),比对在存储锁内进行(RF #5,不匹配抛 ``ContextCollabVersionConflict``)。
``GroupMemberBinding`` 是单个成员行(同 YAML + §6.3 审计四字段),状态
词汇与 G4 注册机器 ``METHOD_MACHINE_REGISTRY["group_member"]``
(INVITED ─group.add_member→ ACTIVE ⇄ SUSPENDED;ACTIVE/SUSPENDED──
group.leave→ LEFT / group.remove_member→ REMOVED;ACTIVE ─
group.replace_member→ REPLACED)在模块加载期强制对齐——漂移即抛
``ContextCollabValidationError``(防漂移自检沿 ``single_profile`` 先例)。
Import 方向安全:本模块 import g4 状态机注册表,g4 不反向 import 本模块。

invite 建 INVITED 行、不经迁移规则;change_role 只写 ``role_binding_refs``
不迁态(roster revision 仍 +1)。操作编排在服务层(T10),本模块只有值
对象与词汇。

字段校验助手(``_required_str`` / ``_optional_str`` / ``_audit_str`` /
``_str_tuple``)自 ``method_binding`` 原样 import 复用——跨模块拷贝是
值对象纪律间的唯一漂移通道(沿 Task 9 先例)。所有 ``*_ref`` 只做格式
校验(非空前缀形状),存在性校验是 P2(spec §4.2 引用校验边界裁决)。
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


class MemberState(str, Enum):
    """Group 成员状态(spec §4.2);wire 值逐字对齐 G4 注册机器。"""

    INVITED = "INVITED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    LEFT = "LEFT"
    REMOVED = "REMOVED"
    REPLACED = "REPLACED"

    @classmethod
    def from_wire(cls, value: str) -> "MemberState":
        """从 wire 字符串恢复枚举;未知值抛 ``ContextCollabValidationError``。"""
        try:
            return cls(value)
        except ValueError:
            raise ContextCollabValidationError(
                f"unknown group member state wire value: {value!r}"
            ) from None


def _assert_state_machine_alignment() -> None:
    """模块加载期防漂移:枚举词汇必须与注册机器逐字一致(G4 纪律)。"""
    machine_states = {
        state.name for state in METHOD_MACHINE_REGISTRY["group_member"].states
    }
    enum_states = {state.value for state in MemberState}
    if machine_states != enum_states:
        raise ContextCollabValidationError(
            "GroupMemberBinding state vocabulary drifted from the registered "
            f"'group_member' G4 state machine: {sorted(machine_states)} "
            f"vs {sorted(enum_states)}"
        )


_assert_state_machine_alignment()

# 终态成员行集合(存储层"参与者未终态不得重复在册"裁断的依据):自注册
# 机器的治理类别派生(terminal_* 无出边),不建平行词汇。
MEMBER_TERMINAL_STATES = frozenset(
    MemberState.from_wire(state.name)
    for state in METHOD_MACHINE_REGISTRY["group_member"].states
    if state.governance_kind.startswith("terminal_")
)

# from_dict 接受的封闭字段集;未知字段拒绝(沿 SingleSelection 同款纪律)。
_ROSTER_FIELDS = (
    "roster_id",
    "method_binding_id",
    "revision",
)

_MEMBER_FIELDS = (
    "group_member_binding_id",
    "method_binding_id",
    "participant_ref",
    "address",
    "role_binding_refs",
    "state",
    "joined_at",
    "left_at",
    "transitioned_from",
    "transition_reason",
    "transitioned_by",
    "transitioned_at",
)


def _positive_int(data: dict, key: str, owner: str) -> int:
    """取正整数字段;bool 不是合法整数(bool 是 int 子类的既有纪律)。"""
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ContextCollabValidationError(
            f"{owner}.{key} must be a positive integer, got: {value!r}"
        )
    return value


@dataclass(frozen=True)
class GroupRoster:
    """Group roster 值对象(spec §4.2 YAML)。

    每 method binding 至多一份 roster;``revision`` 是成员变更的乐观并发
    版本,比对与 +1 在存储锁内进行,不匹配抛
    ``ContextCollabVersionConflict``(RF #5,409 语义)。roster 不承载
    状态迁移,故无审计四字段(§6.3 只约束状态承载对象)。
    """

    roster_id: str
    method_binding_id: str
    revision: int

    @classmethod
    def from_dict(cls, data: dict) -> "GroupRoster":
        """从 dict 恢复;字段集封闭(未知字段拒绝),``roster_id`` /
        ``method_binding_id`` 非空,``revision`` 正整数。"""
        if not isinstance(data, dict):
            raise ContextCollabValidationError(
                f"GroupRoster must be an object, got: {data!r}"
            )
        unknown = [key for key in data if key not in _ROSTER_FIELDS]
        if unknown:
            raise ContextCollabValidationError(
                f"unknown field(s) in GroupRoster: {sorted(unknown)}"
            )
        return cls(
            roster_id=_required_str(data, "roster_id", "GroupRoster"),
            method_binding_id=_required_str(
                data, "method_binding_id", "GroupRoster"
            ),
            revision=_positive_int(data, "revision", "GroupRoster"),
        )

    def to_dict(self) -> dict:
        """输出全部声明字段,保证 ``from_dict(to_dict(x)) == x``。"""
        return {
            "roster_id": self.roster_id,
            "method_binding_id": self.method_binding_id,
            "revision": self.revision,
        }


@dataclass(frozen=True)
class GroupMemberBinding:
    """Group 成员行值对象(spec §4.2 YAML + §6.3 审计四字段)。

    ``participant_ref`` 是成员参与者引用;``address`` 任意字符串(P1
    格式自由,缺省空串);``role_binding_refs`` 只做格式校验(非空串条目);
    ``joined_at`` 在进入 ACTIVE 时落值,``left_at`` 在离开 roster 的终态
    (LEFT/REMOVED/REPLACED)时落值;时间由 store 层的调用方管理,本对象
    不做时钟假设。审计四字段每次状态转换覆写于对象;创建行(invite/原子
    换人的新行 / 初始投影)不经迁移,``transitioned_from`` 落空串(沿
    MethodBinding 初始 BOUND 行先例)。
    """

    group_member_binding_id: str
    method_binding_id: str
    participant_ref: str
    state: MemberState
    address: str = ""
    role_binding_refs: tuple[str, ...] = ()
    joined_at: str | None = None
    left_at: str | None = None
    transitioned_from: str = ""
    transition_reason: str = ""
    transitioned_by: str = ""
    transitioned_at: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "GroupMemberBinding":
        """从 dict 恢复;字段集封闭(未知字段拒绝),校验规则:

        - ``group_member_binding_id`` / ``method_binding_id`` /
          ``participant_ref`` 非空;``state`` 经 ``MemberState.from_wire``;
        - ``address`` 必须是字符串(允许空串,invite 可不带地址);
        - ``role_binding_refs`` 缺失视为空,元素必须非空字符串;
        - ``joined_at`` / ``left_at`` 可空(none 或非空串);
        - 审计四字段(§6.3)必须存在且为字符串,允许空串。
        """
        if not isinstance(data, dict):
            raise ContextCollabValidationError(
                f"GroupMemberBinding must be an object, got: {data!r}"
            )
        unknown = [key for key in data if key not in _MEMBER_FIELDS]
        if unknown:
            raise ContextCollabValidationError(
                f"unknown field(s) in GroupMemberBinding: {sorted(unknown)}"
            )
        return cls(
            group_member_binding_id=_required_str(
                data, "group_member_binding_id", "GroupMemberBinding"
            ),
            method_binding_id=_required_str(
                data, "method_binding_id", "GroupMemberBinding"
            ),
            participant_ref=_required_str(
                data, "participant_ref", "GroupMemberBinding"
            ),
            state=MemberState.from_wire(
                _required_str(data, "state", "GroupMemberBinding")
            ),
            # address 与审计字段同规则:键存在、必须字符串、允许空串。
            address=_audit_str(data, "address", "GroupMemberBinding"),
            role_binding_refs=_str_tuple(
                data, "role_binding_refs", "GroupMemberBinding"
            ),
            joined_at=_optional_str(data, "joined_at", "GroupMemberBinding"),
            left_at=_optional_str(data, "left_at", "GroupMemberBinding"),
            transitioned_from=_audit_str(
                data, "transitioned_from", "GroupMemberBinding"
            ),
            transition_reason=_audit_str(
                data, "transition_reason", "GroupMemberBinding"
            ),
            transitioned_by=_audit_str(data, "transitioned_by", "GroupMemberBinding"),
            transitioned_at=_audit_str(data, "transitioned_at", "GroupMemberBinding"),
        )

    def to_dict(self) -> dict:
        """输出全部声明字段;枚举取 wire 值、元组降为列表,保证
        ``from_dict(to_dict(x)) == x``。"""
        return {
            "group_member_binding_id": self.group_member_binding_id,
            "method_binding_id": self.method_binding_id,
            "participant_ref": self.participant_ref,
            "address": self.address,
            "role_binding_refs": list(self.role_binding_refs),
            "state": self.state.value,
            "joined_at": self.joined_at,
            "left_at": self.left_at,
            "transitioned_from": self.transitioned_from,
            "transition_reason": self.transition_reason,
            "transitioned_by": self.transitioned_by,
            "transitioned_at": self.transitioned_at,
        }
