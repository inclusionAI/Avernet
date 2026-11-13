"""L5 Swarm 方法剖面(ACCS-303)五个内部对象值对象(spec §4.3)。

``SwarmPlan`` / ``Stage`` / ``StageAllocation`` / ``SwarmExecutionProfile``
/ ``SwarmExecutionRecord`` 与 spec §4.3 YAML 逐字段对齐;承状态对象
(plan/stage/allocation/execution record)附 §6.3 审计四字段,每次状态
转换覆写于对象;``SwarmExecutionProfile`` 不承状态迁移(P1 经
DeclareExecutionProfile 声明即活,spec §4.3 作者裁决),沿
``GroupRoster`` 先例不带审计字段。状态词汇与 G4 注册机器
(METHOD_MACHINE_REGISTRY 的 swarm_plan / swarm_stage /
swarm_allocation / swarm_execution_record 四键)在模块加载期强制对齐——
漂移即抛 ``ContextCollabValidationError``(防漂移自检沿
``single_profile`` / ``group_profile`` 先例)。Import 方向安全:本模块
import g4 状态机注册表,g4 不反向 import 本模块。

字段校验助手(``_required_str`` / ``_optional_str`` / ``_audit_str`` /
``_str_tuple``)自 ``method_binding`` 原样 import 复用——跨模块拷贝是
值对象纪律间的唯一漂移通道(沿 Task 9 先例);``_required_dict`` /
``_optional_dict`` 是 Swarm 独有的对象类字段(termination_condition /
completion_condition / lifecycle_mapping)校验,本模块内单点维护。
所有 ``*_ref`` 只做格式校验(非空前缀形状),存在性校验是 P2(spec §4.2
引用校验边界裁决)。

P1 裁决两条(spec §2 / §8.1 引注):
- Computation Plane 派发不实现:execution record 仅由权威面操作
  创建/查询,``external_execution_id`` 恒为来源操作的审计标识;计划候选
  的 provenance 行尚无关联执行 profile,``execution_profile_ref``
  允许空串——P2 接入外部计算系统后强制非空。
- Swarm lease(spec §8.1):allocation 对象存 ``lease_expires_at``,
  过期检查是 AllocateStage/CompleteStage 的结构断言,无后台续租扫描。
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
from agentclaw.community.core.task.context_collab.l5_methods.method_composition import (
    detect_cycle,
)


class PlanState(str, Enum):
    """Swarm 计划状态(spec §4.3);wire 值逐字对齐 G4 注册机器。"""

    DRAFT = "DRAFT"
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @classmethod
    def from_wire(cls, value: str) -> "PlanState":
        """从 wire 字符串恢复枚举;未知值抛 ``ContextCollabValidationError``。"""
        try:
            return cls(value)
        except ValueError:
            raise ContextCollabValidationError(
                f"unknown swarm plan state wire value: {value!r}"
            ) from None


class StageState(str, Enum):
    """Swarm Stage 状态(spec §4.3);wire 值逐字对齐 G4 注册机器。"""

    PENDING = "PENDING"
    READY = "READY"
    ALLOCATING = "ALLOCATING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"

    @classmethod
    def from_wire(cls, value: str) -> "StageState":
        """从 wire 字符串恢复枚举;未知值抛 ``ContextCollabValidationError``。"""
        try:
            return cls(value)
        except ValueError:
            raise ContextCollabValidationError(
                f"unknown swarm stage state wire value: {value!r}"
            ) from None


class AllocationState(str, Enum):
    """Stage 分配状态(spec §4.3);wire 值逐字对齐 G4 注册机器。"""

    PROPOSED = "PROPOSED"
    ACTIVE = "ACTIVE"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"

    @classmethod
    def from_wire(cls, value: str) -> "AllocationState":
        """从 wire 字符串恢复枚举;未知值抛 ``ContextCollabValidationError``。"""
        try:
            return cls(value)
        except ValueError:
            raise ContextCollabValidationError(
                f"unknown swarm allocation state wire value: {value!r}"
            ) from None


class ExecutionRecordState(str, Enum):
    """Swarm 执行记录状态(spec §4.3);wire 值逐字对齐 G4 注册机器。"""

    SUBMITTED = "SUBMITTED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @classmethod
    def from_wire(cls, value: str) -> "ExecutionRecordState":
        """从 wire 字符串恢复枚举;未知值抛 ``ContextCollabValidationError``。"""
        try:
            return cls(value)
        except ValueError:
            raise ContextCollabValidationError(
                f"unknown swarm execution record state wire value: {value!r}"
            ) from None


class AllocationTargetKind(str, Enum):
    """分配目标类型(spec §4.3 YAML):AGENT(成员 agent)或 COMPUTE
    (原生算力,NATIVE_COMPUTE profile)。"""

    AGENT = "AGENT"
    COMPUTE = "COMPUTE"

    @classmethod
    def from_wire(cls, value: str) -> "AllocationTargetKind":
        """从 wire 字符串恢复枚举;未知值抛 ``ContextCollabValidationError``。"""
        try:
            return cls(value)
        except ValueError:
            raise ContextCollabValidationError(
                f"unknown swarm allocation target kind wire value: {value!r}"
            ) from None


class ExecutionMode(str, Enum):
    """执行剖面模式(spec §4.3 YAML):A2A_AGENT 走 A2A 派发,
    NATIVE_COMPUTE 走原生算力派发;P1 只落语义,两种模式的真实派发
    均为 P2(spec §2 Computation Plane 裁决)。"""

    A2A_AGENT = "A2A_AGENT"
    NATIVE_COMPUTE = "NATIVE_COMPUTE"

    @classmethod
    def from_wire(cls, value: str) -> "ExecutionMode":
        """从 wire 字符串恢复枚举;未知值抛 ``ContextCollabValidationError``。"""
        try:
            return cls(value)
        except ValueError:
            raise ContextCollabValidationError(
                f"unknown swarm execution mode wire value: {value!r}"
            ) from None


# supported_work_kinds 的闭合词汇(spec §4.3 YAML)。
SWARM_WORK_KINDS = frozenset(
    {"PLAN", "MATCH", "SCHEDULE", "EVALUATE", "EXECUTE"}
)


def _assert_state_machine_alignments() -> None:
    """模块加载期防漂移:四个状态枚举词汇必须逐字对齐注册机器(G4 纪律)。"""
    expectations = (
        (PlanState, "swarm_plan"),
        (StageState, "swarm_stage"),
        (AllocationState, "swarm_allocation"),
        (ExecutionRecordState, "swarm_execution_record"),
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


def _required_dict(data: dict, key: str, owner: str) -> dict:
    """取必填对象类字段;缺失或非 dict 抛校验错误(允许空 dict——
    "结构存在"的语义裁决在服务层完成,值对象只保证类型形状)。"""
    value = data.get(key)
    if not isinstance(value, dict):
        raise ContextCollabValidationError(
            f"{owner}.{key} must be an object, got: {value!r}"
        )
    return value


def _optional_dict(data: dict, key: str, owner: str) -> dict:
    """取可缺省对象类字段;缺失视为空 dict,非 dict 拒绝。"""
    value = data.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ContextCollabValidationError(
            f"{owner}.{key} must be an object, got: {value!r}"
        )
    return value


# from_dict 接受的封闭字段集;未知字段拒绝(沿 SingleSelection 同款纪律)。
_PLAN_FIELDS = (
    "swarm_plan_id",
    "method_binding_id",
    "task_ref",
    "max_parallelism",
    "max_depth",
    "budget_resource_refs",
    "termination_condition",
    "state",
    "transitioned_from",
    "transition_reason",
    "transitioned_by",
    "transitioned_at",
)

_STAGE_FIELDS = (
    "stage_id",
    "swarm_plan_ref",
    "task_ref",
    "name",
    "dependency_stage_refs",
    "requirement_refs",
    "input_artifact_refs",
    "output_artifact_roles",
    "completion_condition",
    "state",
    "transitioned_from",
    "transition_reason",
    "transitioned_by",
    "transitioned_at",
)

_ALLOCATION_FIELDS = (
    "allocation_id",
    "stage_ref",
    "target_kind",
    "target_ref",
    "execution_profile_ref",
    "lease_ref",
    "lease_expires_at",
    "qualification_evidence_refs",
    "authorization_evidence_refs",
    "attempt",
    "state",
    "transitioned_from",
    "transition_reason",
    "transitioned_by",
    "transitioned_at",
)

_EXECUTION_PROFILE_FIELDS = (
    "execution_profile_id",
    "mode",
    "target_ref",
    "supported_work_kinds",
    "input_contract_ref",
    "output_contract_ref",
    "lifecycle_mapping",
)

_EXECUTION_RECORD_FIELDS = (
    "execution_record_id",
    "operation_ref",
    "stage_allocation_ref",
    "execution_profile_ref",
    "external_execution_id",
    "state",
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
class SwarmPlan:
    """Swarm 计划值对象(spec §4.3 YAML + §6.3 审计四字段)。

    ``max_parallelism`` / ``max_depth`` 是结构上限(依赖图深度与同层
    并行宽度的裁决输入);``budget_resource_refs`` 只做格式校验(非空串
    条目);``termination_condition`` 必须是 dict(计划终止条件的结构
    由提交方声明);状态迁移由 swarm_plan 机器裁决,审计四字段每次
    转换覆写于对象。
    """

    swarm_plan_id: str
    method_binding_id: str
    task_ref: str
    max_parallelism: int
    max_depth: int
    state: PlanState
    termination_condition: dict
    budget_resource_refs: tuple[str, ...] = ()
    transitioned_from: str = ""
    transition_reason: str = ""
    transitioned_by: str = ""
    transitioned_at: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "SwarmPlan":
        """从 dict 恢复;字段集封闭(未知字段拒绝),校验规则:

        - ``swarm_plan_id`` / ``method_binding_id`` / ``task_ref`` 非空;
        - ``max_parallelism`` / ``max_depth`` 正整数;
        - ``termination_condition`` 必须存在且为 dict;
        - budget refs 缺失视为空,元素必须非空字符串;
        - ``state`` 经 ``PlanState.from_wire``;审计四字段必须存在
          且为字符串,允许空串。
        """
        if not isinstance(data, dict):
            raise ContextCollabValidationError(
                f"SwarmPlan must be an object, got: {data!r}"
            )
        unknown = [key for key in data if key not in _PLAN_FIELDS]
        if unknown:
            raise ContextCollabValidationError(
                f"unknown field(s) in SwarmPlan: {sorted(unknown)}"
            )
        return cls(
            swarm_plan_id=_required_str(data, "swarm_plan_id", "SwarmPlan"),
            method_binding_id=_required_str(
                data, "method_binding_id", "SwarmPlan"
            ),
            task_ref=_required_str(data, "task_ref", "SwarmPlan"),
            max_parallelism=_positive_int(
                data, "max_parallelism", "SwarmPlan"
            ),
            max_depth=_positive_int(data, "max_depth", "SwarmPlan"),
            state=PlanState.from_wire(_required_str(data, "state", "SwarmPlan")),
            budget_resource_refs=_str_tuple(
                data, "budget_resource_refs", "SwarmPlan"
            ),
            termination_condition=_required_dict(
                data, "termination_condition", "SwarmPlan"
            ),
            transitioned_from=_audit_str(
                data, "transitioned_from", "SwarmPlan"
            ),
            transition_reason=_audit_str(
                data, "transition_reason", "SwarmPlan"
            ),
            transitioned_by=_audit_str(data, "transitioned_by", "SwarmPlan"),
            transitioned_at=_audit_str(data, "transitioned_at", "SwarmPlan"),
        )

    def to_dict(self) -> dict:
        """输出全部声明字段;枚举取 wire 值、元组降为列表,保证
        ``from_dict(to_dict(x)) == x``。"""
        return {
            "swarm_plan_id": self.swarm_plan_id,
            "method_binding_id": self.method_binding_id,
            "task_ref": self.task_ref,
            "max_parallelism": self.max_parallelism,
            "max_depth": self.max_depth,
            "budget_resource_refs": list(self.budget_resource_refs),
            "termination_condition": self.termination_condition,
            "state": self.state.value,
            "transitioned_from": self.transitioned_from,
            "transition_reason": self.transition_reason,
            "transitioned_by": self.transitioned_by,
            "transitioned_at": self.transitioned_at,
        }


@dataclass(frozen=True)
class Stage:
    """Swarm Stage(阶段)值对象(spec §4.3 YAML + §6.3 审计四字段)。

    ``dependency_stage_refs`` 是同计划内前置 Stage 引用(格式校验;
    图结构校验——引用存在性、无环、深度/宽度——在 ValidatePlan 报告面,
    spec §4.3);``completion_condition`` 必须存在且为 dict(空 dict 允许,
    "完成条件结构存在"的服务层裁决拒绝空条件);``output_artifact_roles``
    只做格式校验。
    """

    stage_id: str
    swarm_plan_ref: str
    task_ref: str
    name: str
    state: StageState
    completion_condition: dict
    dependency_stage_refs: tuple[str, ...] = ()
    requirement_refs: tuple[str, ...] = ()
    input_artifact_refs: tuple[str, ...] = ()
    output_artifact_roles: tuple[str, ...] = ()
    transitioned_from: str = ""
    transition_reason: str = ""
    transitioned_by: str = ""
    transitioned_at: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "Stage":
        """从 dict 恢复;字段集封闭(未知字段拒绝),校验规则:

        - ``stage_id`` / ``swarm_plan_ref`` / ``task_ref`` / ``name`` 非空;
        - refs 元组缺失视为空,元素必须非空字符串;
        - ``completion_condition`` 必须存在且为 dict;
        - ``state`` 经 ``StageState.from_wire``;审计四字段同款。
        """
        if not isinstance(data, dict):
            raise ContextCollabValidationError(
                f"Stage must be an object, got: {data!r}"
            )
        unknown = [key for key in data if key not in _STAGE_FIELDS]
        if unknown:
            raise ContextCollabValidationError(
                f"unknown field(s) in Stage: {sorted(unknown)}"
            )
        return cls(
            stage_id=_required_str(data, "stage_id", "Stage"),
            swarm_plan_ref=_required_str(data, "swarm_plan_ref", "Stage"),
            task_ref=_required_str(data, "task_ref", "Stage"),
            name=_required_str(data, "name", "Stage"),
            state=StageState.from_wire(_required_str(data, "state", "Stage")),
            completion_condition=_required_dict(
                data, "completion_condition", "Stage"
            ),
            dependency_stage_refs=_str_tuple(
                data, "dependency_stage_refs", "Stage"
            ),
            requirement_refs=_str_tuple(data, "requirement_refs", "Stage"),
            input_artifact_refs=_str_tuple(
                data, "input_artifact_refs", "Stage"
            ),
            output_artifact_roles=_str_tuple(
                data, "output_artifact_roles", "Stage"
            ),
            transitioned_from=_audit_str(data, "transitioned_from", "Stage"),
            transition_reason=_audit_str(data, "transition_reason", "Stage"),
            transitioned_by=_audit_str(data, "transitioned_by", "Stage"),
            transitioned_at=_audit_str(data, "transitioned_at", "Stage"),
        )

    def to_dict(self) -> dict:
        """输出全部声明字段;枚举取 wire 值、元组降为列表,保证
        ``from_dict(to_dict(x)) == x``。"""
        return {
            "stage_id": self.stage_id,
            "swarm_plan_ref": self.swarm_plan_ref,
            "task_ref": self.task_ref,
            "name": self.name,
            "dependency_stage_refs": list(self.dependency_stage_refs),
            "requirement_refs": list(self.requirement_refs),
            "input_artifact_refs": list(self.input_artifact_refs),
            "output_artifact_roles": list(self.output_artifact_roles),
            "completion_condition": self.completion_condition,
            "state": self.state.value,
            "transitioned_from": self.transitioned_from,
            "transition_reason": self.transition_reason,
            "transitioned_by": self.transitioned_by,
            "transitioned_at": self.transitioned_at,
        }


@dataclass(frozen=True)
class StageAllocation:
    """Stage 分配值对象(spec §4.3 YAML + §8.1 lease + §6.3 审计四字段)。

    ``target_kind``(AGENT/COMPUTE)与 ``target_ref`` 定目标;
    ``execution_profile_ref`` 必须指向 binding 作用域内已声明的
    ``SwarmExecutionProfile``(存在性在服务层裁决);``lease_ref`` 是租约
    引用,``lease_expires_at`` 是租约到期 ISO 时间串(§8.1:allocation
    对象存租约到期,过期即 Active-Allocation 断言失效);``attempt``
    是该 Stage 的第几次分配尝试(正整数)。
    """

    allocation_id: str
    stage_ref: str
    target_kind: AllocationTargetKind
    target_ref: str
    execution_profile_ref: str
    lease_ref: str
    lease_expires_at: str
    state: AllocationState
    attempt: int
    qualification_evidence_refs: tuple[str, ...] = ()
    authorization_evidence_refs: tuple[str, ...] = ()
    transitioned_from: str = ""
    transition_reason: str = ""
    transitioned_by: str = ""
    transitioned_at: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "StageAllocation":
        """从 dict 恢复;字段集封闭(未知字段拒绝),校验规则:

        - ``allocation_id`` / ``stage_ref`` / ``target_ref`` /
          ``execution_profile_ref`` / ``lease_ref`` / ``lease_expires_at``
          非空;
        - ``target_kind`` 经 ``AllocationTargetKind.from_wire``;
        - ``attempt`` 正整数;evidence refs 缺失视为空、条目非空串;
        - ``state`` 经 ``AllocationState.from_wire``;审计四字段同款。

        ``execution_profile_ref`` 的非空强制在对象口径上是"参与执行
        的分配必须挂 profile";P1 无外部派发,空串只可能出现在 P2
        派发前占位,此处按值对象纪律先行拒绝。
        """
        if not isinstance(data, dict):
            raise ContextCollabValidationError(
                f"StageAllocation must be an object, got: {data!r}"
            )
        unknown = [key for key in data if key not in _ALLOCATION_FIELDS]
        if unknown:
            raise ContextCollabValidationError(
                f"unknown field(s) in StageAllocation: {sorted(unknown)}"
            )
        return cls(
            allocation_id=_required_str(data, "allocation_id", "StageAllocation"),
            stage_ref=_required_str(data, "stage_ref", "StageAllocation"),
            target_kind=AllocationTargetKind.from_wire(
                _required_str(data, "target_kind", "StageAllocation")
            ),
            target_ref=_required_str(data, "target_ref", "StageAllocation"),
            execution_profile_ref=_required_str(
                data, "execution_profile_ref", "StageAllocation"
            ),
            lease_ref=_required_str(data, "lease_ref", "StageAllocation"),
            lease_expires_at=_required_str(
                data, "lease_expires_at", "StageAllocation"
            ),
            state=AllocationState.from_wire(
                _required_str(data, "state", "StageAllocation")
            ),
            attempt=_positive_int(data, "attempt", "StageAllocation"),
            qualification_evidence_refs=_str_tuple(
                data, "qualification_evidence_refs", "StageAllocation"
            ),
            authorization_evidence_refs=_str_tuple(
                data, "authorization_evidence_refs", "StageAllocation"
            ),
            transitioned_from=_audit_str(
                data, "transitioned_from", "StageAllocation"
            ),
            transition_reason=_audit_str(
                data, "transition_reason", "StageAllocation"
            ),
            transitioned_by=_audit_str(
                data, "transitioned_by", "StageAllocation"
            ),
            transitioned_at=_audit_str(
                data, "transitioned_at", "StageAllocation"
            ),
        )

    def to_dict(self) -> dict:
        """输出全部声明字段;枚举取 wire 值、元组降为列表,保证
        ``from_dict(to_dict(x)) == x``。"""
        return {
            "allocation_id": self.allocation_id,
            "stage_ref": self.stage_ref,
            "target_kind": self.target_kind.value,
            "target_ref": self.target_ref,
            "execution_profile_ref": self.execution_profile_ref,
            "lease_ref": self.lease_ref,
            "lease_expires_at": self.lease_expires_at,
            "qualification_evidence_refs": list(
                self.qualification_evidence_refs
            ),
            "authorization_evidence_refs": list(
                self.authorization_evidence_refs
            ),
            "attempt": self.attempt,
            "state": self.state.value,
            "transitioned_from": self.transitioned_from,
            "transition_reason": self.transition_reason,
            "transitioned_by": self.transitioned_by,
            "transitioned_at": self.transitioned_at,
        }


@dataclass(frozen=True)
class SwarmExecutionProfile:
    """Swarm 执行剖面值对象(spec §4.3 YAML;无状态、无审计四字段——
    P1 经 DeclareExecutionProfile 声明即活,不承状态迁移)。

    ``mode`` 是 A2A_AGENT / NATIVE_COMPUTE 派发模式(P1 只落语义);
    ``supported_work_kinds`` 必须是 SWARM_WORK_KINDS 非空子集;
    ``input_contract_ref`` 非空、``output_contract_ref`` 允许空串;
    ``lifecycle_mapping`` 缺失视为空 dict。
    """

    execution_profile_id: str
    mode: ExecutionMode
    target_ref: str
    input_contract_ref: str
    supported_work_kinds: tuple[str, ...] = ()
    output_contract_ref: str = ""
    lifecycle_mapping: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "SwarmExecutionProfile":
        """从 dict 恢复;字段集封闭(未知字段拒绝),校验规则:

        - ``execution_profile_id`` / ``target_ref`` /
          ``input_contract_ref`` 非空;
        - ``mode`` 经 ``ExecutionMode.from_wire``;
        - ``supported_work_kinds`` 非空且逐项在 SWARM_WORK_KINDS 内;
        - ``output_contract_ref`` 必须是字符串,允许空串;
        - ``lifecycle_mapping`` 缺失视为空 dict。
        """
        if not isinstance(data, dict):
            raise ContextCollabValidationError(
                f"SwarmExecutionProfile must be an object, got: {data!r}"
            )
        unknown = [
            key for key in data if key not in _EXECUTION_PROFILE_FIELDS
        ]
        if unknown:
            raise ContextCollabValidationError(
                f"unknown field(s) in SwarmExecutionProfile: {sorted(unknown)}"
            )
        kinds = _str_tuple(
            data, "supported_work_kinds", "SwarmExecutionProfile"
        )
        if not kinds:
            raise ContextCollabValidationError(
                "SwarmExecutionProfile.supported_work_kinds must be a "
                "non-empty subset of "
                f"{sorted(SWARM_WORK_KINDS)}, got: {data.get('supported_work_kinds')!r}"
            )
        for kind in kinds:
            if kind not in SWARM_WORK_KINDS:
                raise ContextCollabValidationError(
                    f"unknown swarm work kind: {kind!r}; expected one of "
                    f"{sorted(SWARM_WORK_KINDS)}"
                )
        return cls(
            execution_profile_id=_required_str(
                data, "execution_profile_id", "SwarmExecutionProfile"
            ),
            mode=ExecutionMode.from_wire(
                _required_str(data, "mode", "SwarmExecutionProfile")
            ),
            target_ref=_required_str(
                data, "target_ref", "SwarmExecutionProfile"
            ),
            supported_work_kinds=kinds,
            input_contract_ref=_required_str(
                data, "input_contract_ref", "SwarmExecutionProfile"
            ),
            output_contract_ref=_audit_str(
                data, "output_contract_ref", "SwarmExecutionProfile"
            ),
            lifecycle_mapping=_optional_dict(
                data, "lifecycle_mapping", "SwarmExecutionProfile"
            ),
        )

    def to_dict(self) -> dict:
        """输出全部声明字段;枚举取 wire 值、元组降为列表,保证
        ``from_dict(to_dict(x)) == x``。"""
        return {
            "execution_profile_id": self.execution_profile_id,
            "mode": self.mode.value,
            "target_ref": self.target_ref,
            "supported_work_kinds": list(self.supported_work_kinds),
            "input_contract_ref": self.input_contract_ref,
            "output_contract_ref": self.output_contract_ref,
            "lifecycle_mapping": self.lifecycle_mapping,
        }


@dataclass(frozen=True)
class SwarmExecutionRecord:
    """Swarm 执行记录值对象(spec §4.3 YAML + §6.3 审计四字段;P1 双平面
    边界:仅由权威面操作创建/查询,记录 Computation Plane 的派发事实)。

    ``operation_ref`` 指向来源权威面操作;``stage_allocation_ref`` 可空
    (无关联分配的计划级 provenance);``execution_profile_ref`` 允许空串
    ——计划候选 provenance 行(P1)尚无关联执行 profile,P2 接入外部计算
    系统后强制非空;``external_execution_id`` 在 P1 恒为来源操作的审计
    标识(spec §4.3 Computation Plane 裁决)。
    """

    execution_record_id: str
    operation_ref: str
    external_execution_id: str
    state: ExecutionRecordState
    stage_allocation_ref: str | None = None
    execution_profile_ref: str = ""
    transitioned_from: str = ""
    transition_reason: str = ""
    transitioned_by: str = ""
    transitioned_at: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "SwarmExecutionRecord":
        """从 dict 恢复;字段集封闭(未知字段拒绝),校验规则:

        - ``execution_record_id`` / ``operation_ref`` /
          ``external_execution_id`` 非空;
        - ``stage_allocation_ref`` 可空(none 或非空串);
        - ``execution_profile_ref`` 必须是字符串,允许空串(见类
          docstring 的 P1 裁决);
        - ``state`` 经 ``ExecutionRecordState.from_wire``;审计四字段
          同款。
        """
        if not isinstance(data, dict):
            raise ContextCollabValidationError(
                f"SwarmExecutionRecord must be an object, got: {data!r}"
            )
        unknown = [
            key for key in data if key not in _EXECUTION_RECORD_FIELDS
        ]
        if unknown:
            raise ContextCollabValidationError(
                f"unknown field(s) in SwarmExecutionRecord: {sorted(unknown)}"
            )
        return cls(
            execution_record_id=_required_str(
                data, "execution_record_id", "SwarmExecutionRecord"
            ),
            operation_ref=_required_str(
                data, "operation_ref", "SwarmExecutionRecord"
            ),
            external_execution_id=_required_str(
                data, "external_execution_id", "SwarmExecutionRecord"
            ),
            state=ExecutionRecordState.from_wire(
                _required_str(data, "state", "SwarmExecutionRecord")
            ),
            stage_allocation_ref=_optional_str(
                data, "stage_allocation_ref", "SwarmExecutionRecord"
            ),
            execution_profile_ref=_audit_str(
                data, "execution_profile_ref", "SwarmExecutionRecord"
            ),
            transitioned_from=_audit_str(
                data, "transitioned_from", "SwarmExecutionRecord"
            ),
            transition_reason=_audit_str(
                data, "transition_reason", "SwarmExecutionRecord"
            ),
            transitioned_by=_audit_str(
                data, "transitioned_by", "SwarmExecutionRecord"
            ),
            transitioned_at=_audit_str(
                data, "transitioned_at", "SwarmExecutionRecord"
            ),
        )

    def to_dict(self) -> dict:
        """输出全部声明字段;枚举取 wire 值,保证
        ``from_dict(to_dict(x)) == x``。"""
        return {
            "execution_record_id": self.execution_record_id,
            "operation_ref": self.operation_ref,
            "stage_allocation_ref": self.stage_allocation_ref,
            "execution_profile_ref": self.execution_profile_ref,
            "external_execution_id": self.external_execution_id,
            "state": self.state.value,
            "transitioned_from": self.transitioned_from,
            "transition_reason": self.transition_reason,
            "transitioned_by": self.transitioned_by,
            "transitioned_at": self.transitioned_at,
        }


# ---------------------------------------------------------------------------
# 计划结构校验单点(spec §4.3;ValidatePlan 报告与 AdoptPlan 守卫共用,
# 沿 ``method_composition.validate_composition`` 的"值对象 + 结构裁决
# 同模块"先例)
# ---------------------------------------------------------------------------


def layer_metrics(stages: list["Stage"]) -> tuple[int, int] | None:
    """按最长路径给 Stage 分层,返回 (深度, 宽度);图有环返回 None。

    深度 = 最长依赖链的节点数(单 Stage 计 1);宽度 = 同层 Stage 数的
    最大值——同层 Stage 互不依赖、结构上可并行,层大小即并行宽度;
    Stage 数上限由"层数 × 每层 ≤ 并行上限"蕴含,不单列断言。
    引用计划外 Stage 的 dependency_stage_refs 不参与分层(引用存在性
    违例已由 ``structural_violations`` 单独报告)。
    """
    by_id = {stage.stage_id: stage for stage in stages}
    depth: dict[str, int] = {}
    remaining = set(by_id)
    while remaining:
        progressed = False
        for stage_id in sorted(remaining):
            deps = [
                ref
                for ref in by_id[stage_id].dependency_stage_refs
                if ref in by_id
            ]
            if all(ref in depth for ref in deps):
                depth[stage_id] = 1 + max(
                    (depth[ref] for ref in deps), default=0
                )
                remaining.discard(stage_id)
                progressed = True
        if not progressed:
            return None
    if not depth:
        return (0, 0)
    layer_sizes: dict[int, int] = {}
    for value in depth.values():
        layer_sizes[value] = layer_sizes.get(value, 0) + 1
    return max(depth.values()), max(layer_sizes.values())


def structural_violations(plan: "SwarmPlan", stages: list["Stage"]) -> list[str]:
    """计划结构校验单点,返回违例文案列表(空列表即通过):

    - Stage 完成条件结构:每个 Stage 必须携带非空 completion_condition
      (作者裁决:空 dict 不构成完成断言,"结构存在"即非空);
    - 依赖引用存在性:dependency_stage_refs 必须解析到计划内 Stage;
    - 依赖图无环(``detect_cycle`` 复用,T3 同款纪律);
    - 深度/宽度上限:分别与 plan.max_depth / max_parallelism 比对
      (图有环或缺引用时跳过——前序违例已足以否决)。
    """
    violations: list[str] = []
    stage_ids = {stage.stage_id for stage in stages}
    has_undefined_ref = False
    for stage in stages:
        if not stage.completion_condition:
            violations.append(
                f"stage {stage.stage_id!r} must carry a non-empty "
                "completion_condition"
            )
        for ref in stage.dependency_stage_refs:
            if ref not in stage_ids:
                violations.append(
                    f"stage {stage.stage_id!r} references undefined "
                    f"dependency {ref!r}"
                )
                has_undefined_ref = True
    edges = [
        (ref, stage.stage_id)
        for stage in stages
        for ref in stage.dependency_stage_refs
    ]
    has_cycle = detect_cycle(edges)
    if has_cycle:
        violations.append("stage dependency graph contains a cycle")
    metrics = (
        layer_metrics(stages)
        if not (has_undefined_ref or has_cycle)
        else None
    )
    if metrics is not None:
        depth, width = metrics
        if depth > plan.max_depth:
            violations.append(
                f"dependency depth {depth} exceeds plan max_depth "
                f"{plan.max_depth}"
            )
        if width > plan.max_parallelism:
            violations.append(
                f"parallel stage width {width} exceeds plan "
                f"max_parallelism {plan.max_parallelism}"
            )
    return violations
