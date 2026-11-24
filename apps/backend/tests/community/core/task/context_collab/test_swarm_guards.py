"""Swarm Method guards contracts; fixtures in swarm_profile_support."""

import pytest
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.method_authorization import (
    SWARM_OWNER_OPS,
    SWARM_READ_OPS,
)
from agentclaw.community.core.task.context_collab.method_service_api import (
    BindMethodCommand,
    MethodStateChangeCommand,
    SwarmPlanOpCommand,
    SwarmStageOutcomeCommand,
)

from tests.community.core.task.context_collab.swarm_profile_support import (
    COLLET_ID,
    OWNER_OPS_ALL,
    active_swarm_binding,
    allocate_command,
    make_actor,
    make_swarm_service,
    plan_command,
    profile_command,
    running_plan_harness,
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
                COLLET_ID,
                bound,
                "st1",
                SwarmStageOutcomeCommand(),
                make_actor(),
            ),
            lambda: service.fail_stage(
                COLLET_ID,
                bound,
                "st1",
                SwarmStageOutcomeCommand(),
                make_actor(),
            ),
        ]
        for op_call in callables:
            with pytest.raises(ContextCollabValidationError, match="ACTIVE"):
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
        with pytest.raises(ContextCollabValidationError, match="declare Swarm"):
            service.declare_execution_profile(
                COLLET_ID, group_binding, profile_command(), make_actor()
            )
        with pytest.raises(ContextCollabValidationError, match="declare Swarm"):
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
            service.submit_plan_candidate(COLLET_ID, binding, plan_command(), stranger)
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
            service2.allocate_stage(COLLET_ID, binding2, allocate_command(), stranger)
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
        service.submit_plan_candidate(COLLET_ID, binding, plan_command(), make_actor())
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
