"""Swarm Method profile contracts; fixtures in swarm_profile_support."""

from dataclasses import replace

import pytest
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
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
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    InMemoryMethodStore,
)

from tests.community.core.task.context_collab.swarm_profile_support import (
    ALLOCATION_MACHINE,
    AXES,
    COLLET_ID,
    PLAN_MACHINE,
    RECORD_MACHINE,
    STAGE_MACHINE,
    allocation_dict,
    plan_dict,
    record_dict,
    stage_dict,
)


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
        assert SwarmExecutionProfile.from_dict(profile.to_dict()) == profile
        data.pop("lifecycle_mapping")
        assert SwarmExecutionProfile.from_dict(data).lifecycle_mapping == {}

    def test_state_wire_validation(self):
        assert PlanState.from_wire("DRAFT") is PlanState.DRAFT
        assert StageState.from_wire("ALLOCATING") is StageState.ALLOCATING
        assert AllocationState.from_wire("REVOKED") is AllocationState.REVOKED
        assert (
            ExecutionRecordState.from_wire("SUBMITTED")
            is ExecutionRecordState.SUBMITTED
        )
        for enum_cls in (
            PlanState,
            StageState,
            AllocationState,
            ExecutionRecordState,
        ):
            with pytest.raises(ContextCollabValidationError, match="wire"):
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
            assert {s.value for s in enum_cls} == {s.name for s in machine.states}

    def test_target_kind_and_mode_wire_validation(self):
        assert AllocationTargetKind.from_wire("COMPUTE") is AllocationTargetKind.COMPUTE
        assert ExecutionMode.from_wire("A2A_AGENT") is ExecutionMode.A2A_AGENT
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
        with pytest.raises(ContextCollabValidationError, match="non-empty subset"):
            SwarmExecutionProfile.from_dict({**base, "supported_work_kinds": []})

    def test_unknown_field_rejected(self):
        for build, owner in (
            (plan_dict, SwarmPlan),
            (stage_dict, Stage),
            (allocation_dict, StageAllocation),
            (record_dict, SwarmExecutionRecord),
        ):
            with pytest.raises(ContextCollabValidationError, match="unknown field"):
                owner.from_dict({**build(), "extra": 1})

    def test_required_fields_non_empty(self):
        for field in ("swarm_plan_id", "method_binding_id", "task_ref"):
            with pytest.raises(ContextCollabValidationError):
                SwarmPlan.from_dict(plan_dict(**{field: ""}))
        for field in ("stage_id", "swarm_plan_ref", "task_ref", "name"):
            with pytest.raises(ContextCollabValidationError):
                Stage.from_dict(stage_dict(**{field: ""}))
        for field in (
            "allocation_id",
            "stage_ref",
            "target_ref",
            "execution_profile_ref",
            "lease_ref",
            "lease_expires_at",
        ):
            with pytest.raises(ContextCollabValidationError):
                StageAllocation.from_dict(allocation_dict(**{field: ""}))
        for field in ("execution_record_id", "operation_ref", "external_execution_id"):
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
        assert SwarmExecutionRecord.from_dict(data).stage_allocation_ref is None
        # P1 裁决:计划候选 provenance 行允许空 execution_profile_ref。
        assert (
            SwarmExecutionRecord.from_dict(
                record_dict(execution_profile_ref="")
            ).execution_profile_ref
            == ""
        )
        with pytest.raises(ContextCollabValidationError):
            SwarmExecutionRecord.from_dict(record_dict(stage_allocation_ref=""))

    def test_refs_must_be_non_empty_strings(self):
        with pytest.raises(ContextCollabValidationError, match="budget_resource_refs"):
            SwarmPlan.from_dict(plan_dict(budget_resource_refs=[""]))
        with pytest.raises(ContextCollabValidationError, match="dependency_stage_refs"):
            Stage.from_dict(stage_dict(dependency_stage_refs=[""]))


