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
from agentclaw.community.core.task.context_collab.l5_methods.operations.swarm.workflow_guards import (
    freeze_inputs,
    require_stage,
    normalize_outputs,
    require_done_evidence,
)

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
from agentclaw.community.core.task.context_collab.l5_methods.operations.open.participation import (
    validate_enrolled_participation,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participation import (
    current_participation,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    ProfileOpAcceptedResult,
    SwarmAllocateCommand,
    SwarmStageOutcomeCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    _shape_of,
    current_axes,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.swarm.base import (
    SwarmMethodServiceBase,
    _now,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.swarm.matching import (
    stage_role_candidates,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_profile import (
    AllocationState,
    ALLOCATION_REQUEST_FIELDS,
    ExecutionMode,
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
        workflow = self._workflow_policies.workflow_for(
            collab_context_id, method_binding_id
        )
        if workflow is not None and command.allocation_id is not None:
            require_stage(workflow, collab_context_id, plan, stage)
            existing = self._store.get_allocation(
                axes, command.allocation_id, method_binding_id, collab_context_id
            )
            if existing is not None:
                # Replaying an accepted request is not a new lease or allocation.
                # Keep the current G1 gate, but do not require READY, free capacity
                # or a still-future lease merely to return the original record.
                request = {
                    "stage_ref": command.stage_ref,
                    "target_kind": command.target_kind,
                    "target_ref": command.target_ref,
                    "execution_profile_ref": command.execution_profile_ref,
                    "lease_ref": f"lease_{command.allocation_id}",
                    "lease_expires_at": command.lease_expires_at,
                    "qualification_evidence_refs": command.qualification_evidence_refs,
                    "authorization_evidence_refs": [],
                }
                if _shape_of(
                    existing.to_dict(), ALLOCATION_REQUEST_FIELDS
                ) != _shape_of(request, ALLOCATION_REQUEST_FIELDS):
                    raise ContextCollabValidationError(
                        "idempotent workflow allocation differs from the stored shape"
                    )
                return ProfileOpAcceptedResult(
                    accepted=True,
                    collab_context_id=collab_context_id,
                    method_binding_id=method_binding_id,
                    object_kind="swarm_allocation",
                    object_id=existing.allocation_id,
                    state=existing.state.value,
                )
        self._require_running_plan(plan, "allocate_stage")
        expected_stages = tuple(
            self._plan_stages(
                axes, collab_context_id, method_binding_id, stage.swarm_plan_ref
            )
        )
        if workflow is not None:
            stage = freeze_inputs(
                workflow, collab_context_id, plan, stage, expected_stages
            )
        elif stage.workflow_node is not None:
            raise ContextCollabValidationError("workflow policy unavailable")
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
        execution = self._store.get_execution_profile(
            axes, command.execution_profile_ref, method_binding_id, collab_context_id
        )
        if execution is None:
            raise ContextCollabValidationError(
                f"swarm stage {stage.stage_id!r} allocation references "
                f"execution profile {command.execution_profile_ref!r} which "
                f"is not declared on method binding {method_binding_id!r}"
            )
        if workflow is not None or any(
            "/role-bindings/" in ref for ref in stage.requirement_refs
        ):
            record = self._collab_repo.get_current_status(collab_context_id)
            if record is None:
                raise ContextCollabNotFoundError("stage Collet not found")
            validate_enrolled_participation(
                collab_context_id, record.events, self._store
            )
            candidates = stage_role_candidates(
                collab_context_id, stage, current_participation(record.events)
            )
            if command.target_kind != "AGENT" or command.target_ref not in {
                participant.participant_id for participant in candidates
            }:
                raise ContextCollabValidationError(
                    "allocation target does not satisfy stage role requirements"
                )
            if (
                execution.mode is not ExecutionMode.A2A_AGENT
                or execution.target_ref != command.target_ref
            ):
                raise ContextCollabValidationError(
                    "execution profile does not match stage role target"
                )
            if workflow is not None:
                selected = next(
                    p for p in candidates if p.participant_id == command.target_ref
                )
                if sum(p.agent_ref == selected.agent_ref for p in candidates) != 1:
                    raise ContextCollabValidationError("ambiguous workflow participant")
                node = stage.workflow_node
                if (
                    execution.input_contract_ref != node.input_contract_ref
                    or execution.output_contract_ref != node.output_contract_ref
                ):
                    raise ContextCollabValidationError(
                        "execution profile changed workflow I/O contracts"
                    )
                self._artifact_validator.validate_inputs(stage, selected)
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
                    existing.to_dict(), ALLOCATION_REQUEST_FIELDS
                ) == _shape_of(candidate.to_dict(), ALLOCATION_REQUEST_FIELDS):
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
        if workflow is not None:
            candidate = self._store.commit_workflow_allocation(
                axes,
                method_binding_id,
                collab_context_id,
                plan,
                expected_stages,
                updated_stage,
                candidate,
            )
        else:
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
            object_id=candidate.allocation_id,
            state=candidate.state.value,
        )

    def complete_stage(
        self,
        collab_context_id: str,
        method_binding_id: str,
        stage_ref: str,
        command: SwarmStageOutcomeCommand,
        context: ActorContext,
    ) -> ProfileOpAcceptedResult:
        with self._store.swarm_guard(current_axes(), collab_context_id):
            return self._complete_stage(
                collab_context_id, method_binding_id, stage_ref, command, context
            )

    def _complete_stage(
        self,
        collab_context_id,
        method_binding_id,
        stage_ref,
        command,
        context,
    ):
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
        from agentclaw.community.core.task.context_collab.l5_methods.swarm_execution_state import (
            pending_clarifications,
            pending_interactions,
        )

        record = self._collab_repo.get_current_status(collab_context_id)
        if record is not None and (
            pending_clarifications(record.events, method_binding_id, stage.stage_id)
            or pending_interactions(record.events, method_binding_id)
        ):
            raise ContextCollabValidationError(
                "Stage is waiting for human clarification/interaction resolution"
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
        workflow = self._workflow_policies.workflow_for(
            collab_context_id, method_binding_id
        )
        bindings = ()
        if workflow is not None:
            plan = self._require_plan(
                axes, collab_context_id, method_binding_id, stage.swarm_plan_ref
            )
            require_stage(workflow, collab_context_id, plan, stage)
            if len(target_ids) != 1 or {
                a.allocation_id
                for a in stage_allocations
                if a.state is AllocationState.ACTIVE
            } != set(target_ids):
                raise ContextCollabValidationError(
                    "workflow requires exactly one live allocation"
                )
            bindings = normalize_outputs(
                stage, command.output_bindings, command.artifact_refs
            )
            record = self._collab_repo.get_current_status(collab_context_id)
            if record is None:
                raise ContextCollabValidationError("workflow Collet unavailable")
            validate_enrolled_participation(
                collab_context_id, record.events, self._store
            )
            require_done_evidence(record, plan, stage, by_id[target_ids[0]], bindings)
            self._artifact_validator.validate_outputs(stage, bindings)
        elif stage.workflow_node is not None or command.output_bindings:
            raise ContextCollabValidationError("workflow policy unavailable")
        new_state = _resolve_stage_state(stage.state.value, "swarm.stage.complete")
        now = _now()
        reason = command.reason or "swarm.stage.complete"
        released_allocations = []
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
            released_allocations.append(released)
            if workflow is None:
                self._store.update_allocation_state(
                    axes, method_binding_id, released, collab_context_id
                )
        updated = replace(
            stage,
            output_artifact_refs=tuple(command.artifact_refs),
            output_bindings=bindings,
            state=StageState.from_wire(new_state),
            transitioned_from=stage.state.value,
            transition_reason=reason,
            transitioned_by=context.actor.principal_id,
            transitioned_at=now,
        )
        if workflow is not None:
            self._store.commit_workflow_completion(
                axes,
                method_binding_id,
                collab_context_id,
                stage,
                tuple(stage_allocations),
                updated,
                tuple(released_allocations),
            )
        else:
            self._store.update_stage_state(
                axes, method_binding_id, updated, collab_context_id
            )
        plan = self._require_plan(
            axes, collab_context_id, method_binding_id, stage.swarm_plan_ref
        )
        if plan.state is PlanState.RUNNING:
            self._ready_pending_stages(
                axes,
                collab_context_id,
                method_binding_id,
                stage.swarm_plan_ref,
                context,
                "swarm.stage.dependencies_satisfied",
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
