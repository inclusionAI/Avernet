"""L5 Swarm 服务计划面操作切片(spec §4.3):profile 声明、计划提交、
结构校验报告与七节点计划状态机。

八个用例:``declare_execution_profile`` / ``submit_plan_candidate`` /
``validate_plan`` / ``adopt_plan`` / ``start_plan`` / ``pause_plan`` /
``resume_plan`` / ``terminate_plan``。状态转换一律经 G4 声明式机器
(``METHOD_MACHINE_REGISTRY["swarm_plan"]``,``resolve_transition``
同构)裁决;计划状态机 COMPLETED / FAILED 两态无对应 P1 操作
(swarm.plan.complete / fail 事件留给 P2 执行面回写),词汇按机器保留。
结构校验单点在值对象模块 ``swarm_profile.structural_violations``,
``ValidatePlan`` 出报告不动状态、``AdoptPlan`` 重跑同一份裁决
("未 Validate 通过的拒绝")。

双平面边界(spec §2):Computation Plane 派发 P1 不实现。
``SubmitPlanCandidate`` 自建一行 provenance ``SwarmExecutionRecord``
(operation_ref 指向本次提交、external_execution_id 即来源操作审计
标识、state=SUBMITTED,作者裁决),record 只增不改、永不触碰权威行。

时间一律 UTC ISO(§6.3);审计四字段每次转换覆写于对象。
"""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from agentclaw.community.core.task.context_collab.errors import (
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
    SwarmExecutionProfileCommand,
    SwarmPlanCandidateCommand,
    SwarmPlanOpCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    _shape_of,
    current_axes,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.swarm.base import (
    SwarmMethodServiceBase,
    _now,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_profile import (
    ExecutionRecordState,
    PlanState,
    Stage,
    StageState,
    SwarmExecutionProfile,
    SwarmExecutionRecord,
    SwarmPlan,
    structural_violations,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    StoreAxes,
)

_PLAN_MACHINE = METHOD_MACHINE_REGISTRY["swarm_plan"]
_STAGE_MACHINE = METHOD_MACHINE_REGISTRY["swarm_stage"]

# 幂等重放(§8.3)的"请求形态"白名单(排服务端时间/审计/状态机事实):
# plan 重放比对计划结构 + Stage 集整体形态;profile 同款,排除 mode 之外
# 无服务端生成字段的口径见各白名单注释。
_PLAN_REQUEST_FIELDS = (
    "method_binding_id",
    "task_ref",
    "max_parallelism",
    "max_depth",
    "budget_resource_refs",
    "termination_condition",
)
_STAGE_REQUEST_FIELDS = (
    "swarm_plan_ref",
    "task_ref",
    "name",
    "dependency_stage_refs",
    "requirement_refs",
    "input_artifact_refs",
    "output_artifact_roles",
    "completion_condition",
)
_PROFILE_REQUEST_FIELDS = (
    "method_binding_id",
    "mode",
    "target_ref",
    "supported_work_kinds",
    "input_contract_ref",
    "output_contract_ref",
    "lifecycle_mapping",
)


def _resolve_plan_state(from_state: str, event: str) -> str:
    """经 G4 机器裁决 swarm_plan 迁移;无出边即校验失败。"""
    new_state = resolve_transition(_PLAN_MACHINE, from_state, event, {})
    if new_state is None:
        raise ContextCollabValidationError(
            f"swarm plan state {from_state!r} does not accept event {event!r}"
        )
    return new_state


def _resolve_stage_ready(stage: Stage) -> str:
    """swarm_stage 机器对 PENDING─swarm.stage.ready→READY 的裁决
    (start_plan 的分批 READY 转换专用)。"""
    new_state = resolve_transition(
        _STAGE_MACHINE, stage.state.value, "swarm.stage.ready", {}
    )
    if new_state is None:
        raise ContextCollabValidationError(
            f"swarm stage state {stage.state.value!r} does not accept "
            "event 'swarm.stage.ready'"
        )
    return new_state


class SwarmPlanOpsMixin(SwarmMethodServiceBase):
    """计划面八操作(plan 机器裁决 + 结构校验单点 + provenance 行)。"""

    def _transition_plan(
        self,
        op: str,
        collab_context_id: str,
        method_binding_id: str,
        swarm_plan_id: str,
        axes: StoreAxes,
        context: ActorContext,
        command: SwarmPlanOpCommand,
        event: str,
    ) -> ProfileOpAcceptedResult:
        """adopt/start/pause/resume/terminate 四态机操作共用编排:
        加载 → 机器裁决 → 换新冻结实例(审计四字段)→ 覆写一次。"""
        plan = self._require_plan(
            axes, collab_context_id, method_binding_id, swarm_plan_id
        )
        new_state = _resolve_plan_state(plan.state.value, event)
        now = _now()
        updated = replace(
            plan,
            state=PlanState.from_wire(new_state),
            transitioned_from=plan.state.value,
            transition_reason=command.reason or event,
            transitioned_by=context.actor.principal_id,
            transitioned_at=now,
        )
        self._store.update_plan_state(axes, updated, collab_context_id)
        return ProfileOpAcceptedResult(
            accepted=True,
            collab_context_id=collab_context_id,
            method_binding_id=method_binding_id,
            object_kind="swarm_plan",
            object_id=swarm_plan_id,
            state=new_state,
        )

    def declare_execution_profile(
        self,
        collab_context_id: str,
        method_binding_id: str,
        command: SwarmExecutionProfileCommand,
        context: ActorContext,
    ) -> ProfileOpAcceptedResult:
        """DeclareExecutionProfile 用例:binding 作用域内声明执行剖面,
        对象 P1 即活(spec §4.3 作者裁决,无状态列)。

        幂等键 ``execution_profile_id``(§8.3 同款):同 ID 同请求形态
        返回既有已受理对象,异形态 409 语义。
        """
        axes = self._gate(
            "declare_execution_profile",
            collab_context_id,
            method_binding_id,
            context,
        )
        profile = SwarmExecutionProfile.from_dict(
            {
                "execution_profile_id": (
                    command.execution_profile_id or f"sep_{uuid4().hex[:12]}"
                ),
                "mode": command.mode,
                "target_ref": command.target_ref,
                "supported_work_kinds": list(command.supported_work_kinds),
                "input_contract_ref": command.input_contract_ref,
                "output_contract_ref": command.output_contract_ref,
                "lifecycle_mapping": dict(command.lifecycle_mapping),
            }
        )
        if command.execution_profile_id is not None:
            existing = self._store.get_execution_profile(
                axes,
                command.execution_profile_id,
                method_binding_id,
                collab_context_id,
            )
            if existing is not None:
                if _shape_of(existing.to_dict(), _PROFILE_REQUEST_FIELDS) == _shape_of(
                    profile.to_dict(), _PROFILE_REQUEST_FIELDS
                ):
                    return ProfileOpAcceptedResult(
                        accepted=True,
                        collab_context_id=collab_context_id,
                        method_binding_id=method_binding_id,
                        object_kind="swarm_execution_profile",
                        object_id=existing.execution_profile_id,
                        # 声明即活的 P1 语义:无状态列,受理结果按
                        # "ACTIVE" 投影已声明可用的剖面。
                        state="ACTIVE",
                    )
                raise ContextCollabValidationError(
                    f"idempotent re-submit of execution profile "
                    f"{command.execution_profile_id!r} differs from the "
                    "stored shape"
                )
        stored = self._store.insert_execution_profile(
            axes, method_binding_id, profile, collab_context_id
        )
        return ProfileOpAcceptedResult(
            accepted=True,
            collab_context_id=collab_context_id,
            method_binding_id=method_binding_id,
            object_kind="swarm_execution_profile",
            object_id=stored.execution_profile_id,
            state="ACTIVE",
        )

    def submit_plan_candidate(
        self,
        collab_context_id: str,
        method_binding_id: str,
        command: SwarmPlanCandidateCommand,
        context: ActorContext,
    ) -> ProfileOpAcceptedResult:
        """SubmitPlanCandidate 用例:落 DRAFT 计划 + PENDING Stage 集 +
        一行 provenance 执行记录(spec §4.3 / §2 双平面边界)。

        Stage 行不经状态机迁移(``transitioned_from`` 空串,沿 invite
        建 INVITED 行先例);幂等键 ``swarm_plan_id``(§8.3):同 ID 同
        请求形态(计划结构 + Stage 集整体)返回既有已受理对象,异形态
        409 语义。
        """
        axes = self._gate(
            "submit_plan_candidate",
            collab_context_id,
            method_binding_id,
            context,
        )
        plan_id = command.swarm_plan_id or f"sp_{uuid4().hex[:12]}"
        now = _now()
        reason = command.reason or "swarm.submit_plan_candidate"
        candidate_plan = SwarmPlan.from_dict(
            {
                "swarm_plan_id": plan_id,
                "method_binding_id": method_binding_id,
                "task_ref": command.task_ref,
                "max_parallelism": command.max_parallelism,
                "max_depth": command.max_depth,
                "budget_resource_refs": list(command.budget_resource_refs),
                "termination_condition": dict(command.termination_condition),
                "state": PlanState.DRAFT.value,
                "transitioned_from": "",
                "transition_reason": reason,
                "transitioned_by": context.actor.principal_id,
                "transitioned_at": now,
            }
        )
        candidate_stages: list[Stage] = []
        for entry in command.stages:
            candidate_stages.append(
                Stage.from_dict(
                    {
                        "stage_id": entry.get("stage_id"),
                        "swarm_plan_ref": plan_id,
                        "task_ref": command.task_ref,
                        "name": entry.get("name"),
                        "dependency_stage_refs": list(
                            entry.get("dependency_stage_refs", [])
                        ),
                        "requirement_refs": list(entry.get("requirement_refs", [])),
                        "input_artifact_refs": list(
                            entry.get("input_artifact_refs", [])
                        ),
                        "output_artifact_roles": list(
                            entry.get("output_artifact_roles", [])
                        ),
                        "completion_condition": entry.get("completion_condition"),
                        "state": StageState.PENDING.value,
                        "transitioned_from": "",
                        "transition_reason": reason,
                        "transitioned_by": context.actor.principal_id,
                        "transitioned_at": now,
                    }
                )
            )
        if command.swarm_plan_id is not None:
            existing = self._store.get_plan(
                axes, plan_id, method_binding_id, collab_context_id
            )
            if existing is not None:
                if self._plan_replay_matches(
                    existing,
                    candidate_plan,
                    candidate_stages,
                    axes,
                    collab_context_id,
                    method_binding_id,
                ):
                    return ProfileOpAcceptedResult(
                        accepted=True,
                        collab_context_id=collab_context_id,
                        method_binding_id=method_binding_id,
                        object_kind="swarm_plan",
                        object_id=existing.swarm_plan_id,
                        state=existing.state.value,
                    )
                raise ContextCollabValidationError(
                    f"idempotent re-submit of swarm plan {plan_id!r} "
                    "differs from the stored shape"
                )
        self._store.insert_plan(axes, candidate_plan, collab_context_id)
        for stage in candidate_stages:
            self._store.insert_stage(axes, method_binding_id, stage, collab_context_id)
        # provenance 行(§2 双平面):operation_ref 指向本次提交,
        # external_execution_id P1 恒为来源操作的审计标识,record 只增
        # 不改、不触碰权威行。
        operation_ref = f"swarm.submit_plan_candidate:{plan_id}"
        self._store.insert_execution_record(
            axes,
            method_binding_id,
            SwarmExecutionRecord.from_dict(
                {
                    "execution_record_id": f"exr_{uuid4().hex[:12]}",
                    "operation_ref": operation_ref,
                    "stage_allocation_ref": None,
                    "execution_profile_ref": "",
                    "external_execution_id": operation_ref,
                    "state": ExecutionRecordState.SUBMITTED.value,
                    "transitioned_from": "",
                    "transition_reason": reason,
                    "transitioned_by": context.actor.principal_id,
                    "transitioned_at": now,
                }
            ),
            collab_context_id,
        )
        return ProfileOpAcceptedResult(
            accepted=True,
            collab_context_id=collab_context_id,
            method_binding_id=method_binding_id,
            object_kind="swarm_plan",
            object_id=plan_id,
            state=PlanState.DRAFT.value,
        )

    def _plan_replay_matches(
        self,
        existing: SwarmPlan,
        candidate_plan: SwarmPlan,
        candidate_stages: list[Stage],
        axes: StoreAxes,
        collab_context_id: str,
        method_binding_id: str,
    ) -> bool:
        """§8.3 重放比对:已受理计划与候选(计划结构 + Stage 集整体
        请求形态)逐项比对;Stages 按 stage_id 对齐,集不等即形态不同。"""
        stored_stages = {
            stage.stage_id: stage
            for stage in self._plan_stages(
                axes,
                collab_context_id,
                method_binding_id,
                existing.swarm_plan_id,
            )
        }
        candidate_by_id = {stage.stage_id: stage for stage in candidate_stages}
        return (
            _shape_of(existing.to_dict(), _PLAN_REQUEST_FIELDS)
            == _shape_of(candidate_plan.to_dict(), _PLAN_REQUEST_FIELDS)
            and set(stored_stages) == set(candidate_by_id)
            and all(
                _shape_of(stored_stages[stage_id].to_dict(), _STAGE_REQUEST_FIELDS)
                == _shape_of(
                    candidate_by_id[stage_id].to_dict(),
                    _STAGE_REQUEST_FIELDS,
                )
                for stage_id in stored_stages
            )
        )

    def validate_plan(
        self,
        collab_context_id: str,
        method_binding_id: str,
        swarm_plan_id: str,
        context: ActorContext,
    ) -> dict:
        """ValidatePlan 用例:结构校验出报告、不改状态(spec §4.3;
        读面授权——任意已认证 actor)。

        报告键(transport-agnostic 投影):``swarm_plan_id`` /
        ``valid``(bool)/ ``violations``(违例文案列表,空列表即通过)/
        ``stage_count``。
        """
        axes = current_axes()
        binding = self._load_binding(axes, collab_context_id, method_binding_id)
        self._authorize("validate_plan", binding, context, collab_context_id)
        plan = self._require_plan(
            axes, collab_context_id, method_binding_id, swarm_plan_id
        )
        stages = self._plan_stages(
            axes, collab_context_id, method_binding_id, swarm_plan_id
        )
        violations = structural_violations(plan, stages)
        return {
            "swarm_plan_id": swarm_plan_id,
            "valid": not violations,
            "violations": violations,
            "stage_count": len(stages),
        }

    def adopt_plan(
        self,
        collab_context_id: str,
        method_binding_id: str,
        swarm_plan_id: str,
        command: SwarmPlanOpCommand,
        context: ActorContext,
    ) -> ProfileOpAcceptedResult:
        """AdoptPlan 用例(DRAFT→READY):重跑结构校验,有违例即拒
        ("未 Validate 通过的拒绝");裁决经机器 adopt。"""
        axes = self._gate("adopt_plan", collab_context_id, method_binding_id, context)
        self._require_structurally_valid_plan(
            axes, collab_context_id, method_binding_id, swarm_plan_id
        )
        return self._transition_plan(
            "adopt_plan",
            collab_context_id,
            method_binding_id,
            swarm_plan_id,
            axes,
            context,
            command,
            "swarm.plan.adopt",
        )

    def _require_structurally_valid_plan(
        self,
        axes: StoreAxes,
        collab_context_id: str,
        method_binding_id: str,
        swarm_plan_id: str,
    ) -> None:
        """结构校验守卫(ValidatePlan 同一份裁决单点):有违例即拒。"""
        plan = self._require_plan(
            axes, collab_context_id, method_binding_id, swarm_plan_id
        )
        violations = structural_violations(
            plan,
            self._plan_stages(
                axes, collab_context_id, method_binding_id, swarm_plan_id
            ),
        )
        if violations:
            raise ContextCollabValidationError(
                f"swarm plan {swarm_plan_id!r} did not pass structural "
                f"validation: {violations}"
            )

    def start_plan(
        self,
        collab_context_id: str,
        method_binding_id: str,
        swarm_plan_id: str,
        command: SwarmPlanOpCommand,
        context: ActorContext,
    ) -> ProfileOpAcceptedResult:
        """StartPlan 用例(READY→RUNNING):计划起飞的同时,依赖已满足
        (无前置或前置全部 SUCCEEDED)的 PENDING Stage 同步转 READY
        (spec §4.3 "前置 READY Stage 同步转 READY")。"""
        axes = self._gate("start_plan", collab_context_id, method_binding_id, context)
        result = self._transition_plan(
            "start_plan",
            collab_context_id,
            method_binding_id,
            swarm_plan_id,
            axes,
            context,
            command,
            "swarm.plan.start",
        )
        plan_stages = self._plan_stages(
            axes, collab_context_id, method_binding_id, swarm_plan_id
        )
        succeeded = {
            stage.stage_id
            for stage in plan_stages
            if stage.state is StageState.SUCCEEDED
        }
        for stage in plan_stages:
            if stage.state is not StageState.PENDING:
                continue
            if not all(ref in succeeded for ref in stage.dependency_stage_refs):
                continue
            new_state = _resolve_stage_ready(stage)
            updated = replace(
                stage,
                state=StageState.from_wire(new_state),
                transitioned_from=stage.state.value,
                transition_reason=command.reason or "swarm.plan.start",
                transitioned_by=context.actor.principal_id,
                transitioned_at=_now(),
            )
            self._store.update_stage_state(
                axes, method_binding_id, updated, collab_context_id
            )
        return result

    def pause_plan(
        self,
        collab_context_id: str,
        method_binding_id: str,
        swarm_plan_id: str,
        command: SwarmPlanOpCommand,
        context: ActorContext,
    ) -> ProfileOpAcceptedResult:
        """PausePlan 用例(RUNNING→PAUSED)。"""
        axes = self._gate("pause_plan", collab_context_id, method_binding_id, context)
        return self._transition_plan(
            "pause_plan",
            collab_context_id,
            method_binding_id,
            swarm_plan_id,
            axes,
            context,
            command,
            "swarm.plan.pause",
        )

    def resume_plan(
        self,
        collab_context_id: str,
        method_binding_id: str,
        swarm_plan_id: str,
        command: SwarmPlanOpCommand,
        context: ActorContext,
    ) -> ProfileOpAcceptedResult:
        """ResumePlan 用例(PAUSED→RUNNING,作者裁决补齐计划状态机
        可达性)。"""
        axes = self._gate("resume_plan", collab_context_id, method_binding_id, context)
        return self._transition_plan(
            "resume_plan",
            collab_context_id,
            method_binding_id,
            swarm_plan_id,
            axes,
            context,
            command,
            "swarm.plan.resume",
        )

    def terminate_plan(
        self,
        collab_context_id: str,
        method_binding_id: str,
        swarm_plan_id: str,
        command: SwarmPlanOpCommand,
        context: ActorContext,
    ) -> ProfileOpAcceptedResult:
        """TerminatePlan 用例(RUNNING/PAUSED→CANCELLED,终态;之后
        拒绝一切计划扩展)。

        扩展拒绝由 Stage 分配面的统一 RUNNING 门槛承载
        (``SwarmStageOpsMixin._require_running_plan``):CANCELLED 无
        出边,机器再裁一条后继迁移同样拒绝。"""
        axes = self._gate(
            "terminate_plan", collab_context_id, method_binding_id, context
        )
        return self._transition_plan(
            "terminate_plan",
            collab_context_id,
            method_binding_id,
            swarm_plan_id,
            axes,
            context,
            command,
            "swarm.plan.terminate",
        )