class TestStructuralValidation:
    """layer_metrics / structural_violations:图无环、引用存在性、
    深度/并行上限、完成条件结构(ValidatePlan 报告面的裁决单点)。"""

    def _plan(self, **overrides) -> SwarmPlan:
        return SwarmPlan.from_dict(plan_dict(**overrides))

    def test_valid_chain_reports_no_violations(self):
        st1 = Stage.from_dict(stage_dict(stage_id="st1"))
        st2 = Stage.from_dict(stage_dict(stage_id="st2", dependency_stage_refs=["st1"]))
        assert structural_violations(self._plan(), [st1, st2]) == []

    def test_empty_completion_condition_violation(self):
        st = Stage.from_dict(stage_dict(completion_condition={}))
        violations = structural_violations(self._plan(), [st])
        assert any("completion_condition" in v for v in violations)

    def test_unknown_dependency_reference(self):
        st = Stage.from_dict(stage_dict(dependency_stage_refs=["st_ghost"]))
        violations = structural_violations(self._plan(), [st])
        assert any("st_ghost" in v for v in violations)

    def test_cycle_detected(self):
        st1 = Stage.from_dict(stage_dict(stage_id="st1", dependency_stage_refs=["st2"]))
        st2 = Stage.from_dict(stage_dict(stage_id="st2", dependency_stage_refs=["st1"]))
        violations = structural_violations(self._plan(), [st1, st2])
        assert any("cycle" in v for v in violations)

    def test_depth_limit(self):
        plan = self._plan(max_depth=2)
        st1 = Stage.from_dict(stage_dict(stage_id="st1"))
        st2 = Stage.from_dict(stage_dict(stage_id="st2", dependency_stage_refs=["st1"]))
        st3 = Stage.from_dict(stage_dict(stage_id="st3", dependency_stage_refs=["st2"]))
        assert layer_metrics([st1, st2, st3]) == (3, 1)
        violations = structural_violations(plan, [st1, st2, st3])
        assert any("depth 3" in v for v in violations)

    def test_parallelism_limit(self):
        plan = self._plan(max_parallelism=2)
        stages = [Stage.from_dict(stage_dict(stage_id=f"st{i}")) for i in range(3)]
        assert layer_metrics(stages) == (1, 3)
        violations = structural_violations(plan, stages)
        assert any("parallel stage width 3" in v for v in violations)


class TestSwarmStoreSlice:
    def test_plan_idempotent_insert_and_shape_conflict(self):
        store = InMemoryMethodStore()
        plan = SwarmPlan.from_dict(plan_dict())
        assert store.insert_plan(AXES, plan, COLLET_ID) is plan
        assert store.insert_plan(AXES, plan, COLLET_ID) is plan
        with pytest.raises(ContextCollabValidationError, match="stored shape"):
            store.insert_plan(
                AXES,
                SwarmPlan.from_dict(plan_dict(max_parallelism=5)),
                COLLET_ID,
            )
        assert store.get_plan(AXES, "sp_001", "mb_swarm_001", COLLET_ID) is plan
        assert store.get_plan(AXES, "sp_999", "mb_swarm_001", COLLET_ID) is None

    def test_update_state_requires_existing_key(self):
        store = InMemoryMethodStore()
        plan = store.insert_plan(AXES, SwarmPlan.from_dict(plan_dict()), COLLET_ID)
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
                SwarmPlan.from_dict(plan_dict(swarm_plan_id="sp_ghost")),
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
            s.stage_id for s in store.list_stages(AXES, "mb_swarm_001", COLLET_ID)
        ] == ["st_001"]
        assert [
            s.stage_id for s in store.list_stages(AXES, "mb_swarm_002", COLLET_ID)
        ] == ["st_001"]
        allocation = StageAllocation.from_dict(allocation_dict())
        store.insert_allocation(AXES, "mb_swarm_001", allocation, COLLET_ID)
        with pytest.raises(ContextCollabValidationError, match="stored shape"):
            store.insert_allocation(
                AXES,
                "mb_swarm_001",
                StageAllocation.from_dict(allocation_dict(state="RELEASED")),
                COLLET_ID,
            )
        released = replace(allocation, state=AllocationState.from_wire("RELEASED"))
        store.update_allocation_state(AXES, "mb_swarm_001", released, COLLET_ID)
        with pytest.raises(ContextCollabNotFoundError):
            store.update_allocation_state(
                AXES,
                "mb_swarm_001",
                StageAllocation.from_dict(allocation_dict(allocation_id="alloc_ghost")),
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
            store.insert_execution_profile(AXES, "mb_swarm_001", profile, COLLET_ID)
            is profile
        )
        assert (
            store.get_execution_profile(AXES, "sep_001", "mb_swarm_001", COLLET_ID)
            is profile
        )
        record = SwarmExecutionRecord.from_dict(record_dict())
        assert (
            store.insert_execution_record(AXES, "mb_swarm_001", record, COLLET_ID)
            is record
        )
        assert [
            r.execution_record_id
            for r in store.list_execution_records(AXES, "mb_swarm_001", COLLET_ID)
        ] == ["exr_001"]
