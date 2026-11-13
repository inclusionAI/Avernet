"""L5 Open 方法剖面(ACCS-304):三个内部对象值对象(spec §4.4)。

``OpenOpportunity`` / ``ParticipationApplication`` / ``AdmissionDecision``
与 spec §4.4 YAML 逐字段对齐;承状态对象(opportunity/application)附
§6.3 审计四字段,每次状态转换覆写于对象;``AdmissionDecision`` 是录取
裁决的实纬记录(不承状态迁移,spec §4.4 无对应状态机),沿
``GroupRoster`` 先例不带审计四字段。状态词汇与 G4 注册机器
(``METHOD_MACHINE_REGISTRY`` 的 open_opportunity / open_application
两键)在模块加载期强制对齐——漂移即抛
``ContextCollabValidationError``(防漂移自检沿 swarm/single/group
先例)。Import 方向安全:本模块 import g4 状态机注册表,g4 不反向
import 本模块。

字段校验助手(``_required_str`` / ``_optional_str`` / ``_audit_str`` /
``_str_tuple``)自 ``method_binding`` 原样 import 复用——跨模块拷贝是
值对象纪律间的唯一漂移通道(沿 Task 9 先例);``_positive_int_or_none``
是 Open 独一的 capacity 字段校验,本模块内单点维护。所有 ``*_ref``
只做格式校验(非空前缀形状),存在性校验是 P2(spec §4.2 引用校验
边界裁决)。``opens_at`` / ``closes_at`` / ``submitted_at`` 是 ISO
时间串(解析与窗口裁决在服务层),值对象只保证非空串形状。

P1 裁决(spec §4.4):录取仅对 ``admitted_scope_ref`` 生效,不产生
Space Membership / Grant(G1 断言在服务与测试面);``COLLET_PARTICIPANT``
参与类型 P1 拒绝(L4 参与者治理, P2)。
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


class OpportunityState(str, Enum):
    """Open 机会状态(spec §4.4);wire 值逐字对齐 G4 注册机器。"""

    DRAFT = "DRAFT"
    OPEN = "OPEN"
    PAUSED = "PAUSED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"

    @classmethod
    def from_wire(cls, value: str) -> "OpportunityState":
        """从 wire 字符串恢复枚举;未知值抛 ``ContextCollabValidationError``。"""
        try:
            return cls(value)
        except ValueError:
            raise ContextCollabValidationError(
                f"unknown open opportunity state wire value: {value!r}"
            ) from None


class ApplicationState(str, Enum):
    """参与申请状态(spec §4.4);wire 值逐字对齐 G4 注册机器。"""

    SUBMITTED = "SUBMITTED"
    SCREENING = "SCREENING"
    ADMITTED = "ADMITTED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"
    REVOKED = "REVOKED"

    @classmethod
    def from_wire(cls, value: str) -> "ApplicationState":
        """从 wire 字符串恢复枚举;未知值抛 ``ContextCollabValidationError``。"""
        try:
            return cls(value)
        except ValueError:
            raise ContextCollabValidationError(
                f"unknown open application state wire value: {value!r}"
            ) from None


class ParticipationType(str, Enum):
    """参与类型(spec §4.4 YAML 闭合词汇);

    ``COLLET_PARTICIPANT`` P1 拒绝(spec §4.4 作者裁决:L4 参与者治理
    是 P2),wire 值仍合法——枚举只定义形状,拒绝在服务层裁决。
    """

    COLLET_PARTICIPANT = "COLLET_PARTICIPANT"
    GROUP_MEMBER = "GROUP_MEMBER"
    SWARM_ALLOCATION = "SWARM_ALLOCATION"
    SINGLE_CONTRIBUTION = "SINGLE_CONTRIBUTION"

    @classmethod
    def from_wire(cls, value: str) -> "ParticipationType":
        """从 wire 字符串恢复枚举;未知值抛 ``ContextCollabValidationError``。"""
        try:
            return cls(value)
        except ValueError:
            raise ContextCollabValidationError(
                f"unknown open participation type wire value: {value!r}"
            ) from None


def _assert_state_machine_alignments() -> None:
    """模块加载期防漂移:两个状态枚举词汇必须逐字对齐注册机器(G4 纪律)。"""
    expectations = (
        (OpportunityState, "open_opportunity"),
        (ApplicationState, "open_application"),
    )
    for enum_cls, machine_key in expectations:
        machine_states = {
            state.name for state in METHOD_MACHINE_REGISTRY[machine_key].states
        }
        enum_states = {state.value for state in enum_cls}
        if machine_states != enum_states:
            raise ContextCollabValidationError(
                f"{enum_cls.__name__} state vocabulary drifted from the "
                f"registered {machine_key!r} G4 state machine: "
                f"{sorted(machine_states)} vs {sorted(enum_states)}"
            )


_assert_state_machine_alignments()


# from_dict 接受的封闭字段集;未知字段拒绝(沿 SingleSelection 同款纪律)。
_OPPORTUNITY_FIELDS = (
    "opportunity_id",
    "method_binding_id",
    "scope_ref",
    "participation_type",
    "requirement_refs",
    "admission_policy_refs",
    "capacity",
    "opens_at",
    "closes_at",
    "state",
    "transitioned_from",
    "transition_reason",
    "transitioned_by",
    "transitioned_at",
)

_APPLICATION_FIELDS = (
    "application_id",
    "opportunity_ref",
    "applicant_agent_ref",
    "accountable_principal_ref",
    "capability_evidence_refs",
    "proposed_artifact_refs",
    "requested_role_refs",
    "state",
    "submitted_at",
    "transitioned_from",
    "transition_reason",
    "transitioned_by",
    "transitioned_at",
)

_DECISION_FIELDS = (
    "admission_decision_id",
    "application_ref",
    "decision_ref",
    "admitted_scope_ref",
    "resulting_binding_refs",
)


def _positive_int_or_none(data: dict, key: str, owner: str) -> int | None:
    """取可缺省正整数字段;``None`` 是合法域态(Open 机会容量不设上限,
    spec §4.4 capacity: integer?);给了就必须是正整数(bool 不是)。"""
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ContextCollabValidationError(
            f"{owner}.{key} must be a positive integer or null, got: "
            f"{value!r}"
        )
    return value


@dataclass(frozen=True)
class OpenOpportunity:
    """Open 机会值对象(spec §4.4 YAML + §6.3 审计四字段)。

    ``participation_type`` 闭合四值;``capacity`` 为 ``None`` 表示不设
    容量上限(合法域态);``opens_at`` 是开放起点,``closes_at`` 可空
    (不闭窗);窗口归属裁决(发布形态与提交守卫)在服务层;状态迁移
    由 open_opportunity 机器裁决,审计四字段每次转换覆写于对象。
    """

    opportunity_id: str
    method_binding_id: str
    scope_ref: str
    participation_type: ParticipationType
    opens_at: str
    state: OpportunityState
    capacity: int | None = None
    requirement_refs: tuple[str, ...] = ()
    admission_policy_refs: tuple[str, ...] = ()
    closes_at: str | None = None
    transitioned_from: str = ""
    transition_reason: str = ""
    transitioned_by: str = ""
    transitioned_at: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "OpenOpportunity":
        """从 dict 恢复;字段集封闭(未知字段拒绝),校验规则:

        - ``opportunity_id`` / ``method_binding_id`` / ``scope_ref`` /
          ``opens_at`` 非空;``participation_type`` 经
          ``ParticipationType.from_wire``;
        - ``closes_at`` 可空(none 或非空串);capacity 是正整数或
          null;refs 元组缺失视为空、条目非空串;
        - ``state`` 经 ``OpportunityState.from_wire``;审计四字段
          必须存在且为字符串,允许空串。
        """
        if not isinstance(data, dict):
            raise ContextCollabValidationError(
                f"OpenOpportunity must be an object, got: {data!r}"
            )
        unknown = [key for key in data if key not in _OPPORTUNITY_FIELDS]
        if unknown:
            raise ContextCollabValidationError(
                f"unknown field(s) in OpenOpportunity: {sorted(unknown)}"
            )
        return cls(
            opportunity_id=_required_str(
                data, "opportunity_id", "OpenOpportunity"
            ),
            method_binding_id=_required_str(
                data, "method_binding_id", "OpenOpportunity"
            ),
            scope_ref=_required_str(data, "scope_ref", "OpenOpportunity"),
            participation_type=ParticipationType.from_wire(
                _required_str(data, "participation_type", "OpenOpportunity")
            ),
            opens_at=_required_str(data, "opens_at", "OpenOpportunity"),
            capacity=_positive_int_or_none(data, "capacity", "OpenOpportunity"),
            state=OpportunityState.from_wire(
                _required_str(data, "state", "OpenOpportunity")
            ),
            requirement_refs=_str_tuple(
                data, "requirement_refs", "OpenOpportunity"
            ),
            admission_policy_refs=_str_tuple(
                data, "admission_policy_refs", "OpenOpportunity"
            ),
            closes_at=_optional_str(data, "closes_at", "OpenOpportunity"),
            transitioned_from=_audit_str(
                data, "transitioned_from", "OpenOpportunity"
            ),
            transition_reason=_audit_str(
                data, "transition_reason", "OpenOpportunity"
            ),
            transitioned_by=_audit_str(data, "transitioned_by", "OpenOpportunity"),
            transitioned_at=_audit_str(data, "transitioned_at", "OpenOpportunity"),
        )

    def to_dict(self) -> dict:
        """输出全部声明字段;枚举取 wire 值、元组降为列表,保证
        ``from_dict(to_dict(x)) == x``。"""
        return {
            "opportunity_id": self.opportunity_id,
            "method_binding_id": self.method_binding_id,
            "scope_ref": self.scope_ref,
            "participation_type": self.participation_type.value,
            "requirement_refs": list(self.requirement_refs),
            "admission_policy_refs": list(self.admission_policy_refs),
            "capacity": self.capacity,
            "opens_at": self.opens_at,
            "closes_at": self.closes_at,
            "state": self.state.value,
            "transitioned_from": self.transitioned_from,
            "transition_reason": self.transition_reason,
            "transitioned_by": self.transitioned_by,
            "transitioned_at": self.transitioned_at,
        }


@dataclass(frozen=True)
class ParticipationApplication:
    """参与申请值对象(spec §4.4 YAML + §6.3 审计四字段)。

    ``applicant_agent_ref`` 是申请人 agent 引用;``accountable_principal_ref``
    是申请责任主体(必填,spec §4.4)——withdraw 的 self-help 比对目标;
    ``opportunity_ref`` 指向所属机会(值对象不携带 binding,键域隔离
    在存储层以显式 method_binding_id 承载);``submitted_at`` 在提交时
    落值;非终态 (opportunity, applicant) 至多一条的存储层唯一断言在
    open 切片(spec §6.1)。
    """

    application_id: str
    opportunity_ref: str
    applicant_agent_ref: str
    accountable_principal_ref: str
    state: ApplicationState
    submitted_at: str
    capability_evidence_refs: tuple[str, ...] = ()
    proposed_artifact_refs: tuple[str, ...] = ()
    requested_role_refs: tuple[str, ...] = ()
    transitioned_from: str = ""
    transition_reason: str = ""
    transitioned_by: str = ""
    transitioned_at: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "ParticipationApplication":
        """从 dict 恢复;字段集封闭(未知字段拒绝),校验规则:

        - ``application_id`` / ``opportunity_ref`` /
          ``applicant_agent_ref`` / ``accountable_principal_ref`` /
          ``submitted_at`` 非空;
        - refs 元组缺失视为空、条目非空串;
        - ``state`` 经 ``ApplicationState.from_wire``;审计四字段
          同款。
        """
        if not isinstance(data, dict):
            raise ContextCollabValidationError(
                f"ParticipationApplication must be an object, got: {data!r}"
            )
        unknown = [key for key in data if key not in _APPLICATION_FIELDS]
        if unknown:
            raise ContextCollabValidationError(
                f"unknown field(s) in ParticipationApplication: "
                f"{sorted(unknown)}"
            )
        return cls(
            application_id=_required_str(
                data, "application_id", "ParticipationApplication"
            ),
            opportunity_ref=_required_str(
                data, "opportunity_ref", "ParticipationApplication"
            ),
            applicant_agent_ref=_required_str(
                data, "applicant_agent_ref", "ParticipationApplication"
            ),
            accountable_principal_ref=_required_str(
                data, "accountable_principal_ref", "ParticipationApplication"
            ),
            state=ApplicationState.from_wire(
                _required_str(data, "state", "ParticipationApplication")
            ),
            submitted_at=_required_str(
                data, "submitted_at", "ParticipationApplication"
            ),
            capability_evidence_refs=_str_tuple(
                data, "capability_evidence_refs", "ParticipationApplication"
            ),
            proposed_artifact_refs=_str_tuple(
                data, "proposed_artifact_refs", "ParticipationApplication"
            ),
            requested_role_refs=_str_tuple(
                data, "requested_role_refs", "ParticipationApplication"
            ),
            transitioned_from=_audit_str(
                data, "transitioned_from", "ParticipationApplication"
            ),
            transition_reason=_audit_str(
                data, "transition_reason", "ParticipationApplication"
            ),
            transitioned_by=_audit_str(
                data, "transitioned_by", "ParticipationApplication"
            ),
            transitioned_at=_audit_str(
                data, "transitioned_at", "ParticipationApplication"
            ),
        )

    def to_dict(self) -> dict:
        """输出全部声明字段;枚举取 wire 值、元组降为列表,保证
        ``from_dict(to_dict(x)) == x``。"""
        return {
            "application_id": self.application_id,
            "opportunity_ref": self.opportunity_ref,
            "applicant_agent_ref": self.applicant_agent_ref,
            "accountable_principal_ref": self.accountable_principal_ref,
            "capability_evidence_refs": list(self.capability_evidence_refs),
            "proposed_artifact_refs": list(self.proposed_artifact_refs),
            "requested_role_refs": list(self.requested_role_refs),
            "state": self.state.value,
            "submitted_at": self.submitted_at,
            "transitioned_from": self.transitioned_from,
            "transition_reason": self.transition_reason,
            "transitioned_by": self.transitioned_by,
            "transitioned_at": self.transitioned_at,
        }


@dataclass(frozen=True)
class AdmissionDecision:
    """录取裁决值对象(spec §4.4 YAML;实纬记录,不承状态迁移、无审计
    四字段——spec §4.4 无对应状态机,沿 ``GroupRoster`` 先例)。

    ``decision_ref`` 指向产生本裁决的权威面操作(P1 由服务端派生,
    与 SwarmExecutionRecord.operation_ref 同款审计标识口径);
    ``admitted_scope_ref`` 可空(录取仅对它生效,不产生 Space
    Membership / Grant,G1);``resulting_binding_refs`` 是录取产物
    引用——GROUP_MEMBER 指向 group_member_binding、SWARM_ALLOCATION
    指向 stage allocation、SINGLE_CONTRIBUTION 仅记录 artifact refs
    不产生参与绑定(spec §4.4)。
    """

    admission_decision_id: str
    application_ref: str
    decision_ref: str
    admitted_scope_ref: str | None = None
    resulting_binding_refs: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: dict) -> "AdmissionDecision":
        """从 dict 恢复;字段集封闭(未知字段拒绝),校验规则:

        - ``admission_decision_id`` / ``application_ref`` / ``decision_ref``
          非空;``admitted_scope_ref`` 可空(none 或非空串);
        - ``resulting_binding_refs`` 缺失视为空元组、条目非空串。
        """
        if not isinstance(data, dict):
            raise ContextCollabValidationError(
                f"AdmissionDecision must be an object, got: {data!r}"
            )
        unknown = [key for key in data if key not in _DECISION_FIELDS]
        if unknown:
            raise ContextCollabValidationError(
                f"unknown field(s) in AdmissionDecision: {sorted(unknown)}"
            )
        return cls(
            admission_decision_id=_required_str(
                data, "admission_decision_id", "AdmissionDecision"
            ),
            application_ref=_required_str(
                data, "application_ref", "AdmissionDecision"
            ),
            decision_ref=_required_str(
                data, "decision_ref", "AdmissionDecision"
            ),
            admitted_scope_ref=_optional_str(
                data, "admitted_scope_ref", "AdmissionDecision"
            ),
            resulting_binding_refs=_str_tuple(
                data, "resulting_binding_refs", "AdmissionDecision"
            ),
        )

    def to_dict(self) -> dict:
        """输出全部声明字段;元组降为列表,保证
        ``from_dict(to_dict(x)) == x``。"""
        return {
            "admission_decision_id": self.admission_decision_id,
            "application_ref": self.application_ref,
            "decision_ref": self.decision_ref,
            "admitted_scope_ref": self.admitted_scope_ref,
            "resulting_binding_refs": list(self.resulting_binding_refs),
        }
