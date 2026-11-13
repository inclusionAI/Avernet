"""Open Method guards contracts; fixtures in open_profile_support."""

import pytest
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.method_authorization import (
    OPEN_OWNER_OPS,
    OPEN_PUBLIC_OPS,
    OPEN_SELF_HELP_OPS,
)
from agentclaw.community.core.task.context_collab.method_service_api import (
    BindMethodCommand,
    MethodStateChangeCommand,
    OpenAdmitCommand,
    OpenDecisionOpCommand,
    OpenOpportunityOpCommand,
)

from tests.community.core.task.context_collab.open_profile_support import (
    COLLET_ID,
    OWNER_OPS_ALL,
    active_open_binding,
    application_command,
    make_actor,
    make_open_service,
    opportunity_command,
)


class TestOpenGuards:
    def test_rf2_non_active_binding_rejected(self):
        """RF#2:binding 非 ACTIVE 时副作用操作一律拒绝(公开提交
        submit_application 同为副作用面)。"""
        _, _, lifecycle, service = make_open_service()
        lifecycle.bind(
            COLLET_ID,
            BindMethodCommand(
                method_kind="Open",
                method_binding_id="mb_open_bound",
                parent_scope_ref="task_scope_root",
            ),
            make_actor(),
        )
        bound = "mb_open_bound"
        callables = [
            lambda: service.publish_opportunity(
                COLLET_ID, bound, opportunity_command(), make_actor()
            ),
            lambda: service.pause_opportunity(
                COLLET_ID,
                bound,
                "opp_x",
                OpenOpportunityOpCommand(),
                make_actor(),
            ),
            lambda: service.reopen_opportunity(
                COLLET_ID,
                bound,
                "opp_x",
                OpenOpportunityOpCommand(),
                make_actor(),
            ),
            lambda: service.close_opportunity(
                COLLET_ID,
                bound,
                "opp_x",
                OpenOpportunityOpCommand(),
                make_actor(),
            ),
            lambda: service.submit_application(
                COLLET_ID, bound, "opp_x", application_command(), make_actor()
            ),
            lambda: service.screen(
                COLLET_ID,
                bound,
                "app_x",
                OpenDecisionOpCommand(),
                make_actor(),
            ),
            lambda: service.reject(
                COLLET_ID,
                bound,
                "app_x",
                OpenDecisionOpCommand(),
                make_actor(),
            ),
            lambda: service.withdraw(
                COLLET_ID,
                bound,
                "app_x",
                OpenDecisionOpCommand(),
                make_actor("u_member_001"),
            ),
            lambda: service.admit(
                COLLET_ID, bound, "app_x", OpenAdmitCommand(), make_actor()
            ),
            lambda: service.revoke_admission(
                COLLET_ID,
                bound,
                "app_x",
                OpenDecisionOpCommand(),
                make_actor(),
            ),
        ]
        for op_call in callables:
            with pytest.raises(ContextCollabValidationError, match="ACTIVE"):
                op_call()

    def test_open_ops_reject_non_open_binding(self):
        """结构守卫:Group binding 上的 Open 剖面操作是结构漂移,拒绝。"""
        _, _, lifecycle, service = make_open_service()
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
        with pytest.raises(ContextCollabValidationError, match="declare Open"):
            service.publish_opportunity(
                COLLET_ID, group_binding, opportunity_command(), make_actor()
            )

    def test_owner_ops_reject_stranger(self):
        """T6:八个 owner 操作不放行陌生人(frozenset pin + 行为 pin);
        submit 是公开面;withdraw 是 self-help。"""
        assert OPEN_OWNER_OPS == frozenset(OWNER_OPS_ALL)
        assert OPEN_PUBLIC_OPS == frozenset({"submit_application"})
        assert OPEN_SELF_HELP_OPS == frozenset({"withdraw"})
        store, _, lifecycle, service = make_open_service()
        binding = active_open_binding(lifecycle)
        stranger = make_actor("u_9999")
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service.publish_opportunity(
                COLLET_ID, binding, opportunity_command(), stranger
            )
        service.publish_opportunity(
            COLLET_ID, binding, opportunity_command(), make_actor()
        )
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service.pause_opportunity(
                COLLET_ID,
                binding,
                "opp_alpha",
                OpenOpportunityOpCommand(),
                stranger,
            )
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service.close_opportunity(
                COLLET_ID,
                binding,
                "opp_alpha",
                OpenOpportunityOpCommand(),
                stranger,
            )
        service.submit_application(
            COLLET_ID,
            binding,
            "opp_alpha",
            application_command(),
            make_actor(),
        )
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service.screen(
                COLLET_ID, binding, "app_1", OpenDecisionOpCommand(), stranger
            )
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service.reject(
                COLLET_ID, binding, "app_1", OpenDecisionOpCommand(), stranger
            )
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service.admit(COLLET_ID, binding, "app_1", OpenAdmitCommand(), stranger)
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service.revoke_admission(
                COLLET_ID, binding, "app_1", OpenDecisionOpCommand(), stranger
            )
        # 公开面不拒陌生人(submit 已在上方覆盖)。

    def test_missing_binding_and_opportunity_not_found(self):
        _, _, _, service = make_open_service()
        with pytest.raises(ContextCollabNotFoundError):
            service.publish_opportunity(
                COLLET_ID, "mb_ghost", opportunity_command(), make_actor()
            )
        with pytest.raises(ContextCollabNotFoundError):
            service.pause_opportunity(
                COLLET_ID,
                "mb_ghost",
                "opp_none",
                OpenOpportunityOpCommand(),
                make_actor(),
            )
        with pytest.raises(ContextCollabNotFoundError):
            service.submit_application(
                COLLET_ID,
                "mb_ghost",
                "opp_none",
                application_command(),
                make_actor(),
            )
