"""Task 11:Swarm 方法剖面(五值对象 + 11 权威面操作)单测。

覆盖(spec §4.3):
- 值对象:round-trip / 状态 wire 校验 / 未知字段拒绝 / refs 校验 /
  G4 注册机器词汇对齐(防漂移,plan/stage/allocation/record 四枚举),
  work-kind 闭合词汇;
- store 切片:plan/stage/allocation/profile/record 幂等插入、
  同键异形态拒绝、binding/context 域隔离、update_*_state 缺键 NotFound;
- 权威面 11 操作:profile 声明(幂等)、计划提交(DRAFT + PENDING
  Stage 集 + provenance 执行记录)、结构校验(Stage 依赖图无环、
  引用存在性、深度/并行上限、完成条件结构——ValidatePlan 报告面)、
  adopt(未通过校验拒绝)、start(前沿 Stage 同步 READY)、pause、
  resume(可达性 pin)、terminate(终态)、allocate(计划 RUNNING
  门槛、profile 声明门槛、lease 结构断言、同 Stage 并行 ACTIVE 上限、
  分配即活即跑)、complete(完成条件非空、目标分配 ACTIVE + 租约
  未过期,缺省取全部 ACTIVE)、fail;
- 双平面边界(§2):provenance record 由服务端自建一行,record 只增
  不改、不触碰权威行(adopt 前后断言 plan 状态);
- RF#2:binding 非 ACTIVE 时副作用操作一律拒绝;结构 Swarm binding
  守卫(Group binding 上的 Swarm 操作拒绝);
- 授权矩阵(G1):10 个 owner 操作 principal 放行、陌生人拒绝;
  validate_plan 是读面(陌生人可达);
- 协议:``SwarmMethodService`` 整体满足
  ``SwarmMethodOpsServiceProtocol`` 的 runtime_checkable isinstance 面。

collet 仓储用手写 fake(受控返回 ``ContextStatusRecord``),store/
授权器/lifecycle 用真实现。
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.method_authorization import (
    SWARM_OWNER_OPS,
    SWARM_READ_OPS,
    StructuralMethodOpsAuthorizer,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.method_state_machines import (
    METHOD_MACHINE_REGISTRY,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
    ContextSpec,
    CurrentSnapshot,
    ProgressSnapshot,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import (
    TaskStatus,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_profile import (
    AllocationState,
    AllocationTargetKind,
    ExecutionMode,
    ExecutionRecordState,
    PlanState,
    Stage,
    StageAllocation,
    StageState,
    SwarmExecutionProfile,
    SwarmExecutionRecord,
    SwarmPlan,
    layer_metrics,
    structural_violations,
)
from agentclaw.community.core.task.context_collab.method_service_api import (
    BindMethodCommand,
    MethodStateChangeCommand,
    SwarmAllocateCommand,
    SwarmExecutionProfileCommand,
    SwarmMethodOpsServiceProtocol,
    SwarmPlanCandidateCommand,
    SwarmPlanOpCommand,
    SwarmStageOutcomeCommand,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    InMemoryMethodStore,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    ContextStatusRecord,
)
from agentclaw.community.core.task.context_collab.service_api import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.services.method_lifecycle_service import (
    MethodLifecycleService,
    current_axes,
)
from agentclaw.community.core.task.context_collab.services.swarm_method_service import (
    SwarmMethodService,
)

COLLET_ID = "collet_swarm_001"
PRINCIPAL_ID = "u_2001"
EXECUTOR_ID = "bot_swarm_exec_001"
NOW = "2026-11-12T08:00:00+00:00"

OBJECTIVE = "为 BBS 公开协作落 Swarm 剖面计划/Stage/分配语义测试"

AXES = current_axes()

PLAN_MACHINE = METHOD_MACHINE_REGISTRY["swarm_plan"]
STAGE_MACHINE = METHOD_MACHINE_REGISTRY["swarm_stage"]
ALLOCATION_MACHINE = METHOD_MACHINE_REGISTRY["swarm_allocation"]
RECORD_MACHINE = METHOD_MACHINE_REGISTRY["swarm_execution_record"]


def make_actor(principal_id: str = PRINCIPAL_ID) -> ActorContext:
    return ActorContext(actor=PrincipalRef("human", principal_id), source="api")


def _future_iso(hours: float = 1.0) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(hours=hours)
    ).isoformat()


def _past_iso(hours: float = 1.0) -> str:
    return (
        datetime.now(timezone.utc) - timedelta(hours=hours)
    ).isoformat()


def _context_spec(*, method_kind: str = "Open + Group") -> ContextSpec:
    return ContextSpec(
        space=SpaceRef(space_id="space_workspace_001", environment="prod"),
        principal_ref=PrincipalRef("human", PRINCIPAL_ID),
        method_kind=method_kind,
        executor_ref=AgentRef("bot", EXECUTOR_ID),
        objective=OBJECTIVE,
        resource_refs=(),
        product_kind=None,
        workflow_kind=None,
    )


def _status_record(**overrides) -> ContextStatusRecord:
    """构造受控 ``ContextStatusRecord``(与 lifecycle 单测同款)。"""
    fields = {
        "collab_context_id": COLLET_ID,
        "status": TaskStatus.IN_PROGRESS.value,
        "method_kind": "Open + Group",
    }
    fields.update(overrides)
    spec = _context_spec(method_kind=fields["method_kind"])
    principal_ref = PrincipalRef("human", PRINCIPAL_ID)
    executor_ref = AgentRef("bot", EXECUTOR_ID)
    collet = Collet(
        collab_context_id=fields["collab_context_id"],
        actor_ref=None,
        context_spec=spec,
        current_snapshot=CurrentSnapshot(
            objective=OBJECTIVE,
            status=fields["status"],
            principal_ref=principal_ref,
            executor_ref=executor_ref,
            method_kind=fields["method_kind"],
            progress=ProgressSnapshot(),
        ),
        current_event_id=None,
        context_version=1,
    )
    return ContextStatusRecord(collet=collet, events=[])


class _FakeCollabRepository:
    """最小 collet 仓储 fake:按 context 返回受控 ``ContextStatusRecord``。"""

    def __init__(self, records: dict[str, ContextStatusRecord]) -> None:
        self._records = dict(records)

    def get_current_status(self, collab_context_id: str):
        return self._records.get(collab_context_id)

    def create_context(self, request):
        raise NotImplementedError("fake only implements get_current_status")

    def append_event(self, collab_context_id: str, request):
        raise NotImplementedError("fake only implements get_current_status")


def make_swarm_service():
    """真 store + 真 authorizer + 真 lifecycle + 受控 fake 仓储的服务四件套。"""
    store = InMemoryMethodStore()
    repo = _FakeCollabRepository({COLLET_ID: _status_record()})
    lifecycle = MethodLifecycleService(
        store=store,
        authorizer=StructuralMethodOpsAuthorizer(),
        collab_repo=repo,
    )
    service = SwarmMethodService(
        store=store,
        authorizer=StructuralMethodOpsAuthorizer(),
        collab_repo=repo,
    )
    return store, repo, lifecycle, service


def active_swarm_binding(
    lifecycle: MethodLifecycleService,
    binding_id: str = "mb_swarm_alpha",
    collab_context_id: str = COLLET_ID,
) -> str:
    """bind + activate 一条嵌套 Swarm binding(11 操作的前置)。"""
    lifecycle.bind(
        collab_context_id,
        BindMethodCommand(
            method_kind="Swarm",
            method_binding_id=binding_id,
            parent_scope_ref="task_scope_root",
            bind_reason="compose",
        ),
        make_actor(),
    )
    lifecycle.activate(
        collab_context_id, binding_id, MethodStateChangeCommand(), make_actor()
    )
    return binding_id


def profile_command(**overrides) -> SwarmExecutionProfileCommand:
    """执行剖面声明命令基型;字段可覆盖。"""
    fields = {
        "mode": "A2A_AGENT",
        "target_ref": "agent://bot_compute_001",
        "supported_work_kinds": ["PLAN", "EXECUTE"],
        "input_contract_ref": "contract://swarm-input",
        "execution_profile_id": "sep_alpha",
        "output_contract_ref": "contract://swarm-output",
        "lifecycle_mapping": {"stage": "exec"},
    }
    fields.update(overrides)
    return SwarmExecutionProfileCommand(**fields)


def plan_command(**overrides) -> SwarmPlanCandidateCommand:
    """计划候选命令基型:双 Stage 链 st1→st2,结构合法。"""
    fields = {
        "task_ref": "task_swarm_build_001",
        "swarm_plan_id": "sp_alpha",
        "max_parallelism": 2,
        "max_depth": 3,
        "budget_resource_refs": ["res://compute/001"],
        "termination_condition": {"all_stages_succeeded": True},
        "stages": [
            {
                "stage_id": "st1",
                "name": "gather",
                "dependency_stage_refs": [],
                "requirement_refs": ["req://search"],
                "input_artifact_refs": [],
                "output_artifact_roles": ["draft"],
                "completion_condition": {"min_sources": 3},
            },
            {
                "stage_id": "st2",
                "name": "assemble",
                "dependency_stage_refs": ["st1"],
                "requirement_refs": [],
                "input_artifact_refs": ["artifact://draft"],
                "output_artifact_roles": ["report"],
                "completion_condition": {"reviewed": True},
            },
        ],
        "reason": "",
    }
    fields.update(overrides)
    return SwarmPlanCandidateCommand(**fields)


def running_plan_harness(
    binding_id: str = "mb_swarm_alpha",
    plan_id: str = "sp_alpha",
):
    """声明剖面 + 提交 + adopt + start 一路铺到 RUNNING 计划与 READY st1。"""
    store, _, lifecycle, service = make_swarm_service()
    binding = active_swarm_binding(lifecycle, binding_id)
    service.declare_execution_profile(
        COLLET_ID, binding, profile_command(), make_actor()
    )
    service.submit_plan_candidate(
        COLLET_ID, binding, plan_command(swarm_plan_id=plan_id), make_actor()
    )
    service.adopt_plan(
        COLLET_ID, binding, plan_id, SwarmPlanOpCommand(), make_actor()
    )
    service.start_plan(
        COLLET_ID, binding, plan_id, SwarmPlanOpCommand(), make_actor()
    )
    return store, lifecycle, service, binding, plan_id


def allocate_command(**overrides) -> SwarmAllocateCommand:
    """分配命令基型;字段可覆盖。"""
    fields = {
        "stage_ref": "st1",
        "target_kind": "AGENT",
        "target_ref": "agent://bot_worker_001",
        "execution_profile_ref": "sep_alpha",
        "lease_expires_at": _future_iso(),
        "allocation_id": "alloc_1",
        "reason": "",
    }
    fields.update(overrides)
    return SwarmAllocateCommand(**fields)


# ---------------------------------------------------------------------------
# 值对象(spec §4.3 YAML + §6.3 审计四字段)
# ---------------------------------------------------------------------------


def plan_dict(**overrides) -> dict:
    fields = {
        "swarm_plan_id": "sp_001",
        "method_binding_id": "mb_swarm_001",
        "task_ref": "task_swarm_build_001",
        "max_parallelism": 2,
        "max_depth": 3,
        "budget_resource_refs": ["res://compute/001"],
        "termination_condition": {"all_stages_succeeded": True},
        "state": "DRAFT",
        "transitioned_from": "",
        "transition_reason": "swarm.submit_plan_candidate",
        "transitioned_by": PRINCIPAL_ID,
        "transitioned_at": NOW,
    }
    fields.update(overrides)
    return fields


def stage_dict(**overrides) -> dict:
    fields = {
        "stage_id": "st_001",
        "swarm_plan_ref": "sp_001",
        "task_ref": "task_swarm_build_001",
        "name": "gather",
        "dependency_stage_refs": [],
        "requirement_refs": [],
        "input_artifact_refs": [],
        "output_artifact_roles": ["draft"],
        "completion_condition": {"min_sources": 3},
        "state": "PENDING",
        "transitioned_from": "",
        "transition_reason": "",
        "transitioned_by": PRINCIPAL_ID,
        "transitioned_at": NOW,
    }
    fields.update(overrides)
    return fields


def allocation_dict(**overrides) -> dict:
    fields = {
        "allocation_id": "alloc_001",
        "stage_ref": "st_001",
        "target_kind": "AGENT",
        "target_ref": "agent://bot_worker_001",
        "execution_profile_ref": "sep_001",
        "lease_ref": "lease_alloc_001",
        "lease_expires_at": "2099-01-01T00:00:00+00:00",
        "qualification_evidence_refs": [],
        "authorization_evidence_refs": [],
        "attempt": 1,
        "state": "ACTIVE",
        "transitioned_from": "PROPOSED",
        "transition_reason": "swarm.stage.allocate",
        "transitioned_by": PRINCIPAL_ID,
        "transitioned_at": NOW,
    }
    fields.update(overrides)
    return fields


def record_dict(**overrides) -> dict:
    fields = {
        "execution_record_id": "exr_001",
        "operation_ref": "swarm.submit_plan_candidate:sp_001",
        "stage_allocation_ref": "alloc_001",
        "execution_profile_ref": "sep_001",
        "external_execution_id": "swarm.submit_plan_candidate:sp_001",
        "state": "SUBMITTED",
        "transitioned_from": "",
        "transition_reason": "",
        "transitioned_by": PRINCIPAL_ID,
        "transitioned_at": NOW,
    }
    fields.update(overrides)
    return fields


class TestSwarmValueObjects:
    def test_round_trips(self):
        for build, owner in (
            (plan_dict, SwarmPlan),
            (stage_dict, Stage),
            (allocation_dict, StageAllocation),
            (record_dict, SwarmExecutionRecord),
        ):
            obj = owner.from_dict(build())
            assert owner.from_dict(obj.to_dict()) == obj

    def test_execution_profile_round_trip_and_defaults(self):
        data = {
            "execution_profile_id": "sep_001",
            "mode": "NATIVE_COMPUTE",
            "target_ref": "compute://runner/001",
            "supported_work_kinds": ["SCHEDULE", "EVALUATE"],
            "input_contract_ref": "contract://in",
            "output_contract_ref": "",
            "lifecycle_mapping": {},
        }
        profile = SwarmExecutionProfile.from_dict(data)
        assert SwarmExecutionProfile.from_dict(
            profile.to_dict()
        ) == profile
        data.pop("lifecycle_mapping")
        assert (
            SwarmExecutionProfile.from_dict(data).lifecycle_mapping == {}
        )

    def test_state_wire_validation(self):
        assert PlanState.from_wire("DRAFT") is PlanState.DRAFT
        assert StageState.from_wire("ALLOCATING") is StageState.ALLOCATING
        assert (
            AllocationState.from_wire("REVOKED")
            is AllocationState.REVOKED
        )
        assert (
            ExecutionRecordState.from_wire("SUBMITTED")
            is ExecutionRecordState.SUBMITTED
        )
        for enum_cls in (
            PlanState, StageState, AllocationState, ExecutionRecordState,
        ):
            with pytest.raises(
                ContextCollabValidationError, match="wire"
            ):
                enum_cls.from_wire("LEFT")

    def test_state_vocabularies_match_registered_machines(self):
        """防漂移:四个状态枚举与 G4 注册机器逐字一致。"""
        expectations = (
            (PlanState, PLAN_MACHINE),
            (StageState, STAGE_MACHINE),
            (AllocationState, ALLOCATION_MACHINE),
            (ExecutionRecordState, RECORD_MACHINE),
        )
        for enum_cls, machine in expectations:
            assert {s.value for s in enum_cls} == {
                s.name for s in machine.states
            }

    def test_target_kind_and_mode_wire_validation(self):
        assert (
            AllocationTargetKind.from_wire("COMPUTE")
            is AllocationTargetKind.COMPUTE
        )
        assert (
            ExecutionMode.from_wire("A2A_AGENT") is ExecutionMode.A2A_AGENT
        )
        with pytest.raises(ContextCollabValidationError, match="target"):
            AllocationTargetKind.from_wire("ENGINE")
        with pytest.raises(ContextCollabValidationError, match="mode"):
            ExecutionMode.from_wire("A2A_GATEWAY")

    def test_work_kinds_closed_and_non_empty(self):
        base = {
            "execution_profile_id": "sep_001",
            "mode": "A2A_AGENT",
            "target_ref": "agent://bot_compute_001",
            "supported_work_kinds": ["PLAN"],
            "input_contract_ref": "contract://swarm-input",
            "output_contract_ref": "",
            "lifecycle_mapping": {},
        }
        with pytest.raises(ContextCollabValidationError, match="work kind"):
            SwarmExecutionProfile.from_dict(
                {**base, "supported_work_kinds": ["TRANSLATE"]}
            )
        with pytest.raises(
            ContextCollabValidationError, match="non-empty subset"
        ):
            SwarmExecutionProfile.from_dict(
                {**base, "supported_work_kinds": []}
            )

    def test_unknown_field_rejected(self):
        for build, owner in (
            (plan_dict, SwarmPlan),
            (stage_dict, Stage),
            (allocation_dict, StageAllocation),
            (record_dict, SwarmExecutionRecord),
        ):
            with pytest.raises(
                ContextCollabValidationError, match="unknown field"
            ):
                owner.from_dict({**build(), "extra": 1})

    def test_required_fields_non_empty(self):
        for field in ("swarm_plan_id", "method_binding_id", "task_ref"):
            with pytest.raises(ContextCollabValidationError):
                SwarmPlan.from_dict(plan_dict(**{field: ""}))
        for field in ("stage_id", "swarm_plan_ref", "task_ref", "name"):
            with pytest.raises(ContextCollabValidationError):
                Stage.from_dict(stage_dict(**{field: ""}))
        for field in ("allocation_id", "stage_ref", "target_ref",
                      "execution_profile_ref", "lease_ref",
                      "lease_expires_at"):
            with pytest.raises(ContextCollabValidationError):
                StageAllocation.from_dict(allocation_dict(**{field: ""}))
        for field in ("execution_record_id", "operation_ref",
                      "external_execution_id"):
            with pytest.raises(ContextCollabValidationError):
                SwarmExecutionRecord.from_dict(record_dict(**{field: ""}))

    def test_positive_integers_rejected_for_bool_and_non_int(self):
        for bad in (0, -1, "2", 2.0, True, None):
            with pytest.raises(ContextCollabValidationError):
                SwarmPlan.from_dict(plan_dict(max_parallelism=bad))
            with pytest.raises(ContextCollabValidationError):
                SwarmPlan.from_dict(plan_dict(max_depth=bad))
            with pytest.raises(ContextCollabValidationError):
                StageAllocation.from_dict(allocation_dict(attempt=bad))

    def test_condition_must_be_object(self):
        for bad in (None, [], "x", 1):
            with pytest.raises(
                ContextCollabValidationError, match="termination_condition"
            ):
                SwarmPlan.from_dict(plan_dict(termination_condition=bad))
            with pytest.raises(
                ContextCollabValidationError, match="completion_condition"
            ):
                Stage.from_dict(stage_dict(completion_condition=bad))

    def test_record_optional_fields(self):
        data = record_dict()
        data.pop("stage_allocation_ref")
        assert (
            SwarmExecutionRecord.from_dict(data).stage_allocation_ref is None
        )
        # P1 裁决:计划候选 provenance 行允许空 execution_profile_ref。
        assert (
            SwarmExecutionRecord.from_dict(
                record_dict(execution_profile_ref="")
            ).execution_profile_ref
            == ""
        )
        with pytest.raises(ContextCollabValidationError):
            SwarmExecutionRecord.from_dict(
                record_dict(stage_allocation_ref="")
            )

    def test_refs_must_be_non_empty_strings(self):
        with pytest.raises(
            ContextCollabValidationError, match="budget_resource_refs"
        ):
            SwarmPlan.from_dict(plan_dict(budget_resource_refs=[""]))
        with pytest.raises(
            ContextCollabValidationError, match="dependency_stage_refs"
        ):
            Stage.from_dict(stage_dict(dependency_stage_refs=[""]))


class TestStructuralValidation:
    """layer_metrics / structural_violations:图无环、引用存在性、
    深度/并行上限、完成条件结构(ValidatePlan 报告面的裁决单点)。"""

    def _plan(self, **overrides) -> SwarmPlan:
        return SwarmPlan.from_dict(plan_dict(**overrides))

    def test_valid_chain_reports_no_violations(self):
        st1 = Stage.from_dict(stage_dict(stage_id="st1"))
        st2 = Stage.from_dict(
            stage_dict(stage_id="st2", dependency_stage_refs=["st1"])
        )
        assert structural_violations(self._plan(), [st1, st2]) == []

    def test_empty_completion_condition_violation(self):
        st = Stage.from_dict(stage_dict(completion_condition={}))
        violations = structural_violations(self._plan(), [st])
        assert any("completion_condition" in v for v in violations)

    def test_unknown_dependency_reference(self):
        st = Stage.from_dict(
            stage_dict(dependency_stage_refs=["st_ghost"])
        )
        violations = structural_violations(self._plan(), [st])
        assert any("st_ghost" in v for v in violations)

    def test_cycle_detected(self):
        st1 = Stage.from_dict(
            stage_dict(stage_id="st1", dependency_stage_refs=["st2"])
        )
        st2 = Stage.from_dict(
            stage_dict(stage_id="st2", dependency_stage_refs=["st1"])
        )
        violations = structural_violations(self._plan(), [st1, st2])
        assert any("cycle" in v for v in violations)

    def test_depth_limit(self):
        plan = self._plan(max_depth=2)
        st1 = Stage.from_dict(stage_dict(stage_id="st1"))
        st2 = Stage.from_dict(
            stage_dict(stage_id="st2", dependency_stage_refs=["st1"])
        )
        st3 = Stage.from_dict(
            stage_dict(stage_id="st3", dependency_stage_refs=["st2"])
        )
        assert layer_metrics([st1, st2, st3]) == (3, 1)
        violations = structural_violations(plan, [st1, st2, st3])
        assert any("depth 3" in v for v in violations)

    def test_parallelism_limit(self):
        plan = self._plan(max_parallelism=2)
        stages = [
            Stage.from_dict(stage_dict(stage_id=f"st{i}"))
            for i in range(3)
        ]
        assert layer_metrics(stages) == (1, 3)
        violations = structural_violations(plan, stages)
        assert any("parallel stage width 3" in v for v in violations)


# ---------------------------------------------------------------------------
# store 切片(五组幂等 CRUD + 域隔离;spec §6.1)
# ---------------------------------------------------------------------------


class TestSwarmStoreSlice:
    def test_plan_idempotent_insert_and_shape_conflict(self):
        store = InMemoryMethodStore()
        plan = SwarmPlan.from_dict(plan_dict())
        assert store.insert_plan(AXES, plan, COLLET_ID) is plan
        assert store.insert_plan(AXES, plan, COLLET_ID) is plan
        with pytest.raises(
            ContextCollabValidationError, match="stored shape"
        ):
            store.insert_plan(
                AXES,
                SwarmPlan.from_dict(plan_dict(max_parallelism=5)),
                COLLET_ID,
            )
        assert (
            store.get_plan(AXES, "sp_001", "mb_swarm_001", COLLET_ID)
            is plan
        )
        assert store.get_plan(AXES, "sp_999", "mb_swarm_001", COLLET_ID) is None

    def test_update_state_requires_existing_key(self):
        store = InMemoryMethodStore()
        plan = store.insert_plan(
            AXES, SwarmPlan.from_dict(plan_dict()), COLLET_ID
        )
        updated = replace(
            plan,
            state=PlanState.from_wire("READY"),
            transitioned_from="DRAFT",
        )
        store.update_plan_state(AXES, updated, COLLET_ID)
        assert (
            store.get_plan(AXES, "sp_001", "mb_swarm_001", COLLET_ID).state
            is PlanState.READY
        )
        with pytest.raises(ContextCollabNotFoundError):
            store.update_plan_state(
                AXES,
                SwarmPlan.from_dict(
                    plan_dict(swarm_plan_id="sp_ghost")
                ),
                COLLET_ID,
            )

    def test_stage_and_allocation_scoped_by_binding_and_context(self):
        store = InMemoryMethodStore()
        stage = Stage.from_dict(stage_dict())
        store.insert_stage(AXES, "mb_swarm_001", stage, COLLET_ID)
        # binding 段是键的一部分:同 id 跨 binding / 跨 context 互不可见。
        store.insert_stage(
            AXES, "mb_swarm_002", Stage.from_dict(stage_dict()), COLLET_ID
        )
        store.insert_stage(
            AXES, "mb_swarm_001", Stage.from_dict(stage_dict()), "collet_x"
        )
        assert [
            s.stage_id
            for s in store.list_stages(AXES, "mb_swarm_001", COLLET_ID)
        ] == ["st_001"]
        assert [
            s.stage_id
            for s in store.list_stages(AXES, "mb_swarm_002", COLLET_ID)
        ] == ["st_001"]
        allocation = StageAllocation.from_dict(allocation_dict())
        store.insert_allocation(AXES, "mb_swarm_001", allocation, COLLET_ID)
        with pytest.raises(
            ContextCollabValidationError, match="stored shape"
        ):
            store.insert_allocation(
                AXES,
                "mb_swarm_001",
                StageAllocation.from_dict(
                    allocation_dict(state="RELEASED")
                ),
                COLLET_ID,
            )
        released = replace(allocation, state=AllocationState.from_wire("RELEASED"))
        store.update_allocation_state(
            AXES, "mb_swarm_001", released, COLLET_ID
        )
        with pytest.raises(ContextCollabNotFoundError):
            store.update_allocation_state(
                AXES,
                "mb_swarm_001",
                StageAllocation.from_dict(
                    allocation_dict(allocation_id="alloc_ghost")
                ),
                COLLET_ID,
            )

    def test_execution_profile_and_record_idempotent_insert(self):
        store = InMemoryMethodStore()
        profile = SwarmExecutionProfile.from_dict(
            {
                "execution_profile_id": "sep_001",
                "mode": "A2A_AGENT",
                "target_ref": "agent://bot_x",
                "supported_work_kinds": ["EXECUTE"],
                "input_contract_ref": "contract://in",
                "output_contract_ref": "",
                "lifecycle_mapping": {},
            }
        )
        assert (
            store.insert_execution_profile(
                AXES, "mb_swarm_001", profile, COLLET_ID
            )
            is profile
        )
        assert (
            store.get_execution_profile(
                AXES, "sep_001", "mb_swarm_001", COLLET_ID
            )
            is profile
        )
        record = SwarmExecutionRecord.from_dict(record_dict())
        assert (
            store.insert_execution_record(
                AXES, "mb_swarm_001", record, COLLET_ID
            )
            is record
        )
        assert [
            r.execution_record_id
            for r in store.list_execution_records(
                AXES, "mb_swarm_001", COLLET_ID
            )
        ] == ["exr_001"]


# ---------------------------------------------------------------------------
# 服务操作族(spec §4.3 双达面:plan 状态机 + Stage 分配/终局)
# ---------------------------------------------------------------------------


class TestSwarmPlanLifecycle:
    def test_declare_execution_profile_accepted_and_idempotent(self):
        store, _, lifecycle, service = make_swarm_service()
        binding = active_swarm_binding(lifecycle)
        first = service.declare_execution_profile(
            COLLET_ID, binding, profile_command(), make_actor()
        )
        assert first.accepted is True
        assert first.object_kind == "swarm_execution_profile"
        assert first.object_id == "sep_alpha"
        assert first.state == "ACTIVE"  # 声明即活(P1 无状态列)
        # §8.3 幂等重放:同 ID 同形态返回既有对象,不新增行。
        replay = service.declare_execution_profile(
            COLLET_ID, binding, profile_command(), make_actor()
        )
        assert replay.object_id == first.object_id
        assert (
            len(
                store.list_execution_profiles(AXES, binding, COLLET_ID)
            )
            == 1
        )
        with pytest.raises(
            ContextCollabValidationError, match="stored shape"
        ):
            service.declare_execution_profile(
                COLLET_ID,
                binding,
                profile_command(target_ref="agent://other"),
                make_actor(),
            )

    def test_submit_projects_draft_plan_pending_stages_and_provenance(self):
        store, _, lifecycle, service = make_swarm_service()
        binding = active_swarm_binding(lifecycle)
        result = service.submit_plan_candidate(
            COLLET_ID, binding, plan_command(), make_actor()
        )
        assert result.state == "DRAFT"
        assert result.object_kind == "swarm_plan"
        plan = store.get_plan(AXES, "sp_alpha", binding, COLLET_ID)
        assert plan.state is PlanState.DRAFT
        assert plan.transitioned_from == ""
        assert plan.transition_reason == "swarm.submit_plan_candidate"
        stages = store.list_stages(AXES, binding, COLLET_ID)
        assert [s.stage_id for s in stages] == ["st1", "st2"]
        assert all(s.state is StageState.PENDING for s in stages)
        assert stages[0].swarm_plan_ref == "sp_alpha"
        # ...P1 恒为来源操作审计标识(§2 双平面),adopt 前计划状态不动。
        records = store.list_execution_records(AXES, binding, COLLET_ID)
        assert [r.state for r in records] == [
            ExecutionRecordState.SUBMITTED
        ]
        assert (
            records[0].operation_ref
            == "swarm.submit_plan_candidate:sp_alpha"
        )
        assert (
            records[0].external_execution_id == records[0].operation_ref
        )
        assert records[0].execution_profile_ref == ""
        assert store.get_plan(AXES, "sp_alpha", binding, COLLET_ID).state is (
            PlanState.DRAFT
        )

    def test_submit_replay_and_shape_conflict(self):
        store, _, lifecycle, service = make_swarm_service()
        binding = active_swarm_binding(lifecycle)
        first = service.submit_plan_candidate(
            COLLET_ID, binding, plan_command(), make_actor()
        )
        replay = service.submit_plan_candidate(
            COLLET_ID, binding, plan_command(), make_actor()
        )
        assert replay.object_id == first.object_id == "sp_alpha"
        # provenance 行恰好一行:重放不新增 record(§8.3)。
        assert (
            len(store.list_execution_records(AXES, binding, COLLET_ID)) == 1
        )
        with pytest.raises(
            ContextCollabValidationError, match="stored shape"
        ):
            service.submit_plan_candidate(
                COLLET_ID,
                binding,
                plan_command(task_ref="task_swarm_other"),
                make_actor(),
            )

    def test_external_record_never_touches_authority_state(self):
        """双平面隔离(§2):provenance record 落库前后,权威行状态
        不被 record 创建触碰;adopt 推进的是权威行本身。"""
        store, _, lifecycle, service = make_swarm_service()
        binding = active_swarm_binding(lifecycle)
        service.submit_plan_candidate(
            COLLET_ID, binding, plan_command(), make_actor()
        )
        plan_before = store.get_plan(AXES, "sp_alpha", binding, COLLET_ID)
        assert plan_before.state is PlanState.DRAFT
        service.adopt_plan(
            COLLET_ID, binding, "sp_alpha", SwarmPlanOpCommand(), make_actor()
        )
        plan_after = store.get_plan(AXES, "sp_alpha", binding, COLLET_ID)
        assert plan_after.state is PlanState.READY
        # record 行未被 adopt 推进(只增不改)。
        record = store.list_execution_records(AXES, binding, COLLET_ID)[0]
        assert record.state is ExecutionRecordState.SUBMITTED
        assert record.transition_reason == plan_before.transition_reason

    def test_validate_plan_reports_structural_violations(self):
        store, _, lifecycle, service = make_swarm_service()
        binding = active_swarm_binding(lifecycle)
        # 环:st1→st2→st1。
        service.submit_plan_candidate(
            COLLET_ID,
            binding,
            plan_command(
                stages=[
                    {
                        "stage_id": "st1",
                        "name": "a",
                        "dependency_stage_refs": ["st2"],
                        "completion_condition": {"x": 1},
                    },
                    {
                        "stage_id": "st2",
                        "name": "b",
                        "dependency_stage_refs": ["st1"],
                        "completion_condition": {"x": 1},
                    },
                ]
            ),
            make_actor(),
        )
        report = service.validate_plan(
            COLLET_ID, binding, "sp_alpha", make_actor()
        )
        assert report["swarm_plan_id"] == "sp_alpha"
        assert report["valid"] is False
        assert any("cycle" in v for v in report["violations"])
        assert report["stage_count"] == 2
        # 结构违例是机器外拒绝:计划不被 ValidatePlan 推进。
        assert (
            store.get_plan(AXES, "sp_alpha", binding, COLLET_ID).state
            is PlanState.DRAFT
        )

    def test_adopt_rejects_structurally_invalid_and_accepts_valid(self):
        store, _, lifecycle, service = make_swarm_service()
        binding = active_swarm_binding(lifecycle)
        service.submit_plan_candidate(
            COLLET_ID,
            binding,
            plan_command(
                stages=[
                    {
                        "stage_id": "st1",
                        "name": "a",
                        "dependency_stage_refs": ["st_ghost"],
                        "completion_condition": {"x": 1},
                    }
                ]
            ),
            make_actor(),
        )
        with pytest.raises(
            ContextCollabValidationError, match="structural validation"
        ):
            service.adopt_plan(
                COLLET_ID,
                binding,
                "sp_alpha",
                SwarmPlanOpCommand(),
                make_actor(),
            )
        assert (
            store.get_plan(AXES, "sp_alpha", binding, COLLET_ID).state
            is PlanState.DRAFT
        )

    def test_start_promotes_frontier_stages_to_ready(self):
        store, _, lifecycle, service = make_swarm_service()
        binding = active_swarm_binding(lifecycle)
        service.submit_plan_candidate(
            COLLET_ID, binding, plan_command(), make_actor()
        )
        service.adopt_plan(
            COLLET_ID, binding, "sp_alpha", SwarmPlanOpCommand(), make_actor()
        )
        started = service.start_plan(
            COLLET_ID, binding, "sp_alpha", SwarmPlanOpCommand(), make_actor()
        )
        assert started.state == "RUNNING"
        states = {
            s.stage_id: s.state
            for s in store.list_stages(AXES, binding, COLLET_ID)
        }
        # 无前置的 st1 同步转 READY,依赖未满足的 st2 留 PENDING。
        assert states == {"st1": StageState.READY, "st2": StageState.PENDING}

    def test_pause_resume_reachability(self):
        """作者裁决补漏:ResumePlan(PAUSED→RUNNING)必须可达。"""
        store, _, service, binding, plan_id = running_plan_harness()
        paused = service.pause_plan(
            COLLET_ID, binding, plan_id, SwarmPlanOpCommand(), make_actor()
        )
        assert paused.state == "PAUSED"
        resumed = service.resume_plan(
            COLLET_ID, binding, plan_id,
            SwarmPlanOpCommand(reason="恢复计划"), make_actor(),
        )
        assert resumed.state == "RUNNING"
        plan = store.get_plan(AXES, plan_id, binding, COLLET_ID)
        assert plan.state is PlanState.RUNNING
        assert plan.transition_reason == "恢复计划"

    def test_terminate_then_plan_extension_refused(self):
        """TerminatePlan 终态;之后一切计划扩展(Stage 分配)拒绝。"""
        store, _, service, binding, plan_id = running_plan_harness()
        terminated = service.terminate_plan(
            COLLET_ID, binding, plan_id, SwarmPlanOpCommand(), make_actor()
        )
        assert terminated.state == "CANCELLED"
        with pytest.raises(
            ContextCollabValidationError, match="RUNNING"
        ):
            service.allocate_stage(
                COLLET_ID, binding, allocate_command(), make_actor()
            )
        # 机器同样拒绝终态计划上的一切后继迁移。
        with pytest.raises(
            ContextCollabValidationError, match="does not accept event"
        ):
            service.pause_plan(
                COLLET_ID, binding, plan_id, SwarmPlanOpCommand(), make_actor()
            )

    def test_illegal_plan_transitions_rejected_by_machine(self):
        _, _, lifecycle, service = make_swarm_service()
        binding = active_swarm_binding(lifecycle)
        service.submit_plan_candidate(
            COLLET_ID, binding, plan_command(), make_actor()
        )
        for op_name in ("start_plan", "pause_plan"):
            with pytest.raises(
                ContextCollabValidationError, match="does not accept event"
            ):
                getattr(service, op_name)(
                    COLLET_ID,
                    binding,
                    "sp_alpha",
                    SwarmPlanOpCommand(),
                    make_actor(),
                )
        service.adopt_plan(
            COLLET_ID, binding, "sp_alpha", SwarmPlanOpCommand(), make_actor()
        )
        # 重复 adopt 幂等拒绝(机器无出边)。
        with pytest.raises(
            ContextCollabValidationError, match="does not accept event"
        ):
            service.adopt_plan(
                COLLET_ID, binding, "sp_alpha", SwarmPlanOpCommand(),
                make_actor(),
            )

    def test_missing_plan_not_found(self):
        _, _, _, service = make_swarm_service()
        with pytest.raises(ContextCollabNotFoundError):
            service.adopt_plan(
                COLLET_ID, "mb_ghost", "sp_none", SwarmPlanOpCommand(),
                make_actor(),
            )
        with pytest.raises(ContextCollabNotFoundError):
            service.validate_plan(
                COLLET_ID, "mb_ghost", "sp_none", make_actor()
            )


class TestSwarmAllocateAndOutcome:
    def test_allocate_projects_active_allocation_and_running_stage(self):
        store, _, service, binding, plan_id = running_plan_harness()
        result = service.allocate_stage(
            COLLET_ID, binding, allocate_command(), make_actor()
        )
        assert result.accepted is True
        assert result.object_kind == "swarm_allocation"
        assert result.state == "ACTIVE"
        allocation = store.get_allocation(
            AXES, "alloc_1", binding, COLLET_ID
        )
        assert allocation is not None
        assert allocation.state is AllocationState.ACTIVE
        assert allocation.attempt == 1
        assert allocation.lease_ref == "lease_alloc_1"
        assert allocation.execution_profile_ref == "sep_alpha"
        assert allocation.transitioned_from == "PROPOSED"
        stage = store.get_stage(AXES, "st1", binding, COLLET_ID)
        assert stage.state is StageState.RUNNING  # 分配即活即跑
        assert stage.transitioned_from == "READY"

    def test_allocate_replay_is_idempotent(self):
        store, _, service, binding, plan_id = running_plan_harness()
        # 重放携带同一份未过期 lease(请求形态白名单内 lease 是请求
        # 差异;attempt 是 store 派生计数,不在比对面)。
        lease = _future_iso(2)
        first = service.allocate_stage(
            COLLET_ID, binding,
            allocate_command(allocation_id="alloc_x", lease_expires_at=lease),
            make_actor(),
        )
        replay = service.allocate_stage(
            COLLET_ID, binding,
            allocate_command(allocation_id="alloc_x", lease_expires_at=lease),
            make_actor(),
        )
        assert replay.object_id == first.object_id == "alloc_x"
        assert (
            len(store.list_allocations(AXES, binding, COLLET_ID)) == 1
        )
        with pytest.raises(
            ContextCollabValidationError, match="stored shape"
        ):
            service.allocate_stage(
                COLLET_ID,
                binding,
                allocate_command(
                    allocation_id="alloc_x", target_ref="agent://other"
                ),
                make_actor(),
            )

    def test_allocate_requires_running_plan(self):
        _, _, lifecycle, service = make_swarm_service()
        binding = active_swarm_binding(lifecycle)
        service.declare_execution_profile(
            COLLET_ID, binding, profile_command(), make_actor()
        )
        service.submit_plan_candidate(
            COLLET_ID, binding, plan_command(), make_actor()
        )
        with pytest.raises(
            ContextCollabValidationError, match="RUNNING"
        ):
            service.allocate_stage(
                COLLET_ID, binding, allocate_command(), make_actor()
            )

    def test_allocate_requires_declared_profile(self):
        _, _, service, binding, plan_id = running_plan_harness()
        with pytest.raises(
            ContextCollabValidationError, match="not declared"
        ):
            service.allocate_stage(
                COLLET_ID,
                binding,
                allocate_command(execution_profile_ref="sep_ghost"),
                make_actor(),
            )

    @pytest.mark.parametrize(
        "lease,error",
        [
            (_past_iso(), "not in the future"),
            ("not-a-timestamp", "ISO timestamp"),
        ],
    )
    def test_allocate_rejects_bad_lease(self, lease, error):
        _, _, service, binding, plan_id = running_plan_harness()
        with pytest.raises(ContextCollabValidationError, match=error):
            service.allocate_stage(
                COLLET_ID,
                binding,
                allocate_command(lease_expires_at=lease),
                make_actor(),
            )

    def test_allocate_enforces_stage_parallelism_cap(self):
        """同 Stage 并行 ACTIVE 分配上限(spec §4.3 结构断言;预置
        max_parallelism=2 条 ACTIVE 分配模拟并行持租,第三条即拒绝)。"""
        store, _, service, binding, plan_id = running_plan_harness()
        assert (
            store.get_plan(AXES, plan_id, binding, COLLET_ID).max_parallelism
            == 2
        )
        for pre_id in ("alloc_pre_1", "alloc_pre_2"):
            store.insert_allocation(
                AXES,
                binding,
                StageAllocation.from_dict(
                    allocation_dict(
                        allocation_id=pre_id,
                        stage_ref="st1",
                        lease_expires_at=_future_iso(),
                    )
                ),
                COLLET_ID,
            )
        with pytest.raises(
            ContextCollabValidationError, match="ACTIVE allocations"
        ):
            service.allocate_stage(
                COLLET_ID, binding, allocate_command(), make_actor()
            )

    def test_complete_releases_allocations_and_succeeds_stage(self):
        store, _, service, binding, plan_id = running_plan_harness()
        service.allocate_stage(
            COLLET_ID, binding, allocate_command(), make_actor()
        )
        completed = service.complete_stage(
            COLLET_ID, binding, "st1", SwarmStageOutcomeCommand(), make_actor()
        )
        assert completed.accepted is True
        assert completed.object_kind == "swarm_stage"
        assert completed.state == "SUCCEEDED"
        stage = store.get_stage(AXES, "st1", binding, COLLET_ID)
        assert stage.state is StageState.SUCCEEDED
        assert stage.transitioned_from == "RUNNING"
        allocation = store.get_allocation(
            AXES, "alloc_1", binding, COLLET_ID
        )
        assert allocation.state is AllocationState.RELEASED

    def test_complete_rejects_expired_lease(self):
        """§8.1:租约过期的 ACTIVE 分配不得完成 Stage,须先释放再重分配。"""
        store, _, service, binding, plan_id = running_plan_harness()
        store.insert_allocation(
            AXES,
            binding,
            StageAllocation.from_dict(
                allocation_dict(
                    allocation_id="alloc_stale",
                    stage_ref="st1",
                    lease_expires_at=_past_iso(),
                )
            ),
            COLLET_ID,
        )
        with pytest.raises(
            ContextCollabValidationError, match="lease expired"
        ):
            service.complete_stage(
                COLLET_ID,
                binding,
                "st1",
                SwarmStageOutcomeCommand(allocation_refs=["alloc_stale"]),
                make_actor(),
            )

    def test_complete_rejects_non_active_allocation_ref(self):
        store, _, service, binding, plan_id = running_plan_harness()
        store.insert_allocation(
            AXES,
            binding,
            StageAllocation.from_dict(
                allocation_dict(
                    allocation_id="alloc_released",
                    stage_ref="st1",
                    state="RELEASED",
                    lease_expires_at=_future_iso(),
                )
            ),
            COLLET_ID,
        )
        with pytest.raises(
            ContextCollabValidationError, match="only ACTIVE"
        ):
            service.complete_stage(
                COLLET_ID,
                binding,
                "st1",
                SwarmStageOutcomeCommand(allocation_refs=["alloc_released"]),
                make_actor(),
            )
        with pytest.raises(ContextCollabNotFoundError):
            service.complete_stage(
                COLLET_ID,
                binding,
                "st1",
                SwarmStageOutcomeCommand(allocation_refs=["alloc_ghost"]),
                make_actor(),
            )

    def test_complete_requires_completion_condition_structure(self):
        store, _, service, binding, plan_id = running_plan_harness()
        # 直落一条 RUNNING 且空完成条件的 Stage(store 注入,服务面 adopt
        # 守卫拦不到的对象面裁断)。
        store.insert_stage(
            AXES,
            binding,
            Stage.from_dict(
                stage_dict(
                    stage_id="st_bare",
                    swarm_plan_ref="sp_alpha",
                    completion_condition={},
                    state="RUNNING",
                    transitioned_from="ALLOCATING",
                )
            ),
            COLLET_ID,
        )
        with pytest.raises(
            ContextCollabValidationError, match="completion_condition"
        ):
            service.complete_stage(
                COLLET_ID,
                binding,
                "st_bare",
                SwarmStageOutcomeCommand(),
                make_actor(),
            )

    def test_fail_stage_from_running(self):
        """fail_stage:RUNNING──swarm.stage.fail→FAILED;分配行不动
        (审计保留,P1 不做隐式级联)。机器无 READY→FAILED 边,未分配
        的 READY Stage 不可直接 fail。"""
        store, _, service, binding, plan_id = running_plan_harness()
        with pytest.raises(
            ContextCollabValidationError, match="does not accept event"
        ):
            service.fail_stage(
                COLLET_ID, binding, "st1", SwarmStageOutcomeCommand(),
                make_actor(),
            )
        service.allocate_stage(
            COLLET_ID, binding, allocate_command(), make_actor()
        )
        failed = service.fail_stage(
            COLLET_ID,
            binding,
            "st1",
            SwarmStageOutcomeCommand(reason="执行失败"),
            make_actor(),
        )
        assert failed.state == "FAILED"
        stage = store.get_stage(AXES, "st1", binding, COLLET_ID)
        assert stage.state is StageState.FAILED
        assert stage.transition_reason == "执行失败"
        allocation = store.get_allocation(AXES, "alloc_1", binding, COLLET_ID)
        assert allocation.state is AllocationState.ACTIVE

    def test_missing_stage_not_found(self):
        _, _, service, binding, plan_id = running_plan_harness()
        for op_call in (
            lambda: service.complete_stage(
                COLLET_ID, binding, "st_ghost", SwarmStageOutcomeCommand(),
                make_actor(),
            ),
            lambda: service.fail_stage(
                COLLET_ID, binding, "st_ghost", SwarmStageOutcomeCommand(),
                make_actor(),
            ),
            lambda: service.allocate_stage(
                COLLET_ID, binding, allocate_command(stage_ref="st_ghost"),
                make_actor(),
            ),
        ):
            with pytest.raises(ContextCollabNotFoundError):
                op_call()


# ---------------------------------------------------------------------------
# 守卫:RF#2 + 结构 Swarm binding + 授权矩阵(G1/T6)
# ---------------------------------------------------------------------------


OWNER_OPS_ALL = (
    "declare_execution_profile",
    "submit_plan_candidate",
    "adopt_plan",
    "start_plan",
    "pause_plan",
    "resume_plan",
    "allocate_stage",
    "complete_stage",
    "fail_stage",
    "terminate_plan",
)


class TestSwarmGuards:
    def test_rf2_non_active_binding_rejected(self):
        """RF#2:binding 非 ACTIVE 时十个副作用操作一律拒绝。"""
        store, _, lifecycle, service = make_swarm_service()
        lifecycle.bind(
            COLLET_ID,
            BindMethodCommand(
                method_kind="Swarm",
                method_binding_id="mb_swarm_bound",
                parent_scope_ref="task_scope_root",
            ),
            make_actor(),
        )
        bound = "mb_swarm_bound"
        callables = [
            lambda: service.declare_execution_profile(
                COLLET_ID, bound, profile_command(), make_actor()
            ),
            lambda: service.submit_plan_candidate(
                COLLET_ID, bound, plan_command(), make_actor()
            ),
            lambda: service.adopt_plan(
                COLLET_ID, bound, "sp_none", SwarmPlanOpCommand(), make_actor()
            ),
            lambda: service.start_plan(
                COLLET_ID, bound, "sp_none", SwarmPlanOpCommand(), make_actor()
            ),
            lambda: service.pause_plan(
                COLLET_ID, bound, "sp_none", SwarmPlanOpCommand(), make_actor()
            ),
            lambda: service.resume_plan(
                COLLET_ID, bound, "sp_none", SwarmPlanOpCommand(), make_actor()
            ),
            lambda: service.terminate_plan(
                COLLET_ID, bound, "sp_none", SwarmPlanOpCommand(), make_actor()
            ),
            lambda: service.allocate_stage(
                COLLET_ID, bound, allocate_command(), make_actor()
            ),
            lambda: service.complete_stage(
                COLLET_ID, bound, "st1", SwarmStageOutcomeCommand(),
                make_actor(),
            ),
            lambda: service.fail_stage(
                COLLET_ID, bound, "st1", SwarmStageOutcomeCommand(),
                make_actor(),
            ),
        ]
        for op_call in callables:
            with pytest.raises(
                ContextCollabValidationError, match="ACTIVE"
            ):
                op_call()

    def test_swarm_ops_reject_non_swarm_binding(self):
        """结构守卫:Group binding 上的 Swarm 剖面操作是结构漂移,拒绝。"""
        _, _, lifecycle, service = make_swarm_service()
        group_binding = "mb_group_only"
        lifecycle.bind(
            COLLET_ID,
            BindMethodCommand(
                method_kind="Group",
                method_binding_id=group_binding,
                parent_scope_ref="task_scope_root",
            ),
            make_actor(),
        )
        lifecycle.activate(
            COLLET_ID, group_binding, MethodStateChangeCommand(), make_actor()
        )
        with pytest.raises(
            ContextCollabValidationError, match="declare Swarm"
        ):
            service.declare_execution_profile(
                COLLET_ID, group_binding, profile_command(), make_actor()
            )
        with pytest.raises(
            ContextCollabValidationError, match="declare Swarm"
        ):
            service.submit_plan_candidate(
                COLLET_ID, group_binding, plan_command(), make_actor()
            )

    def test_owner_ops_reject_stranger(self):
        """T6:十个 owner 操作不放行陌生人(frozenset pin + 行为 pin)。"""
        assert SWARM_OWNER_OPS == frozenset(OWNER_OPS_ALL)
        assert SWARM_READ_OPS == frozenset({"validate_plan"})
        _, _, lifecycle, service = make_swarm_service()
        binding = active_swarm_binding(lifecycle)
        stranger = make_actor("u_9999")
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service.declare_execution_profile(
                COLLET_ID, binding, profile_command(), stranger
            )
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service.submit_plan_candidate(
                COLLET_ID, binding, plan_command(), stranger
            )
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service.adopt_plan(
                COLLET_ID, binding, "sp_none", SwarmPlanOpCommand(), stranger
            )
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service.start_plan(
                COLLET_ID, binding, "sp_none", SwarmPlanOpCommand(), stranger
            )
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service.pause_plan(
                COLLET_ID, binding, "sp_none", SwarmPlanOpCommand(), stranger
            )
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service.resume_plan(
                COLLET_ID, binding, "sp_none", SwarmPlanOpCommand(), stranger
            )
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service.terminate_plan(
                COLLET_ID, binding, "sp_none", SwarmPlanOpCommand(), stranger
            )
        # 分配/终局面的陌生人拒绝用 RUNNING 计划 harness 验证。
        _, _, service2, binding2, plan2 = running_plan_harness()
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service2.allocate_stage(
                COLLET_ID, binding2, allocate_command(), stranger
            )
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service2.complete_stage(
                COLLET_ID, binding2, "st1", SwarmStageOutcomeCommand(), stranger
            )
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service2.fail_stage(
                COLLET_ID, binding2, "st1", SwarmStageOutcomeCommand(), stranger
            )

    def test_validate_plan_is_read_plane(self):
        """validate_plan 是读面:任意已认证 actor(陌生人)可达。"""
        _, _, lifecycle, service = make_swarm_service()
        binding = active_swarm_binding(lifecycle)
        service.submit_plan_candidate(
            COLLET_ID, binding, plan_command(), make_actor()
        )
        report = service.validate_plan(
            COLLET_ID, binding, "sp_alpha", make_actor("u_9999")
        )
        assert report["valid"] is True
        # RF#2 对读面不生效:BOUND binding 也可读取计划报告。
        lifecycle.bind(
            COLLET_ID,
            BindMethodCommand(
                method_kind="Swarm",
                method_binding_id="mb_swarm_bound",
                parent_scope_ref="task_scope_root",
            ),
            make_actor(),
        )
        with pytest.raises(ContextCollabNotFoundError):
            service.validate_plan(
                COLLET_ID, "mb_swarm_bound", "sp_none", make_actor("u_9999")
            )

    def test_missing_binding_not_found(self):
        _, _, _, service = make_swarm_service()
        with pytest.raises(ContextCollabNotFoundError):
            service.declare_execution_profile(
                COLLET_ID, "mb_ghost", profile_command(), make_actor()
            )


class TestSwarmProtocolConformance:
    def test_service_satisfies_swarm_protocol(self):
        """``SwarmMethodService`` 满足 ``SwarmMethodOpsServiceProtocol``
        的 runtime_checkable isinstance 面。"""
        _, _, _, service = make_swarm_service()
        assert isinstance(service, SwarmMethodOpsServiceProtocol)
