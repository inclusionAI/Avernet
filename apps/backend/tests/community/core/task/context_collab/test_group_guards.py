"""Group Method guards contracts; fixtures in group_profile_support."""

import pytest
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.method_authorization import (
    GROUP_OWNER_OPS,
    GROUP_SELF_HELP_OPS,
)
from agentclaw.community.core.task.context_collab.method_service_api import (
    BindMethodCommand,
    GroupChangeRoleCommand,
    GroupReplaceMemberCommand,
)

from tests.community.core.task.context_collab.group_profile_support import (
    AXES,
    COLLET_ID,
    OWNER_OPS_ALL,
    PRINCIPAL_ID,
    active_group_binding,
    make_actor,
    make_group_service,
    member_command,
)


class TestGroupGuards:
    def test_invite_rejects_non_terminal_duplicate_but_allows_returning(self):
        _, _, lifecycle, service = make_group_service()
        binding_id = active_group_binding(lifecycle)
        service.invite(
            COLLET_ID,
            binding_id,
            member_command(participant_ref="bot_dup_001"),
            make_actor(),
        )
        with pytest.raises(ContextCollabValidationError, match="non-terminal"):
            service.invite(
                COLLET_ID,
                binding_id,
                member_command(participant_ref="bot_dup_001"),
                make_actor(),
            )
        # 走完生命周期(add→leave 终态)后可重新 invite。
        service.add_member(
            COLLET_ID,
            binding_id,
            member_command(participant_ref="bot_dup_001"),
            make_actor(),
        )
        service.leave(
            COLLET_ID,
            binding_id,
            member_command(participant_ref="bot_dup_001"),
            make_actor("bot_dup_001"),
        )
        reinvited = service.invite(
            COLLET_ID,
            binding_id,
            member_command(participant_ref="bot_dup_001"),
            make_actor(),
        )
        assert reinvited.state == "INVITED"

    def test_group_ops_reject_non_active_binding(self):
        """RF#2:binding 非 ACTIVE 时八个副作用操作一律拒绝。"""
        _, repo, lifecycle, service = make_group_service()
        lifecycle.bind(
            COLLET_ID,
            BindMethodCommand(
                method_kind="Group",
                method_binding_id="mb_group_bound",
                parent_scope_ref="task_scope_root",
            ),
            make_actor(),
        )
        callables = [
            lambda: service.invite(
                COLLET_ID, "mb_group_bound", member_command(), make_actor()
            ),
            lambda: service.add_member(
                COLLET_ID, "mb_group_bound", member_command(), make_actor()
            ),
            lambda: service.suspend_member(
                COLLET_ID, "mb_group_bound", member_command(), make_actor()
            ),
            lambda: service.resume_member(
                COLLET_ID, "mb_group_bound", member_command(), make_actor()
            ),
            lambda: service.leave(
                COLLET_ID, "mb_group_bound", member_command(), make_actor()
            ),
            lambda: service.remove_member(
                COLLET_ID, "mb_group_bound", member_command(), make_actor()
            ),
            lambda: service.replace_member(
                COLLET_ID,
                "mb_group_bound",
                GroupReplaceMemberCommand(
                    old_participant_ref="bot_a", new_participant_ref="bot_b"
                ),
                make_actor(),
            ),
            lambda: service.change_role(
                COLLET_ID,
                "mb_group_bound",
                GroupChangeRoleCommand(
                    participant_ref="bot_a",
                    role_binding_refs=["role_binding_x"],
                    expected_revision=1,
                ),
                make_actor(),
            ),
        ]
        for op_call in callables:
            with pytest.raises(ContextCollabValidationError, match="ACTIVE"):
                op_call()

    def test_owner_ops_reject_stranger(self):
        """T6:7 个 owner 操作 invite/add/suspend/resume/remove/replace/
        change_role 不放行陌生人(frozenset pin + 行为 pin)。"""
        assert GROUP_OWNER_OPS == frozenset(OWNER_OPS_ALL)
        assert GROUP_SELF_HELP_OPS == frozenset({"leave"})
        store, _, lifecycle, service = make_group_service()
        binding_id = active_group_binding(lifecycle)
        # 造在册成员:u_member_001 ACTIVE、u_member_002 SUSPENDED。
        for participant in ("u_member_001", "u_member_002"):
            service.invite(
                COLLET_ID,
                binding_id,
                member_command(participant_ref=participant),
                make_actor(),
            )
            service.add_member(
                COLLET_ID,
                binding_id,
                member_command(participant_ref=participant),
                make_actor(),
            )
        service.suspend_member(
            COLLET_ID,
            binding_id,
            member_command(participant_ref="u_member_002"),
            make_actor(),
        )
        stranger = make_actor("u_9999")
        revision = store.get_roster(AXES, binding_id, COLLET_ID).revision
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service.invite(
                COLLET_ID,
                binding_id,
                member_command(participant_ref="bot_new"),
                stranger,
            )
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service.add_member(
                COLLET_ID,
                binding_id,
                member_command(participant_ref="u_member_001"),
                stranger,
            )
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service.suspend_member(
                COLLET_ID,
                binding_id,
                member_command(participant_ref="u_member_001"),
                stranger,
            )
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service.resume_member(
                COLLET_ID,
                binding_id,
                member_command(participant_ref="u_member_002"),
                stranger,
            )
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service.remove_member(
                COLLET_ID,
                binding_id,
                member_command(participant_ref="u_member_001"),
                stranger,
            )
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service.replace_member(
                COLLET_ID,
                binding_id,
                GroupReplaceMemberCommand(
                    old_participant_ref="u_member_001",
                    new_participant_ref="bot_new",
                ),
                stranger,
            )
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service.change_role(
                COLLET_ID,
                binding_id,
                GroupChangeRoleCommand(
                    participant_ref="u_member_001",
                    role_binding_refs=["role_binding_x"],
                    expected_revision=revision,
                ),
                stranger,
            )

    def test_leave_is_self_help_only(self):
        """Leave 是操作对象本人自助:成员本人放行;陌生人拒绝;owner 也不
        能替别人 leave(身份与目标行 participant_ref 字符串相等才放行)。"""
        _, _, lifecycle, service = make_group_service()
        binding_id = active_group_binding(lifecycle)
        service.invite(
            COLLET_ID,
            binding_id,
            member_command(participant_ref="u_member_001"),
            make_actor(),
        )
        service.add_member(
            COLLET_ID,
            binding_id,
            member_command(participant_ref="u_member_001"),
            make_actor(),
        )
        # 陌生人拒绝。
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service.leave(
                COLLET_ID,
                binding_id,
                member_command(participant_ref="u_member_001"),
                make_actor("u_9999"),
            )
        # owner 想替成员行 leave:PRINCIPAL_ID ≠ u_member_001,拒绝。
        with pytest.raises(ContextCollabValidationError, match=PRINCIPAL_ID):
            service.leave(
                COLLET_ID,
                binding_id,
                member_command(participant_ref="u_member_001"),
                make_actor(PRINCIPAL_ID),
            )
        left = service.leave(
            COLLET_ID,
            binding_id,
            member_command(participant_ref="u_member_001"),
            make_actor("u_member_001"),
        )
        assert left.state == "LEFT"

    def test_change_role_is_owner_only_not_self_help(self):
        """T6 复审裁决(2026-11-12):ChangeRole 不经自助路径——成员自改
        role_binding_refs 属提权;成员本人(非 owner)对自己行 change_role
        拒绝,owner 放行。"""
        store, _, lifecycle, service = make_group_service()
        binding_id = active_group_binding(lifecycle)
        service.invite(
            COLLET_ID,
            binding_id,
            member_command(participant_ref="u_member_001"),
            make_actor(),
        )
        service.add_member(
            COLLET_ID,
            binding_id,
            member_command(participant_ref="u_member_001"),
            make_actor(),
        )
        revision = store.get_roster(AXES, binding_id, COLLET_ID).revision
        with pytest.raises(ContextCollabValidationError, match="u_member_001"):
            service.change_role(
                COLLET_ID,
                binding_id,
                GroupChangeRoleCommand(
                    participant_ref="u_member_001",
                    role_binding_refs=["role_binding_admin"],
                    expected_revision=revision,
                ),
                make_actor("u_member_001"),
            )
        changed = service.change_role(
            COLLET_ID,
            binding_id,
            GroupChangeRoleCommand(
                participant_ref="u_member_001",
                role_binding_refs=["role_binding_admin"],
                expected_revision=revision,
            ),
            make_actor(),
        )
        assert changed.state == "ACTIVE"
