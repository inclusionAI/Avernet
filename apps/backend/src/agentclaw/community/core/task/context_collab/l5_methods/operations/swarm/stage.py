"""L5 Swarm 服务 Stage 分配面操作切片(spec §4.3 / §8.1 lease):
``allocate_stage`` / ``complete_stage`` / ``fail_stage``。

状态转换一律经 G4 声明式机器(swarm_stage / swarm_allocation 两键,
``resolve_transition`` 同构)裁决。服务编排裁决(spec §4.3):

- ``AllocateStage`` 一跳完成三段机器裁决——Stage
  READY─swarm.stage.allocate→ ALLOCATING、分配 PROPOSED─
  swarm.allocation.activate→ ACTIVE、Stage ALLOCATING─swarm.stage.run→
  RUNNING(分配即活即跑,P1 无外部派发,"run" 就是分配完成的权威
  回写);两跳裁决后落终态一次写,失败抛错于任何写之前,中间态
  ALLOCATING 不落库。
- 结构断言:计划 RUNNING(扩展/分配统一门槛——TerminatePlan 终态后
  拒绝一切计划扩展的承载点)、同 Stage 并行 ACTIVE 分配 <
  ``max_parallelism``、``lease_expires_at`` 可解析且 > now(§8.1)、
  ``execution_profile_ref`` 必须指向 binding 作用域内已声明的
  ``SwarmExecutionProfile``;``attempt`` 取该 Stage 既有分配数 + 1;
  ``lease_ref`` 由服务端按分配 id 派生(命令面无此字段)。
- ``CompleteStage``:Stage 携带非空 completion_condition(作者裁决:
  空 dict 不构成完成断言)、目标分配必须 ACTIVE 且租约未过期(过期须
  先释放再重分配,§8.1 结构断言);``allocation_refs`` 缺省取该
  Stage 全部 ACTIVE 分配。
- ``FailStage``:分配行不动(审计保留)——终止 Stage 后的重分配/释放
  裁决由后续权威面操作显式驱动,P1 不做隐式级联。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.method_state_machines import (
    METHOD_MACHINE_REGISTRY,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.state_machine import (
    resolve_transition,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    ProfileOpAcceptedResult,
    SwarmAllocateCommand,
    SwarmStageOutcomeCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    _shape_of,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.swarm.base import (
    SwarmMethodServiceBase,
    _now,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_profile import (
    AllocationState,
    PlanState,
    Stage,
    StageAllocation,
    StageState,
    SwarmPlan,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    StoreAxes,
)

_STAGE_MACHINE = METHOD_MACHINE_REGISTRY["swarm_stage"]
_ALLOCATION_MACHINE = METHOD_MACHINE_REGISTRY["swarm_allocation"]

# 幂等重放(§8.3)的"请求形态"白名单:排除服务端生成的时间/审计/
# 状态机事实(``transitioned_*``)与 ``attempt``(既有分配数 + 1 的派生
# 计数,首次落库后随 store 状态漂移——沿 bind 重放排除 version 先例);
# ``lease_ref`` 由服务端按分配 id 派生,同 id 重放恒等,保留在比对面。
_ALLOCATION_REQUEST_FIELDS = (
    "stage_ref",
    "target_kind",
    "target_ref",
    "execution_profile_ref",
    "lease_ref",
    "lease_expires_at",
    "qualification_evidence_refs",
    "authorization_evidence_refs",
)


def _resolve_stage_state(from_state: str, event: str) -> str:
    """经 G4 机器裁决 swarm_stage 迁移;无出边即校验失败。"""
    new_state = resolve_transition(_STAGE_MACHINE, from_state, event, {})
    if new_state is None:
        raise ContextCollabValidationError(
            f"swarm stage state {from_state!r} does not accept event {event!r}"
        )
    return new_state


def _resolve_allocation_state(from_state: str, event: str) -> str:
    """经 G4 机器裁决 swarm_allocation 迁移;无出边即校验失败。"""
    new_state = resolve_transition(_ALLOCATION_MACHINE, from_state, event, {})
    if new_state is None:
        raise ContextCollabValidationError(
            f"swarm allocation state {from_state!r} does not accept event {event!r}"
        )
    return new_state


def _parse_timestamp(value: str, owner: str, field: str) -> datetime:
    """ISO 时间串解析;坏格式抛校验错误;naive 串按 UTC 对待。"""
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise ContextCollabValidationError(
            f"{owner}.{field} must be an ISO timestamp string, got: {value!r}"
        ) from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class SwarmStageOpsMixin(SwarmMethodServiceBase):
    """Stage 分配面三操作(分配/完成/失败)。"""

    def _require_stage(
        self,
        axes: StoreAxes,
        collab_context_id: str,
        method_binding_id: str,
        stage_ref: str,
    ) -> Stage:
        stage = self._store.get_stage(
            axes, stage_ref, method_binding_id, collab_context_id
        )
        if stage is None:
            raise ContextCollabNotFoundError(
                f"swarm stage {stage_ref!r} not found on method binding "
                f"{method_binding_id!r} in collaboration context "
                f"{collab_context_id!r}"
            )
        return stage

    def _stage_allocations(
        self,
        axes: StoreAxes,
        collab_context_id: str,
        method_binding_id: str,
        stage_ref: str,
    ) -> list[StageAllocation]:
        return [
            allocation
            for allocation in self._store.list_allocations(
                axes, method_binding_id, collab_context_id
            )
            if allocation.stage_ref == stage_ref
        ]

    def _require_running_plan(self, plan: SwarmPlan, op: str) -> None:
        """扩展/分配类 Stage 操作的统一计划门槛:计划必须 RUNNING——
        READY(未起飞)、PAUSED、终态(CANCELLED 等)一律拒绝
        (TerminatePlan 终态后拒绝一切计划扩展的承载点)。"""
        if plan.state is not PlanState.RUNNING:
            raise ContextCollabValidationError(
                f"method op {op!r} requires swarm plan "
                f"{plan.swarm_plan_id!r} to be RUNNING, got "
                f"{plan.state.value!r}"
            )

    def allocate_stage(
        self,
        collab_context_id: str,
        method_binding_id: str,
        command: SwarmAllocateCommand,
        context: ActorContext,
    ) -> ProfileOpAcceptedResult:
        """AllocateStage 用例:结构断言 + 三段机器裁决一次落定
        (用例编排见模块 docstring);幂等键 ``allocation_id``
        (§8.3 同款:同 ID 同请求形态返回既有已受理对象,异形态 409
        语义)。"""
        axes = self._gate(
            "allocate_stage", collab_context_id, method_binding_id, context
        )
        stage = self._require_stage(
            axes, collab_context_id, method_binding_id, command.stage_ref
        )
        plan = self._require_plan(
            axes, collab_context_id, method_binding_id, stage.swarm_plan_ref
        )
        self._require_running_plan(plan, "allocate_stage")
        stage_allocations = self._stage_allocations(
            axes, collab_context_id, method_binding_id, stage.stage_id
        )
        active_on_stage = [
            allocation
            for allocation in stage_allocations
            if allocation.state is AllocationState.ACTIVE
        ]
        if len(active_on_stage) >= plan.max_parallelism:
            raise ContextCollabValidationError(
                f"stage {stage.stage_id!r} already has "
                f"{len(active_on_stage)} ACTIVE allocations; plan "
                f"{plan.swarm_plan_id!r} allows at most "
                f"{plan.max_parallelism} parallel ACTIVE allocations per "
                "stage"
            )
        lease_expires_at = _parse_timestamp(
            command.lease_expires_at,
            "SwarmAllocateCommand",
            "lease_expires_at",
        )
        if lease_expires_at <= datetime.now(timezone.utc):
            raise ContextCollabValidationError(
                f"swarm allocation lease expires at "
                f"{command.lease_expires_at!r}, which is not in the "
                "future; a fresh lease is required before allocating the "
                "stage"
            )
        if (
            self._store.get_execution_profile(
                axes,
                command.execution_profile_ref,
                method_binding_id,
                collab_context_id,
            )
            is None
        ):
            raise ContextCollabValidationError(
                f"swarm stage {stage.stage_id!r} allocation references "
                f"execution profile {command.execution_profile_ref!r} which "
                f"is not declared on method binding {method_binding_id!r}"
            )
        allocation_id = command.allocation_id or f"alloc_{uuid4().hex[:12]}"
        now = _now()
        reason = command.reason or "swarm.stage.allocate"
        candidate = StageAllocation.from_dict(
            {
                "allocation_id": allocation_id,
                "stage_ref": stage.stage_id,
                "target_kind": command.target_kind,
                "target_ref": command.target_ref,
                "execution_profile_ref": command.execution_profile_ref,
                "lease_ref": f"lease_{allocation_id}",
                "lease_expires_at": command.lease_expires_at,
                "qualification_evidence_refs": list(
                    command.qualification_evidence_refs
                ),
                # 授权证据 P1 不从命令面接收,证据断言是 P2 接入外部
                # 派发时的输入;此处按值对象纪律落空集。
                "authorization_evidence_refs": [],
                "attempt": len(stage_allocations) + 1,
                "state": AllocationState.ACTIVE.value,
                "transition_reason": reason,
                "transitioned_by": context.actor.principal_id,
                "transitioned_at": now,
                # PROPOSED──activate──ACTIVE:经机器裁决,创建行不经
                # PROPOSED 落库(沿 invite 建 INVITED 行先例)。
                "transitioned_from": AllocationState.PROPOSED.value,
            }
        )
        if command.allocation_id is not None:
            existing = self._store.get_allocation(
                axes,
                command.allocation_id,
                method_binding_id,
                collab_context_id,
            )
            if existing is not None:
                if _shape_of(
                    existing.to_dict(), _ALLOCATION_REQUEST_FIELDS
                ) == _shape_of(candidate.to_dict(), _ALLOCATION_REQUEST_FIELDS):
                    return ProfileOpAcceptedResult(
                        accepted=True,
                        collab_context_id=collab_context_id,
                        method_binding_id=method_binding_id,
                        object_kind="swarm_allocation",
                        object_id=existing.allocation_id,
                        state=existing.state.value,
                    )
                raise ContextCollabValidationError(
                    f"idempotent re-submit of swarm allocation "
                    f"{command.allocation_id!r} differs from the stored "
                    "shape"
                )
        # 两跳机器裁决(READY→ALLOCATING→RUNNING),落终态一次写:
        # 分配失败抛错于任何写之前,中间态 ALLOCATING 不落库。
        allocating_state = _resolve_stage_state(
            stage.state.value, "swarm.stage.allocate"
        )
        running_state = _resolve_stage_state(allocating_state, "swarm.stage.run")
        updated_stage = replace(
            stage,
            state=StageState.from_wire(running_state),
            transitioned_from=stage.state.value,
            transition_reason=reason,
            transitioned_by=context.actor.principal_id,
            transitioned_at=now,
        )
        self._store.update_stage_state(
            axes, method_binding_id, updated_stage, collab_context_id
        )
        self._store.insert_allocation(
            axes, method_binding_id, candidate, collab_context_id
        )
        return ProfileOpAcceptedResult(
            accepted=True,
            collab_context_id=collab_context_id,
            method_binding_id=method_binding_id,
            object_kind="swarm_allocation",
            object_id=allocation_id,
            state=AllocationState.ACTIVE.value,
        )

    def complete_stage(
        self,
        collab_context_id: str,
        method_binding_id: str,
        stage_ref: str,
        command: SwarmStageOutcomeCommand,
        context: ActorContext,
    ) -> ProfileOpAcceptedResult:
        """CompleteStage 用例:Stage RUNNING─swarm.stage.complete→
        SUCCEEDED,指定的 ACTIVE 分配查验租约后逐一 RELEASED。

        结构断言:Stage 完成条件结构存在(非空 completion_condition,
        作者裁决)、目标分配必须 ACTIVE 且租约未过期(§8.1:过期须先
        释放再重分配);``allocation_refs`` 缺省取该 Stage 全部 ACTIVE
        分配。
        """
        axes = self._gate(
            "complete_stage", collab_context_id, method_binding_id, context
        )
        stage = self._require_stage(
            axes, collab_context_id, method_binding_id, stage_ref
        )
        if not stage.completion_condition:
            raise ContextCollabValidationError(
                f"swarm stage {stage_ref!r} carries an empty "
                "completion_condition; a completion assertion structure "
                "is required before the stage can complete"
            )
        stage_allocations = self._stage_allocations(
            axes, collab_context_id, method_binding_id, stage.stage_id
        )
        by_id = {
            allocation.allocation_id: allocation for allocation in stage_allocations
        }
        target_ids = (
            list(command.allocation_refs)
            if command.allocation_refs
            else [
                allocation.allocation_id
                for allocation in stage_allocations
                if allocation.state is AllocationState.ACTIVE
            ]
        )
        now_dt = datetime.now(timezone.utc)
        for allocation_id in target_ids:
            allocation = by_id.get(allocation_id)
            if allocation is None:
                raise ContextCollabNotFoundError(
                    f"swarm allocation {allocation_id!r} not found for "
                    f"stage {stage_ref!r} in collaboration context "
                    f"{collab_context_id!r}"
                )
            if allocation.state is not AllocationState.ACTIVE:
                raise ContextCollabValidationError(
                    f"swarm allocation {allocation_id!r} is "
                    f"{allocation.state.value!r}; only ACTIVE allocations "
                    "can be released by complete_stage"
                )
            lease = _parse_timestamp(
                allocation.lease_expires_at,
                "StageAllocation",
                "lease_expires_at",
            )
            if lease <= now_dt:
                raise ContextCollabValidationError(
                    f"swarm allocation {allocation_id!r} lease expired at "
                    f"{allocation.lease_expires_at!r}; release and "
                    "re-allocate before completing the stage"
                )
        new_state = _resolve_stage_state(stage.state.value, "swarm.stage.complete")
        now = _now()
        reason = command.reason or "swarm.stage.complete"
        for allocation_id in target_ids:
            allocation = by_id[allocation_id]
            released = replace(
                allocation,
                state=AllocationState.from_wire(
                    _resolve_allocation_state(
                        allocation.state.value, "swarm.allocation.release"
                    )
                ),
                transitioned_from=allocation.state.value,
                transition_reason=reason,
                transitioned_by=context.actor.principal_id,
                transitioned_at=now,
            )
            self._store.update_allocation_state(
                axes, method_binding_id, released, collab_context_id
            )
        updated = replace(
            stage,
            state=StageState.from_wire(new_state),
            transitioned_from=stage.state.value,
            transition_reason=reason,
            transitioned_by=context.actor.principal_id,
            transitioned_at=now,
        )
        self._store.update_stage_state(
            axes, method_binding_id, updated, collab_context_id
        )
        return ProfileOpAcceptedResult(
            accepted=True,
            collab_context_id=collab_context_id,
            method_binding_id=method_binding_id,
            object_kind="swarm_stage",
            object_id=stage.stage_id,
            state=new_state,
        )

    def fail_stage(
        self,
        collab_context_id: str,
        method_binding_id: str,
        stage_ref: str,
        command: SwarmStageOutcomeCommand,
        context: ActorContext,
    ) -> ProfileOpAcceptedResult:
        """FailStage 用例:Stage(RUNNING 或 ALLOCATING,机器双规则)
        ──swarm.stage.fail→ FAILED;分配行不动(模块 docstring)。"""
        axes = self._gate("fail_stage", collab_context_id, method_binding_id, context)
        stage = self._require_stage(
            axes, collab_context_id, method_binding_id, stage_ref
        )
        new_state = _resolve_stage_state(stage.state.value, "swarm.stage.fail")
        now = _now()
        updated = replace(
            stage,
            state=StageState.from_wire(new_state),
            transitioned_from=stage.state.value,
            transition_reason=command.reason or "swarm.stage.fail",
            transitioned_by=context.actor.principal_id,
            transitioned_at=now,
        )
        self._store.update_stage_state(
            axes, method_binding_id, updated, collab_context_id
        )
        return ProfileOpAcceptedResult(
            accepted=True,
            collab_context_id=collab_context_id,
            method_binding_id=method_binding_id,
            object_kind="swarm_stage",
            object_id=stage.stage_id,
            state=new_state,
        )
