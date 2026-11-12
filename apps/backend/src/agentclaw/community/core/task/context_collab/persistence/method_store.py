"""L5 方法存储:``MethodStoreProtocol`` 协议与 ``StoreAxes`` 存储轴。

``InMemoryMethodStore`` 进程内实现按切片拆在 ``method_store_impl/``
包(锁与仓库字典在 ``base.py``,binding/edge、single selection、
group roster/member 各自成片)——1000 行 CI 边界的拆分由协议与实现
分家承载,本模块底部 re-export 保持
``from ...persistence.method_store import InMemoryMethodStore`` 的既有
单一导入路径不变(impl 切片不回环 import 本模块,注解经 TYPE_CHECKING)。

值对象(``MethodBinding`` / ``MethodCompositionEdge`` / ``SingleSelection``
/ ``GroupRoster`` / ``GroupMemberBinding``)不携带租户/环境,存储键自带
``(avernet_tenant, env, collab_context_id, <id>)`` 四段前缀,由调用方
显式传 ``StoreAxes``;``SingleSelection`` 值对象连 collab_context_id
也不携带,selection 各方法的 ``collab_context_id`` 一律显式入参,键位
与 binding/edge 同域锚定上下文。store 不读运行环境,保持纯内存语义。

单把粗粒度 ``threading.Lock`` 覆盖全部写与 list 读——store 操作短小,
先以一锁压住正确性,成为热点再细分。构造无参(DI 单例,Task 8/13 接线)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l5_methods.group_profile import (
    GroupMemberBinding,
    GroupRoster,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    MethodBinding,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_composition import (
    MethodCompositionEdge,
)
from agentclaw.community.core.task.context_collab.l5_methods.open_profile import (
    AdmissionDecision,
    OpenOpportunity,
    ParticipationApplication,
)
from agentclaw.community.core.task.context_collab.l5_methods.single_profile import (
    SingleSelection,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_profile import (
    Stage,
    StageAllocation,
    SwarmExecutionProfile,
    SwarmExecutionRecord,
    SwarmPlan,
)


@dataclass(frozen=True)
class StoreAxes:
    """存储的租户/环境轴;不缺省、不读运行环境,由调用方从自身上下文派生。"""

    avernet_tenant: str
    env: str

    def __post_init__(self) -> None:
        for field_name in ("avernet_tenant", "env"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ContextCollabValidationError(
                    f"StoreAxes.{field_name} must be a non-empty string, "
                    f"got: {value!r}"
                )


@runtime_checkable
class MethodStoreProtocol(Protocol):
    """L5 方法存储协议:binding、composition edge、single selection 与
    group roster/member 切片。

    幂等裁断以 ``to_dict()`` 逐字段相等为准;同键异形态的重复提交抛
    ``ContextCollabValidationError``;``replace_*`` 要求键已存在,不存在
    抛 ``ContextCollabNotFoundError``;DETACHED 边保留审计、不物理删除。
    Single selection 切片以显式 ``collab_context_id`` 入参与 binding/edge
    切片同域隔离,并额外强制同 context 内 (method_binding_id,
    scope_ref) 至多一个 ACTIVE(spec §6.1)。Group 切片以
    ``bump_roster_revision`` 锁内比对承载 roster 乐观并发(spec §4.2,
    revision 不匹配抛 ``ContextCollabVersionConflict``),并对参与者
    强制"非终态不得重复在册"。Swarm 切片(plan/stage/allocation/
    execution profile/execution record)以显式 method_binding_id 入参
    键定域(spec §4.3 YAML 只在 plan 上落 binding 字段);execution
    record 只增不改(Computation Plane 派发 P1 不实现,§2)。
    """

    def upsert_binding(
        self, axes: StoreAxes, binding: MethodBinding
    ) -> MethodBinding: ...

    def get_binding(
        self,
        axes: StoreAxes,
        method_binding_id: str,
        collab_context_id: str,
    ) -> MethodBinding | None: ...

    def replace_binding(self, axes: StoreAxes, binding: MethodBinding) -> None: ...

    def list_bindings(
        self, axes: StoreAxes, collab_context_id: str
    ) -> list[MethodBinding]: ...

    def insert_composition_edge(
        self, axes: StoreAxes, edge: MethodCompositionEdge
    ) -> MethodCompositionEdge: ...

    def replace_composition_edge(
        self, axes: StoreAxes, edge: MethodCompositionEdge
    ) -> None: ...

    def list_composition_edges(
        self,
        axes: StoreAxes,
        collab_context_id: str,
        *,
        active_only: bool = True,
    ) -> list[MethodCompositionEdge]: ...

    def get_composition_edge(
        self,
        axes: StoreAxes,
        edge_id: str,
        collab_context_id: str,
    ) -> MethodCompositionEdge | None: ...

    def insert_selection(
        self,
        axes: StoreAxes,
        selection: SingleSelection,
        collab_context_id: str,
    ) -> SingleSelection: ...

    def get_selection(
        self,
        axes: StoreAxes,
        selection_id: str,
        method_binding_id: str,
        collab_context_id: str,
    ) -> SingleSelection | None: ...

    def list_selections(
        self,
        axes: StoreAxes,
        method_binding_id: str,
        collab_context_id: str,
    ) -> list[SingleSelection]: ...

    def active_selection(
        self,
        axes: StoreAxes,
        method_binding_id: str,
        scope_ref: str,
        collab_context_id: str,
    ) -> SingleSelection | None: ...

    def replace_selection(
        self,
        axes: StoreAxes,
        old: SingleSelection,
        new: SingleSelection,
        collab_context_id: str,
    ) -> None: ...

    def update_selection_state(
        self,
        axes: StoreAxes,
        selection: SingleSelection,
        collab_context_id: str,
    ) -> None: ...

    def insert_roster(
        self,
        axes: StoreAxes,
        roster: GroupRoster,
        collab_context_id: str,
    ) -> GroupRoster: ...

    def get_roster(
        self,
        axes: StoreAxes,
        method_binding_id: str,
        collab_context_id: str,
    ) -> GroupRoster | None: ...

    def bump_roster_revision(
        self,
        axes: StoreAxes,
        method_binding_id: str,
        expected_revision: int,
        collab_context_id: str,
    ) -> GroupRoster: ...

    def insert_member(
        self,
        axes: StoreAxes,
        member: GroupMemberBinding,
        expected_revision: int,
        collab_context_id: str,
    ) -> GroupMemberBinding: ...

    def update_member_state(
        self,
        axes: StoreAxes,
        member: GroupMemberBinding,
        expected_revision: int,
        collab_context_id: str,
    ) -> None: ...

    def replace_member(
        self,
        axes: StoreAxes,
        old: GroupMemberBinding,
        new: GroupMemberBinding,
        expected_revision: int,
        collab_context_id: str,
    ) -> None: ...

    def list_members(
        self,
        axes: StoreAxes,
        method_binding_id: str,
        collab_context_id: str,
    ) -> list[GroupMemberBinding]: ...

    # Swarm 切片(spec §4.3 / Task 11):plan / stage / allocation /
    # execution profile / execution record 五组。Stage / allocation /
    # profile / record 值对象不携带 method_binding_id(YAML 域内引用),
    # 键段一律显式入参;plan 携带、键段取自对象。
    def insert_plan(
        self,
        axes: StoreAxes,
        plan: SwarmPlan,
        collab_context_id: str,
    ) -> SwarmPlan: ...

    def get_plan(
        self,
        axes: StoreAxes,
        swarm_plan_id: str,
        method_binding_id: str,
        collab_context_id: str,
    ) -> SwarmPlan | None: ...

    def list_plans(
        self,
        axes: StoreAxes,
        method_binding_id: str,
        collab_context_id: str,
    ) -> list[SwarmPlan]: ...

    def update_plan_state(
        self,
        axes: StoreAxes,
        plan: SwarmPlan,
        collab_context_id: str,
    ) -> None: ...

    def insert_stage(
        self,
        axes: StoreAxes,
        method_binding_id: str,
        stage: Stage,
        collab_context_id: str,
    ) -> Stage: ...

    def get_stage(
        self,
        axes: StoreAxes,
        stage_id: str,
        method_binding_id: str,
        collab_context_id: str,
    ) -> Stage | None: ...

    def list_stages(
        self,
        axes: StoreAxes,
        method_binding_id: str,
        collab_context_id: str,
    ) -> list[Stage]: ...

    def update_stage_state(
        self,
        axes: StoreAxes,
        method_binding_id: str,
        stage: Stage,
        collab_context_id: str,
    ) -> None: ...

    def insert_allocation(
        self,
        axes: StoreAxes,
        method_binding_id: str,
        allocation: StageAllocation,
        collab_context_id: str,
    ) -> StageAllocation: ...

    def get_allocation(
        self,
        axes: StoreAxes,
        allocation_id: str,
        method_binding_id: str,
        collab_context_id: str,
    ) -> StageAllocation | None: ...

    def list_allocations(
        self,
        axes: StoreAxes,
        method_binding_id: str,
        collab_context_id: str,
    ) -> list[StageAllocation]: ...

    def update_allocation_state(
        self,
        axes: StoreAxes,
        method_binding_id: str,
        allocation: StageAllocation,
        collab_context_id: str,
    ) -> None: ...

    def insert_execution_profile(
        self,
        axes: StoreAxes,
        method_binding_id: str,
        profile: SwarmExecutionProfile,
        collab_context_id: str,
    ) -> SwarmExecutionProfile: ...

    def get_execution_profile(
        self,
        axes: StoreAxes,
        execution_profile_id: str,
        method_binding_id: str,
        collab_context_id: str,
    ) -> SwarmExecutionProfile | None: ...

    def list_execution_profiles(
        self,
        axes: StoreAxes,
        method_binding_id: str,
        collab_context_id: str,
    ) -> list[SwarmExecutionProfile]: ...

    def insert_execution_record(
        self,
        axes: StoreAxes,
        method_binding_id: str,
        record: SwarmExecutionRecord,
        collab_context_id: str,
    ) -> SwarmExecutionRecord: ...

    def get_execution_record(
        self,
        axes: StoreAxes,
        execution_record_id: str,
        method_binding_id: str,
        collab_context_id: str,
    ) -> SwarmExecutionRecord | None: ...

    def list_execution_records(
        self,
        axes: StoreAxes,
        method_binding_id: str,
        collab_context_id: str,
    ) -> list[SwarmExecutionRecord]: ...

    # Open 切片(spec §4.4 / Task 12):opportunity / application /
    # decision 三组。opportunity 值对象携带 method_binding_id;application
    # 与 decision 只携带域内引用,键段一律显式入参。store 层强制同
    # (opportunity_ref, applicant_agent_ref) 至多一条非终态 application。
    def insert_opportunity(
        self,
        axes: StoreAxes,
        opportunity: OpenOpportunity,
        collab_context_id: str,
    ) -> OpenOpportunity: ...

    def get_opportunity(
        self,
        axes: StoreAxes,
        opportunity_id: str,
        method_binding_id: str,
        collab_context_id: str,
    ) -> OpenOpportunity | None: ...

    def list_opportunities(
        self,
        axes: StoreAxes,
        method_binding_id: str,
        collab_context_id: str,
    ) -> list[OpenOpportunity]: ...

    def update_opportunity_state(
        self,
        axes: StoreAxes,
        opportunity: OpenOpportunity,
        collab_context_id: str,
    ) -> None: ...

    def insert_application(
        self,
        axes: StoreAxes,
        method_binding_id: str,
        application: ParticipationApplication,
        collab_context_id: str,
    ) -> ParticipationApplication: ...

    def get_application(
        self,
        axes: StoreAxes,
        application_id: str,
        method_binding_id: str,
        collab_context_id: str,
    ) -> ParticipationApplication | None: ...

    def list_applications(
        self,
        axes: StoreAxes,
        method_binding_id: str,
        collab_context_id: str,
    ) -> list[ParticipationApplication]: ...

    def update_application_state(
        self,
        axes: StoreAxes,
        method_binding_id: str,
        application: ParticipationApplication,
        collab_context_id: str,
    ) -> None: ...

    def insert_decision(
        self,
        axes: StoreAxes,
        method_binding_id: str,
        decision: AdmissionDecision,
        collab_context_id: str,
    ) -> AdmissionDecision: ...

    def get_decision(
        self,
        axes: StoreAxes,
        decision_id: str,
        method_binding_id: str,
        collab_context_id: str,
    ) -> AdmissionDecision | None: ...

    def list_decisions(
        self,
        axes: StoreAxes,
        method_binding_id: str,
        collab_context_id: str,
    ) -> list[AdmissionDecision]: ...


# Re-export(协议在上的拆分):impl 切片位于 method_store_impl/ 包,
# 本行保证既有调用方 ``from ...persistence.method_store import
# InMemoryMethodStore`` 的单一导入路径不变;impl 不回环 import 本模块。
from agentclaw.community.core.task.context_collab.persistence.method_store_impl import (  # noqa: E402
    InMemoryMethodStore,
)

__all__ = ["InMemoryMethodStore", "MethodStoreProtocol", "StoreAxes"]
