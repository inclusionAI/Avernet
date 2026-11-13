"""Swarm Method lifecycle contracts; fixtures in swarm_profile_support."""

import pytest
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_profile import (
    AllocationState,
    ExecutionRecordState,
    PlanState,
    Stage,
    StageAllocation,
    StageState,
)
from agentclaw.community.core.task.context_collab.method_service_api import (
    SwarmMethodOpsServiceProtocol,
    SwarmPlanOpCommand,
    SwarmStageOutcomeCommand,
)

from tests.community.core.task.context_collab.swarm_profile_support import (
    AXES,
    COLLET_ID,
    _future_iso,
    _past_iso,
    active_swarm_binding,
    allocate_command,
    allocation_dict,
    make_actor,
    make_swarm_service,
    plan_command,
    profile_command,
    running_plan_harness,
    stage_dict,
)


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
        assert len(store.list_execution_profiles(AXES, binding, COLLET_ID)) == 1
        with pytest.raises(ContextCollabValidationError, match="stored shape"):
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
        assert [r.state for r in records] == [ExecutionRecordState.SUBMITTED]
        assert records[0].operation_ref == "swarm.submit_plan_candidate:sp_alpha"
        assert records[0].external_execution_id == records[0].operation_ref
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
        assert len(store.list_execution_records(AXES, binding, COLLET_ID)) == 1
        with pytest.raises(ContextCollabValidationError, match="stored shape"):
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
        service.submit_plan_candidate(COLLET_ID, binding, plan_command(), make_actor())
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
        report = service.validate_plan(COLLET_ID, binding, "sp_alpha", make_actor())
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
        with pytest.raises(ContextCollabValidationError, match="structural validation"):
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
        service.submit_plan_candidate(COLLET_ID, binding, plan_command(), make_actor())
        service.adopt_plan(
            COLLET_ID, binding, "sp_alpha", SwarmPlanOpCommand(), make_actor()
        )
        started = service.start_plan(
            COLLET_ID, binding, "sp_alpha", SwarmPlanOpCommand(), make_actor()
        )
        assert started.state == "RUNNING"
        states = {
            s.stage_id: s.state for s in store.list_stages(AXES, binding, COLLET_ID)
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
            COLLET_ID,
            binding,
            plan_id,
            SwarmPlanOpCommand(reason="恢复计划"),
            make_actor(),
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
        with pytest.raises(ContextCollabValidationError, match="RUNNING"):
            service.allocate_stage(COLLET_ID, binding, allocate_command(), make_actor())
        # 机器同样拒绝终态计划上的一切后继迁移。
        with pytest.raises(ContextCollabValidationError, match="does not accept event"):
            service.pause_plan(
                COLLET_ID, binding, plan_id, SwarmPlanOpCommand(), make_actor()
            )

    def test_illegal_plan_transitions_rejected_by_machine(self):
        _, _, lifecycle, service = make_swarm_service()
        binding = active_swarm_binding(lifecycle)
        service.submit_plan_candidate(COLLET_ID, binding, plan_command(), make_actor())
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
        with pytest.raises(ContextCollabValidationError, match="does not accept event"):
            service.adopt_plan(
                COLLET_ID,
                binding,
                "sp_alpha",
                SwarmPlanOpCommand(),
                make_actor(),
            )

    def test_missing_plan_not_found(self):
        _, _, _, service = make_swarm_service()
        with pytest.raises(ContextCollabNotFoundError):
            service.adopt_plan(
                COLLET_ID,
                "mb_ghost",
                "sp_none",
                SwarmPlanOpCommand(),
                make_actor(),
            )
        with pytest.raises(ContextCollabNotFoundError):
            service.validate_plan(COLLET_ID, "mb_ghost", "sp_none", make_actor())


class TestSwarmAllocateAndOutcome:
    def test_allocate_projects_active_allocation_and_running_stage(self):
        store, _, service, binding, plan_id = running_plan_harness()
        result = service.allocate_stage(
            COLLET_ID, binding, allocate_command(), make_actor()
        )
        assert result.accepted is True
        assert result.object_kind == "swarm_allocation"
        assert result.state == "ACTIVE"
        allocation = store.get_allocation(AXES, "alloc_1", binding, COLLET_ID)
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
            COLLET_ID,
            binding,
            allocate_command(allocation_id="alloc_x", lease_expires_at=lease),
            make_actor(),
        )
        replay = service.allocate_stage(
            COLLET_ID,
            binding,
            allocate_command(allocation_id="alloc_x", lease_expires_at=lease),
            make_actor(),
        )
        assert replay.object_id == first.object_id == "alloc_x"
        assert len(store.list_allocations(AXES, binding, COLLET_ID)) == 1
        with pytest.raises(ContextCollabValidationError, match="stored shape"):
            service.allocate_stage(
                COLLET_ID,
                binding,
                allocate_command(allocation_id="alloc_x", target_ref="agent://other"),
                make_actor(),
            )

    def test_allocate_requires_running_plan(self):
        _, _, lifecycle, service = make_swarm_service()
        binding = active_swarm_binding(lifecycle)
        service.declare_execution_profile(
            COLLET_ID, binding, profile_command(), make_actor()
        )
        service.submit_plan_candidate(COLLET_ID, binding, plan_command(), make_actor())
        with pytest.raises(ContextCollabValidationError, match="RUNNING"):
            service.allocate_stage(COLLET_ID, binding, allocate_command(), make_actor())

    def test_allocate_requires_declared_profile(self):
        _, _, service, binding, plan_id = running_plan_harness()
        with pytest.raises(ContextCollabValidationError, match="not declared"):
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
        assert store.get_plan(AXES, plan_id, binding, COLLET_ID).max_parallelism == 2
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
        with pytest.raises(ContextCollabValidationError, match="ACTIVE allocations"):
            service.allocate_stage(COLLET_ID, binding, allocate_command(), make_actor())

    def test_complete_releases_allocations_and_succeeds_stage(self):
        store, _, service, binding, plan_id = running_plan_harness()
        service.allocate_stage(COLLET_ID, binding, allocate_command(), make_actor())
        completed = service.complete_stage(
            COLLET_ID, binding, "st1", SwarmStageOutcomeCommand(), make_actor()
        )
        assert completed.accepted is True
        assert completed.object_kind == "swarm_stage"
        assert completed.state == "SUCCEEDED"
        stage = store.get_stage(AXES, "st1", binding, COLLET_ID)
        assert stage.state is StageState.SUCCEEDED
        assert stage.transitioned_from == "RUNNING"
        allocation = store.get_allocation(AXES, "alloc_1", binding, COLLET_ID)
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
        with pytest.raises(ContextCollabValidationError, match="lease expired"):
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
        with pytest.raises(ContextCollabValidationError, match="only ACTIVE"):
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
        with pytest.raises(ContextCollabValidationError, match="completion_condition"):
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
        with pytest.raises(ContextCollabValidationError, match="does not accept event"):
            service.fail_stage(
                COLLET_ID,
                binding,
                "st1",
                SwarmStageOutcomeCommand(),
                make_actor(),
            )
        service.allocate_stage(COLLET_ID, binding, allocate_command(), make_actor())
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
                COLLET_ID,
                binding,
                "st_ghost",
                SwarmStageOutcomeCommand(),
                make_actor(),
            ),
            lambda: service.fail_stage(
                COLLET_ID,
                binding,
                "st_ghost",
                SwarmStageOutcomeCommand(),
                make_actor(),
            ),
            lambda: service.allocate_stage(
                COLLET_ID,
                binding,
                allocate_command(stage_ref="st_ghost"),
                make_actor(),
            ),
        ):
            with pytest.raises(ContextCollabNotFoundError):
                op_call()


class TestSwarmProtocolConformance:
    def test_service_satisfies_swarm_protocol(self):
        """``SwarmMethodService`` 满足 ``SwarmMethodOpsServiceProtocol``
        的 runtime_checkable isinstance 面。"""
        _, _, _, service = make_swarm_service()
        assert isinstance(service, SwarmMethodOpsServiceProtocol)
