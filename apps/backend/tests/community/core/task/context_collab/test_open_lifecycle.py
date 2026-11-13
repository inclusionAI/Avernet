"""Open Method lifecycle contracts; fixtures in open_profile_support."""

from dataclasses import replace

import pytest
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l5_methods.group_profile import (
    MemberState,
)
from agentclaw.community.core.task.context_collab.l5_methods.open_profile import (
    OpportunityState,
    ParticipationType,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_profile import (
    AllocationState,
    StageState,
)
from agentclaw.community.core.task.context_collab.method_service_api import (
    OpenAdmitCommand,
    OpenDecisionOpCommand,
    OpenMethodOpsServiceProtocol,
    OpenOpportunityOpCommand,
)

from tests.community.core.task.context_collab.open_profile_support import (
    AXES,
    COLLET_ID,
    PRINCIPAL_ID,
    _iso_offset,
    active_open_binding,
    application_command,
    make_actor,
    make_open_service,
    make_ready_stage,
    opportunity_command,
    screening_application,
    submitted_application,
)


class TestOpenOpportunityLifecycle:
    def test_publish_projects_open_opportunity(self):
        store, _, lifecycle, service = make_open_service()
        binding = active_open_binding(lifecycle)
        result = service.publish_opportunity(
            COLLET_ID, binding, opportunity_command(), make_actor()
        )
        assert result.accepted is True
        assert result.object_kind == "open_opportunity"
        assert result.state == "OPEN"
        opportunity = store.get_opportunity(AXES, "opp_alpha", binding, COLLET_ID)
        assert opportunity.state is OpportunityState.OPEN
        assert opportunity.transitioned_from == "DRAFT"
        assert opportunity.participation_type is (ParticipationType.GROUP_MEMBER)

    def test_publish_window_shape_validation(self):
        _, _, lifecycle, service = make_open_service()
        binding = active_open_binding(lifecycle)
        with pytest.raises(ContextCollabValidationError, match="ISO timestamp"):
            service.publish_opportunity(
                COLLET_ID,
                binding,
                opportunity_command(opens_at="soon"),
                make_actor(),
            )
        with pytest.raises(ContextCollabValidationError, match="later than opens_at"):
            service.publish_opportunity(
                COLLET_ID,
                binding,
                opportunity_command(opens_at=_iso_offset(1), closes_at=_iso_offset(-1)),
                make_actor(),
            )

    def test_publish_replay_and_shape_conflict(self):
        store, _, lifecycle, service = make_open_service()
        binding = active_open_binding(lifecycle)
        # 同一份命令两次提交(时间戳字段是请求形态,重放必须同值)。
        command = opportunity_command()
        first = service.publish_opportunity(COLLET_ID, binding, command, make_actor())
        replay = service.publish_opportunity(COLLET_ID, binding, command, make_actor())
        assert replay.object_id == first.object_id
        assert len(store.list_opportunities(AXES, binding, COLLET_ID)) == 1
        with pytest.raises(ContextCollabValidationError, match="stored shape"):
            service.publish_opportunity(
                COLLET_ID,
                binding,
                opportunity_command(scope_ref="task_scope_other"),
                make_actor(),
            )

    def test_pause_reopen_close_reachability(self):
        """作者裁决补漏:ReopenOpportunity(PAUSED→OPEN)必须可达;
        CloseOpportunity 是终态,后继迁移由机器拒绝。"""
        store, _, lifecycle, service = make_open_service()
        binding = active_open_binding(lifecycle)
        service.publish_opportunity(
            COLLET_ID, binding, opportunity_command(), make_actor()
        )
        paused = service.pause_opportunity(
            COLLET_ID,
            binding,
            "opp_alpha",
            OpenOpportunityOpCommand(),
            make_actor(),
        )
        assert paused.state == "PAUSED"
        reopened = service.reopen_opportunity(
            COLLET_ID,
            binding,
            "opp_alpha",
            OpenOpportunityOpCommand(reason="重新开放"),
            make_actor(),
        )
        assert reopened.state == "OPEN"
        opportunity = store.get_opportunity(AXES, "opp_alpha", binding, COLLET_ID)
        assert opportunity.state is OpportunityState.OPEN
        assert opportunity.transition_reason == "重新开放"
        closed = service.close_opportunity(
            COLLET_ID,
            binding,
            "opp_alpha",
            OpenOpportunityOpCommand(),
            make_actor(),
        )
        assert closed.state == "CLOSED"
        with pytest.raises(ContextCollabValidationError, match="does not accept event"):
            service.reopen_opportunity(
                COLLET_ID,
                binding,
                "opp_alpha",
                OpenOpportunityOpCommand(),
                make_actor(),
            )
        # 未发布(无机会行)的 close 走 NotFound。
        with pytest.raises(ContextCollabNotFoundError):
            service.close_opportunity(
                COLLET_ID,
                binding,
                "opp_ghost",
                OpenOpportunityOpCommand(),
                make_actor(),
            )

    def test_submit_application_public_plane_window_and_capacity(self):
        """submit_application 是公开面:任意已认证 actor 可投;机会必须
        OPEN、在窗口内、容量未满。"""
        store, _, lifecycle, service = make_open_service()
        binding = active_open_binding(lifecycle)
        service.publish_opportunity(
            COLLET_ID, binding, opportunity_command(), make_actor()
        )
        submitted = service.submit_application(
            COLLET_ID,
            binding,
            "opp_alpha",
            application_command(),
            make_actor("u_stranger_001"),
        )
        assert submitted.accepted is True
        assert submitted.object_id == "app_1"
        assert submitted.state == "SUBMITTED"
        application = store.get_application(AXES, "app_1", binding, COLLET_ID)
        assert application.opportunity_ref == "opp_alpha"
        assert application.accountable_principal_ref == "u_member_001"
        assert application.transitioned_from == ""
        # 幂等重放:同 ID 同形态返回既有对象,不新增行。
        replay = service.submit_application(
            COLLET_ID,
            binding,
            "opp_alpha",
            application_command(),
            make_actor("u_stranger_002"),
        )
        assert replay.object_id == "app_1"
        assert len(store.list_applications(AXES, binding, COLLET_ID)) == 1
        # 未开窗(未来 opens_at)拒绝。
        service.publish_opportunity(
            COLLET_ID,
            binding,
            opportunity_command(opportunity_id="opp_future", opens_at=_iso_offset(2)),
            make_actor(),
        )
        with pytest.raises(
            ContextCollabValidationError, match="before the window opens"
        ):
            service.submit_application(
                COLLET_ID,
                binding,
                "opp_future",
                application_command(),
                make_actor(),
            )
        # 已闭窗拒绝。
        service.publish_opportunity(
            COLLET_ID,
            binding,
            opportunity_command(
                opportunity_id="opp_closed",
                opens_at=_iso_offset(-2),
                closes_at=_iso_offset(-1),
            ),
            make_actor(),
        )
        with pytest.raises(
            ContextCollabValidationError, match="after the window closes"
        ):
            service.submit_application(
                COLLET_ID,
                binding,
                "opp_closed",
                application_command(),
                make_actor(),
            )
        # 容量:capacity=1,两个申请人,第二个拒绝。
        service.publish_opportunity(
            COLLET_ID,
            binding,
            opportunity_command(opportunity_id="opp_capped", capacity=1),
            make_actor(),
        )
        service.submit_application(
            COLLET_ID,
            binding,
            "opp_capped",
            application_command(applicant_agent_ref="bot_a", application_id="app_ca"),
            make_actor(),
        )
        with pytest.raises(ContextCollabValidationError, match="capacity"):
            service.submit_application(
                COLLET_ID,
                binding,
                "opp_capped",
                application_command(
                    applicant_agent_ref="bot_b", application_id="app_cb"
                ),
                make_actor(),
            )

    def test_submit_requires_open_opportunity_and_unique_applicant(self):
        _, _, lifecycle, service = make_open_service()
        binding = active_open_binding(lifecycle)
        service.publish_opportunity(
            COLLET_ID, binding, opportunity_command(), make_actor()
        )
        service.pause_opportunity(
            COLLET_ID,
            binding,
            "opp_alpha",
            OpenOpportunityOpCommand(),
            make_actor(),
        )
        with pytest.raises(
            ContextCollabValidationError, match="only be submitted while"
        ):
            service.submit_application(
                COLLET_ID,
                binding,
                "opp_alpha",
                application_command(),
                make_actor(),
            )
        service.reopen_opportunity(
            COLLET_ID,
            binding,
            "opp_alpha",
            OpenOpportunityOpCommand(),
            make_actor(),
        )
        service.submit_application(
            COLLET_ID,
            binding,
            "opp_alpha",
            application_command(),
            make_actor(),
        )
        with pytest.raises(
            ContextCollabValidationError, match="non-terminal application"
        ):
            service.submit_application(
                COLLET_ID,
                binding,
                "opp_alpha",
                application_command(application_id="app_dup"),
                make_actor(),
            )


class TestOpenApplicationLifecycle:
    def test_screen_then_reject_both_rules(self):
        store, _, lifecycle, service = make_open_service()
        binding = active_open_binding(lifecycle)
        service.publish_opportunity(
            COLLET_ID, binding, opportunity_command(), make_actor()
        )
        service.submit_application(
            COLLET_ID,
            binding,
            "opp_alpha",
            application_command(),
            make_actor(),
        )
        screened = service.screen(
            COLLET_ID, binding, "app_1", OpenDecisionOpCommand(), make_actor()
        )
        assert screened.state == "SCREENING"
        rejected = service.reject(
            COLLET_ID,
            binding,
            "app_1",
            OpenDecisionOpCommand(reason="不合条件"),
            make_actor(),
        )
        assert rejected.state == "REJECTED"
        application = store.get_application(AXES, "app_1", binding, COLLET_ID)
        assert application.transitioned_from == "SCREENING"
        assert application.transition_reason == "不合条件"
        # reject_r1:未经评审直接拒。
        service.submit_application(
            COLLET_ID,
            binding,
            "opp_alpha",
            application_command(application_id="app_r1", applicant_agent_ref="bot_x"),
            make_actor(),
        )
        directly = service.reject(
            COLLET_ID, binding, "app_r1", OpenDecisionOpCommand(), make_actor()
        )
        assert directly.state == "REJECTED"

    def test_withdraw_is_self_help_only(self):
        """withdraw 是责任主体本人自助:与目标行 accountable_principal_ref
        字符串相等才放行;机器仅 SUBMITTED 可退(SCREENING 后不可自助退)。"""
        _, _, lifecycle, service = make_open_service()
        binding = active_open_binding(lifecycle)
        service.publish_opportunity(
            COLLET_ID, binding, opportunity_command(), make_actor()
        )
        service.submit_application(
            COLLET_ID,
            binding,
            "opp_alpha",
            application_command(),
            make_actor(),
        )
        # 陌生人拒绝;owner(PRINCIPAL_ID ≠ u_member_001)也拒绝。
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service.withdraw(
                COLLET_ID,
                binding,
                "app_1",
                OpenDecisionOpCommand(),
                make_actor("u_9999"),
            )
        with pytest.raises(ContextCollabValidationError, match=PRINCIPAL_ID):
            service.withdraw(
                COLLET_ID,
                binding,
                "app_1",
                OpenDecisionOpCommand(),
                make_actor(PRINCIPAL_ID),
            )
        withdrawn = service.withdraw(
            COLLET_ID,
            binding,
            "app_1",
            OpenDecisionOpCommand(),
            make_actor("u_member_001"),
        )
        assert withdrawn.state == "WITHDRAWN"
        # SCREENING 后机器无 withdraw 出边。
        service.submit_application(
            COLLET_ID,
            binding,
            "opp_alpha",
            application_command(application_id="app_s", applicant_agent_ref="bot_y"),
            make_actor(),
        )
        service.screen(
            COLLET_ID, binding, "app_s", OpenDecisionOpCommand(), make_actor()
        )
        with pytest.raises(ContextCollabValidationError, match="does not accept event"):
            service.withdraw(
                COLLET_ID,
                binding,
                "app_s",
                OpenDecisionOpCommand(),
                make_actor("u_member_001"),
            )

    def test_admit_group_member_product(self):
        """GROUP_MEMBER 录取:落 ACTIVE 成员行(roster 惰性建) +
        AdmissionDecision 产物引用。"""
        store, _, lifecycle, service = make_open_service()
        opp, app = screening_application(service, lifecycle)
        binding = "mb_open_alpha"
        admitted = service.admit(
            COLLET_ID,
            binding,
            app,
            OpenAdmitCommand(admitted_scope_ref="scope://admitted"),
            make_actor(),
        )
        assert admitted.state == "ADMITTED"
        members = store.list_members(AXES, binding, COLLET_ID)
        assert len(members) == 1
        assert members[0].state is MemberState.ACTIVE
        assert members[0].participant_ref == "bot_worker_001"
        assert members[0].joined_at
        assert (
            store.get_roster(AXES, binding, COLLET_ID).roster_id == f"roster_{binding}"
        )
        decision = store.list_decisions(AXES, binding, COLLET_ID)[0]
        assert decision.admitted_scope_ref == "scope://admitted"
        assert decision.resulting_binding_refs == (members[0].group_member_binding_id,)
        # G1 断言:录取不产生新 binding / selection / composition edge
        # (admitted ≠ Space Membership 的 service 面投影)。
        assert len(store.list_bindings(AXES, COLLET_ID)) == 1  # 仅既有 Open binding
        assert store.list_selections(AXES, binding, COLLET_ID) == []
        assert store.list_composition_edges(AXES, COLLET_ID) == []

    def test_admit_swarm_allocation_requires_target_stage(self):
        """SWARM_ALLOCATION 录取:target_stage_ref 必填且必须指向本
        binding 作用域内计划的 READY/RUNNING Stage;缺省/越域/非法状态
        一律拒绝,通过后落 ACTIVE 语义分配。"""
        store, _, lifecycle, service = make_open_service()
        binding = "mb_open_alpha"  # screening_application 负责激活
        opp, app = screening_application(
            service,
            lifecycle,
            binding,
            participation_type="SWARM_ALLOCATION",
        )
        # 缺省 target_stage_ref 拒。
        with pytest.raises(ContextCollabValidationError, match="target_stage_ref"):
            service.admit(COLLET_ID, binding, app, OpenAdmitCommand(), make_actor())
        # 域内不存在的 Stage 拒。
        with pytest.raises(
            ContextCollabValidationError, match="not found in the scope"
        ):
            service.admit(
                COLLET_ID,
                binding,
                app,
                OpenAdmitCommand(target_stage_ref="st_ghost"),
                make_actor(),
            )
        # 直落一条 PENDING(未 READY)Stage 拒——谓 READY/RUNNING。
        store.insert_stage(
            AXES,
            binding,
            replace(
                make_ready_stage("st_pending"),
                state=StageState.from_wire("PENDING"),
            ),
            COLLET_ID,
        )
        with pytest.raises(ContextCollabValidationError, match="PENDING"):
            service.admit(
                COLLET_ID,
                binding,
                app,
                OpenAdmitCommand(target_stage_ref="st_pending"),
                make_actor(),
            )
        # READY Stage 放行:落 ACTIVE 语义分配,能力证据承接申请。
        store.insert_stage(AXES, binding, make_ready_stage("st_ready"), COLLET_ID)
        admitted = service.admit(
            COLLET_ID,
            binding,
            app,
            OpenAdmitCommand(target_stage_ref="st_ready"),
            make_actor(),
        )
        assert admitted.state == "ADMITTED"
        decisions = store.list_decisions(AXES, binding, COLLET_ID)
        allocation_id = decisions[0].resulting_binding_refs[0]
        allocation = store.get_allocation(AXES, allocation_id, binding, COLLET_ID)
        assert allocation.state is AllocationState.ACTIVE
        assert allocation.stage_ref == "st_ready"
        assert allocation.target_ref == "bot_worker_001"
        assert allocation.qualification_evidence_refs == ("evidence://skill",)

    def test_admit_single_contribution_records_refs_only(self):
        store, _, lifecycle, service = make_open_service()
        opp, app = screening_application(
            service, lifecycle, participation_type="SINGLE_CONTRIBUTION"
        )
        admitted = service.admit(
            COLLET_ID,
            "mb_open_alpha",
            app,
            OpenAdmitCommand(admitted_scope_ref="scope://draft"),
            make_actor(),
        )
        assert admitted.state == "ADMITTED"
        binding = "mb_open_alpha"
        decision = store.list_decisions(AXES, binding, COLLET_ID)[0]
        assert decision.resulting_binding_refs == ("artifact://draft",)
        # 不产生成员行与分配。
        assert store.list_members(AXES, binding, COLLET_ID) == []
        assert store.list_allocations(AXES, binding, COLLET_ID) == []

    def test_admit_collet_participant_is_p1_rejected(self):
        _, _, lifecycle, service = make_open_service()
        binding = active_open_binding(lifecycle)
        with pytest.raises(
            ContextCollabValidationError,
            match="COLLET_PARTICIPANT",
        ):
            service.publish_opportunity(
                COLLET_ID,
                binding,
                opportunity_command(participation_type="COLLET_PARTICIPANT"),
                make_actor(),
            )

    def test_admit_requires_screening_state_and_is_idempotent(self):
        """机器裁决:未评审(SCREENING 前)不可录取;同 decision_id 重放
        产物不重建。"""
        store, _, lifecycle, service = make_open_service()
        binding = active_open_binding(lifecycle)
        service.publish_opportunity(
            COLLET_ID,
            binding,
            opportunity_command(participation_type="GROUP_MEMBER"),
            make_actor(),
        )
        submitted_application(service, "opp_alpha", binding)
        with pytest.raises(ContextCollabValidationError, match="does not accept event"):
            service.admit(COLLET_ID, binding, "app_1", OpenAdmitCommand(), make_actor())
        service.screen(
            COLLET_ID, binding, "app_1", OpenDecisionOpCommand(), make_actor()
        )
        first = service.admit(
            COLLET_ID,
            binding,
            "app_1",
            OpenAdmitCommand(decision_id="dec_1"),
            make_actor(),
        )
        assert first.state == "ADMITTED"
        replay = service.admit(
            COLLET_ID,
            binding,
            "app_1",
            OpenAdmitCommand(decision_id="dec_1"),
            make_actor(),
        )
        assert replay.state == "ADMITTED"
        # 产物不重建:成员行恰一条、decision 恰一条。
        assert len(store.list_members(AXES, binding, COLLET_ID)) == 1
        assert len(store.list_decisions(AXES, binding, COLLET_ID)) == 1
        with pytest.raises(ContextCollabValidationError, match="stored shape"):
            service.admit(
                COLLET_ID,
                binding,
                "app_1",
                OpenAdmitCommand(
                    decision_id="dec_1", admitted_scope_ref="scope://other"
                ),
                make_actor(),
            )

    def test_revoke_admission_terminates_products(self):
        """revoke_admission:成员 → REMOVED、分配 → REVOKED;
        SINGLE_CONTRIBUTION 无产物可终结;申请经机器 ADMITTED→REVOKED。"""
        # GROUP_MEMBER 产物终结。
        store, _, lifecycle, service = make_open_service()
        opp, app = screening_application(
            service, lifecycle, participation_type="GROUP_MEMBER"
        )
        binding = "mb_open_alpha"
        service.admit(COLLET_ID, binding, app, OpenAdmitCommand(), make_actor())
        member_id = store.list_members(AXES, binding, COLLET_ID)[
            0
        ].group_member_binding_id
        revoked = service.revoke_admission(
            COLLET_ID, binding, app, OpenDecisionOpCommand(), make_actor()
        )
        assert revoked.state == "REVOKED"
        member = next(
            m
            for m in store.list_members(AXES, binding, COLLET_ID)
            if m.group_member_binding_id == member_id
        )
        assert member.state is MemberState.REMOVED
        assert member.left_at
        assert member.transitioned_from == "ACTIVE"
        # SWARM_ALLOCATION 产物终结:语义分配 → REVOKED。
        store2, _, lifecycle2, service2 = make_open_service()
        binding2 = "mb_open_alpha"  # screening_application 负责激活
        opp2, app2 = screening_application(
            service2,
            lifecycle2,
            binding2,
            participation_type="SWARM_ALLOCATION",
        )
        store2.insert_stage(
            AXES,
            binding2,
            make_ready_stage("st_ready", binding2),
            COLLET_ID,
        )
        service2.admit(
            COLLET_ID,
            binding2,
            app2,
            OpenAdmitCommand(target_stage_ref="st_ready"),
            make_actor(),
        )
        allocation_id = store2.list_decisions(AXES, binding2, COLLET_ID)[
            0
        ].resulting_binding_refs[0]
        revoked2 = service2.revoke_admission(
            COLLET_ID, binding2, app2, OpenDecisionOpCommand(), make_actor()
        )
        assert revoked2.state == "REVOKED"
        allocation = store2.get_allocation(AXES, allocation_id, binding2, COLLET_ID)
        assert allocation.state is AllocationState.REVOKED
        assert allocation.transitioned_from == "ACTIVE"


class TestOpenProtocolConformance:
    def test_service_satisfies_open_protocol(self):
        """``OpenMethodService`` 满足 ``OpenMethodOpsServiceProtocol``
        的 runtime_checkable isinstance 面。"""
        _, _, _, service = make_open_service()
        assert isinstance(service, OpenMethodOpsServiceProtocol)
