"""Group Method lifecycle contracts; fixtures in group_profile_support."""

import pytest
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.l5_methods.group_profile import (
    MemberState,
)
from agentclaw.community.core.task.context_collab.method_service_api import (
    GroupChangeRoleCommand,
    GroupMethodOpsServiceProtocol,
    GroupReplaceMemberCommand,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    InMemoryMethodStore,
)
from agentclaw.community.core.task.context_collab.services.method_lifecycle_service import (
    project_created_context,
)

from tests.community.core.task.context_collab.group_profile_support import (
    AXES,
    COLLET_ID,
    EXECUTOR_ID,
    NOW,
    PRINCIPAL_ID,
    _FakeCollabRepository,
    _status_record,
    active_group_binding,
    make_actor,
    make_group_service,
    member_command,
)
from tests.community.core.task.context_collab.product_catalog import PRODUCT_CATALOG


class TestGroupMemberLifecycle:
    def test_invite_add_member_projects_roster_and_states(self):
        """invite 建 INVITED 成员行(无状态迁移,transitioned_from 空串),
        roster 惰性创建;add_member 经机器裁决 INVITED→ACTIVE 并落 joined_at。"""
        store, _, lifecycle, service = make_group_service()
        binding_id = active_group_binding(lifecycle)
        invited = service.invite(
            COLLET_ID,
            binding_id,
            member_command(
                participant_ref="bot_alpha_001",
                address="agent://bot_alpha_001",
                role_binding_refs=["role_binding_py_001"],
                member_binding_id="gm_invite_1",
            ),
            make_actor(),
        )
        assert invited.accepted is True
        assert invited.object_kind == "group_member"
        assert invited.object_id == "gm_invite_1"
        assert invited.state == "INVITED"
        row = next(
            m
            for m in store.list_members(AXES, binding_id, COLLET_ID)
            if m.group_member_binding_id == "gm_invite_1"
        )
        assert row.state is MemberState.INVITED
        assert row.joined_at is None
        assert row.transitioned_from == ""
        assert row.transition_reason == "group.invite"
        assert row.transitioned_by == PRINCIPAL_ID
        assert row.role_binding_refs == ("role_binding_py_001",)
        roster = store.get_roster(AXES, binding_id, COLLET_ID)
        assert roster.roster_id == f"roster_{binding_id}"
        assert roster.revision == 2
        # add_member:机器裁决 INVITED─group.add_member→ACTIVE。
        added = service.add_member(
            COLLET_ID,
            binding_id,
            member_command(participant_ref="bot_alpha_001"),
            make_actor(),
        )
        assert added.state == "ACTIVE"
        row = next(
            m
            for m in store.list_members(AXES, binding_id, COLLET_ID)
            if m.group_member_binding_id == "gm_invite_1"
        )
        assert row.state is MemberState.ACTIVE
        assert row.joined_at
        assert row.transitioned_from == "INVITED"
        assert row.transition_reason == "group.add_member"
        assert store.get_roster(AXES, binding_id, COLLET_ID).revision == 3

    def test_suspend_resume_member_reachability(self):
        """作者裁决补漏:resume_member(SUSPENDED→ACTIVE)必须可达。"""
        store, _, lifecycle, service = make_group_service()
        binding_id = active_group_binding(lifecycle)
        service.invite(
            COLLET_ID,
            binding_id,
            member_command(participant_ref="bot_su_001"),
            make_actor(),
        )
        service.add_member(
            COLLET_ID,
            binding_id,
            member_command(participant_ref="bot_su_001"),
            make_actor(),
        )
        suspended = service.suspend_member(
            COLLET_ID,
            binding_id,
            member_command(participant_ref="bot_su_001", reason="冻结成员"),
            make_actor(),
        )
        assert suspended.state == "SUSPENDED"
        resumed = service.resume_member(
            COLLET_ID,
            binding_id,
            member_command(participant_ref="bot_su_001", reason="恢复成员"),
            make_actor(),
        )
        assert resumed.state == "ACTIVE"
        rows = {
            m.group_member_binding_id: m
            for m in store.list_members(AXES, binding_id, COLLET_ID)
        }
        row = next(iter(rows.values()))
        assert row.transitioned_from == "SUSPENDED"
        assert row.transition_reason == "恢复成员"
        assert row.state is MemberState.ACTIVE

    def test_leave_reachability_from_active_and_suspended(self):
        """作者裁决补漏:Leave 可达,ACTIVE 与 SUSPENDED 两条规则(leave_r1/
        leave_r2)都经机器裁决;leave 是成员本人自助;落 left_at。"""
        store, _, lifecycle, service = make_group_service()
        binding_id = active_group_binding(lifecycle)
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
        # leave_r1:ACTIVE─group.leave→LEFT(成员本人)。
        left_active = service.leave(
            COLLET_ID,
            binding_id,
            member_command(participant_ref="u_member_001"),
            make_actor("u_member_001"),
        )
        assert left_active.state == "LEFT"
        # leave_r2:SUSPENDED─group.leave→LEFT。
        service.suspend_member(
            COLLET_ID,
            binding_id,
            member_command(participant_ref="u_member_002"),
            make_actor(),
        )
        left_suspended = service.leave(
            COLLET_ID,
            binding_id,
            member_command(participant_ref="u_member_002"),
            make_actor("u_member_002"),
        )
        assert left_suspended.state == "LEFT"
        for row in store.list_members(AXES, binding_id, COLLET_ID):
            assert row.state is MemberState.LEFT
            assert row.left_at
            assert row.transition_reason == "group.leave"

    def test_remove_member_from_active_and_suspended(self):
        """remove_member:ACTIVE/SUSPENDED──→REMOVED 两条规则;handover_to
        P1 只校验格式,不执行工作转移。"""
        store, _, lifecycle, service = make_group_service()
        binding_id = active_group_binding(lifecycle)
        for participant in ("bot_rm_001", "bot_rm_002"):
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
        removed_active = service.remove_member(
            COLLET_ID,
            binding_id,
            member_command(
                participant_ref="bot_rm_001", handover_to="bot_handover_001"
            ),
            make_actor(),
        )
        assert removed_active.state == "REMOVED"
        service.suspend_member(
            COLLET_ID,
            binding_id,
            member_command(participant_ref="bot_rm_002"),
            make_actor(),
        )
        removed_suspended = service.remove_member(
            COLLET_ID,
            binding_id,
            member_command(participant_ref="bot_rm_002"),
            make_actor(),
        )
        assert removed_suspended.state == "REMOVED"
        states = {
            m.participant_ref: m.state
            for m in store.list_members(AXES, binding_id, COLLET_ID)
        }
        assert states == {
            "bot_rm_001": MemberState.REMOVED,
            "bot_rm_002": MemberState.REMOVED,
        }
        # handover_to 格式校验:P1 不回填转移行,空串拒绝。
        service.invite(
            COLLET_ID,
            binding_id,
            member_command(participant_ref="bot_rm_003"),
            make_actor(),
        )
        with pytest.raises(ContextCollabValidationError, match="handover_to"):
            service.remove_member(
                COLLET_ID,
                binding_id,
                member_command(participant_ref="bot_rm_003", handover_to=""),
                make_actor(),
            )

    def test_replace_member_atomic_double_write_single_bump(self):
        """replace_member:旧 ACTIVE→REPLACED、新参与者直接 ACTIVE,单次
        roster revision bump;新参与者不能是在册非终态成员。"""
        store, _, lifecycle, service = make_group_service()
        binding_id = active_group_binding(lifecycle)
        service.invite(
            COLLET_ID,
            binding_id,
            member_command(participant_ref="bot_old_001"),
            make_actor(),
        )
        service.add_member(
            COLLET_ID,
            binding_id,
            member_command(participant_ref="bot_old_001"),
            make_actor(),
        )
        revision_before = store.get_roster(AXES, binding_id, COLLET_ID).revision
        result = service.replace_member(
            COLLET_ID,
            binding_id,
            GroupReplaceMemberCommand(
                old_participant_ref="bot_old_001",
                new_participant_ref="bot_new_001",
                address="agent://bot_new_001",
                role_binding_refs=["role_binding_reviewer"],
                reason="换人",
            ),
            make_actor(),
        )
        assert result.accepted is True
        assert result.state == "ACTIVE"
        assert result.object_id.startswith("gm_")
        roster = store.get_roster(AXES, binding_id, COLLET_ID)
        assert roster.revision == revision_before + 1
        rows = {
            m.participant_ref: m
            for m in store.list_members(AXES, binding_id, COLLET_ID)
        }
        assert rows["bot_old_001"].state is MemberState.REPLACED
        assert rows["bot_old_001"].left_at
        assert rows["bot_new_001"].state is MemberState.ACTIVE
        assert rows["bot_new_001"].joined_at
        assert rows["bot_new_001"].role_binding_refs == ("role_binding_reviewer",)
        # 非终态在册的新参与者拒绝。
        service.invite(
            COLLET_ID,
            binding_id,
            member_command(participant_ref="bot_busy_001"),
            make_actor(),
        )
        with pytest.raises(ContextCollabValidationError, match="non-terminal"):
            service.replace_member(
                COLLET_ID,
                binding_id,
                GroupReplaceMemberCommand(
                    old_participant_ref="bot_new_001",
                    new_participant_ref="bot_busy_001",
                ),
                make_actor(),
            )

    def test_change_role_writes_refs_without_transition(self):
        """change_role:只写 role_binding_refs(roster revision +1),
        无状态迁移、审计不覆写;基准 revision 走命令的 expected_revision。"""
        store, _, lifecycle, service = make_group_service()
        binding_id = active_group_binding(lifecycle)
        service.invite(
            COLLET_ID,
            binding_id,
            member_command(participant_ref="bot_role_001", member_binding_id="gm_role"),
            make_actor(),
        )
        service.add_member(
            COLLET_ID,
            binding_id,
            member_command(participant_ref="bot_role_001"),
            make_actor(),
        )
        transitioned_at = next(
            m.transitioned_at for m in store.list_members(AXES, binding_id, COLLET_ID)
        )
        revision = store.get_roster(AXES, binding_id, COLLET_ID).revision
        result = service.change_role(
            COLLET_ID,
            binding_id,
            GroupChangeRoleCommand(
                participant_ref="bot_role_001",
                role_binding_refs=["role_binding_admin"],
                expected_revision=revision,
                reason="调整角色",
            ),
            make_actor(),
        )
        assert result.accepted is True
        assert result.state == "ACTIVE"  # 状态不动
        assert result.object_id == "gm_role"
        row = next(
            m
            for m in store.list_members(AXES, binding_id, COLLET_ID)
            if m.group_member_binding_id == "gm_role"
        )
        assert row.role_binding_refs == ("role_binding_admin",)
        assert row.state is MemberState.ACTIVE
        assert row.transitioned_at == transitioned_at  # 审计不覆写
        assert store.get_roster(AXES, binding_id, COLLET_ID).revision == revision + 1
        # role refs 格式校验:空串条目拒绝。
        with pytest.raises(ContextCollabValidationError, match="role_binding_refs"):
            service.change_role(
                COLLET_ID,
                binding_id,
                GroupChangeRoleCommand(
                    participant_ref="bot_role_001",
                    role_binding_refs=[""],
                    expected_revision=revision + 1,
                ),
                make_actor(),
            )

    def test_illegal_transitions_rejected_by_machine(self):
        """非法迁移(机器无出边)一律 ContextCollabValidationError:
        suspend INVITED、resume ACTIVE、remove INVITED、add 已 ACTIVE 成员。"""
        _, _, lifecycle, service = make_group_service()
        binding_id = active_group_binding(lifecycle)
        service.invite(
            COLLET_ID,
            binding_id,
            member_command(participant_ref="bot_st_001"),
            make_actor(),
        )
        service.add_member(
            COLLET_ID,
            binding_id,
            member_command(participant_ref="bot_st_001"),
            make_actor(),
        )
        service.invite(
            COLLET_ID,
            binding_id,
            member_command(participant_ref="bot_st_002"),
            make_actor(),
        )
        with pytest.raises(ContextCollabValidationError, match="does not accept event"):
            service.suspend_member(
                COLLET_ID,
                binding_id,
                member_command(participant_ref="bot_st_002"),
                make_actor(),
            )
        with pytest.raises(ContextCollabValidationError, match="does not accept event"):
            service.resume_member(
                COLLET_ID,
                binding_id,
                member_command(participant_ref="bot_st_001"),
                make_actor(),
            )
        with pytest.raises(ContextCollabValidationError, match="does not accept event"):
            service.remove_member(
                COLLET_ID,
                binding_id,
                member_command(participant_ref="bot_st_002"),
                make_actor(),
            )
        with pytest.raises(ContextCollabValidationError, match="does not accept event"):
            service.add_member(
                COLLET_ID,
                binding_id,
                member_command(participant_ref="bot_st_001"),
                make_actor(),
            )

    def test_invite_replay_same_idempotency_key(self):
        """§8.3:同 member_binding_id 同请求形态返回既有已受理对象,
        不新增行、不 bump revision;异形态 409 语义拒绝。"""
        store, _, lifecycle, service = make_group_service()
        binding_id = active_group_binding(lifecycle)
        first = service.invite(
            COLLET_ID,
            binding_id,
            member_command(
                participant_ref="bot_replay_001",
                member_binding_id="gm_replay_1",
                role_binding_refs=["role_binding_py"],
            ),
            make_actor(),
        )
        replay = service.invite(
            COLLET_ID,
            binding_id,
            member_command(
                participant_ref="bot_replay_001",
                member_binding_id="gm_replay_1",
                role_binding_refs=["role_binding_py"],
            ),
            make_actor(),
        )
        assert replay.object_id == first.object_id == "gm_replay_1"
        assert len(store.list_members(AXES, binding_id, COLLET_ID)) == 1
        with pytest.raises(
            ContextCollabValidationError, match="differs from the stored shape"
        ):
            service.invite(
                COLLET_ID,
                binding_id,
                member_command(
                    participant_ref="bot_replay_002",
                    member_binding_id="gm_replay_1",
                ),
                make_actor(),
            )
        assert len(store.list_members(AXES, binding_id, COLLET_ID)) == 1

    def test_unknown_member_not_found(self):
        _, _, lifecycle, service = make_group_service()
        binding_id = active_group_binding(lifecycle)
        for op_call in (
            lambda: service.add_member(
                COLLET_ID,
                binding_id,
                member_command(participant_ref="bot_ghost"),
                make_actor(),
            ),
            lambda: service.suspend_member(
                COLLET_ID,
                binding_id,
                member_command(participant_ref="bot_ghost"),
                make_actor(),
            ),
            lambda: service.resume_member(
                COLLET_ID,
                binding_id,
                member_command(participant_ref="bot_ghost"),
                make_actor(),
            ),
            lambda: service.remove_member(
                COLLET_ID,
                binding_id,
                member_command(participant_ref="bot_ghost"),
                make_actor(),
            ),
            lambda: service.leave(
                COLLET_ID,
                binding_id,
                member_command(participant_ref="bot_ghost"),
                make_actor(),
            ),
            lambda: service.change_role(
                COLLET_ID,
                binding_id,
                GroupChangeRoleCommand(
                    participant_ref="bot_ghost",
                    role_binding_refs=["role_binding_x"],
                    expected_revision=1,
                ),
                make_actor(),
            ),
            lambda: service.replace_member(
                COLLET_ID,
                binding_id,
                GroupReplaceMemberCommand(
                    old_participant_ref="bot_ghost",
                    new_participant_ref="bot_someone",
                ),
                make_actor(),
            ),
        ):
            with pytest.raises(ContextCollabNotFoundError):
                op_call()

    def test_missing_binding_not_found(self):
        _, _, _, service = make_group_service()
        with pytest.raises(ContextCollabNotFoundError):
            service.invite(COLLET_ID, "mb_ghost", member_command(), make_actor())


class TestRosterRevisionConflict:
    def test_roster_revision_conflict(self):
        """RF#5:陈旧 expected_revision 的 roster 写入抛
        ContextCollabVersionConflict,成员行与 roster 都不静默落库。"""
        store, _, lifecycle, service = make_group_service()
        binding_id = active_group_binding(lifecycle)
        service.invite(
            COLLET_ID,
            binding_id,
            member_command(participant_ref="u_member_001", member_binding_id="gm_rc_1"),
            make_actor(),
        )
        service.add_member(
            COLLET_ID,
            binding_id,
            member_command(participant_ref="u_member_001"),
            make_actor(),
        )
        stale_revision = store.get_roster(AXES, binding_id, COLLET_ID).revision
        # 并发式:另一操作先一步 bump 了 revision。
        service.change_role(
            COLLET_ID,
            binding_id,
            GroupChangeRoleCommand(
                participant_ref="u_member_001",
                role_binding_refs=["role_binding_te"],
                expected_revision=stale_revision,
            ),
            make_actor(),
        )
        current = store.get_roster(AXES, binding_id, COLLET_ID)
        assert current.revision == stale_revision + 1
        # 携带陈旧 revision 的写:409 语义拒绝,roles 不被静默覆写。
        with pytest.raises(ContextCollabVersionConflict):
            service.change_role(
                COLLET_ID,
                binding_id,
                GroupChangeRoleCommand(
                    participant_ref="u_member_001",
                    role_binding_refs=["role_binding_admin"],
                    expected_revision=stale_revision,
                ),
                make_actor(),
            )
        row = next(
            m
            for m in store.list_members(AXES, binding_id, COLLET_ID)
            if m.group_member_binding_id == "gm_rc_1"
        )
        assert row.role_binding_refs == ("role_binding_te",)
        assert store.get_roster(AXES, binding_id, COLLET_ID).revision == (
            stale_revision + 1
        )
        # 最新 revision 的写照常成功。
        service.change_role(
            COLLET_ID,
            binding_id,
            GroupChangeRoleCommand(
                participant_ref="u_member_001",
                role_binding_refs=["role_binding_admin"],
                expected_revision=stale_revision + 1,
            ),
            make_actor(),
        )
        assert store.get_roster(AXES, binding_id, COLLET_ID).revision == (
            stale_revision + 2
        )


class TestCreatedContextProjection:
    def test_project_created_context_creates_roster_and_initial_members(self):
        store = InMemoryMethodStore()
        repo = _FakeCollabRepository({COLLET_ID: _status_record()})
        record = repo.get_current_status(COLLET_ID)
        binding = project_created_context(
            store,
            AXES,
            COLLET_ID,
            record.context_spec,
            make_actor(),
            now=NOW,
            catalog=PRODUCT_CATALOG,
        )
        assert "Group" in binding.method_names
        roster = store.get_roster(AXES, binding.method_binding_id, COLLET_ID)
        assert roster is not None
        assert roster.method_binding_id == binding.method_binding_id
        assert roster.revision == 3  # 1 + 两次成员插入 bump
        members = {
            m.participant_ref: m
            for m in store.list_members(AXES, binding.method_binding_id, COLLET_ID)
        }
        assert set(members) == {PRINCIPAL_ID, EXECUTOR_ID}
        for row in members.values():
            assert row.state is MemberState.ACTIVE
            assert row.joined_at == NOW
            assert row.left_at is None
            assert row.transitioned_from == ""
            assert row.transition_reason == "create_context"
            assert row.transitioned_by == PRINCIPAL_ID
        # 重复投影幂等:roster 与成员不重复落。
        again = project_created_context(
            store,
            AXES,
            COLLET_ID,
            record.context_spec,
            make_actor(),
            now="2026-11-12T09:30:00+00:00",
            catalog=PRODUCT_CATALOG,
        )
        assert again.method_binding_id == binding.method_binding_id
        assert (
            store.get_roster(AXES, binding.method_binding_id, COLLET_ID).revision == 3
        )
        assert len(store.list_members(AXES, binding.method_binding_id, COLLET_ID)) == 2

    def test_single_context_projects_no_roster(self):
        """Single 顶层方法无 Group 面向:不投影 roster/成员行。"""
        store = InMemoryMethodStore()
        repo = _FakeCollabRepository({COLLET_ID: _status_record(method_kind="Single")})
        record = repo.get_current_status(COLLET_ID)
        binding = project_created_context(
            store,
            AXES,
            COLLET_ID,
            record.context_spec,
            make_actor(),
            now=NOW,
            catalog=PRODUCT_CATALOG,
        )
        assert binding.method_names == ("Single",)
        assert store.get_roster(AXES, binding.method_binding_id, COLLET_ID) is None
        assert store.list_members(AXES, binding.method_binding_id, COLLET_ID) == []


class TestGroupOpsSideEffects:
    def test_group_ops_create_no_bindings_or_selections(self):
        """G1 断言:roster 不建 L3 Membership、不扩资源权限——group 全操作
        后 binding/selection 切片零变化(纯断言,无资源面副作用)。"""
        store, _, lifecycle, service = make_group_service()
        binding_id = active_group_binding(lifecycle)
        bindings_before = len(store.list_bindings(AXES, COLLET_ID))
        service.invite(
            COLLET_ID,
            binding_id,
            member_command(participant_ref="bot_x_001"),
            make_actor(),
        )
        service.add_member(
            COLLET_ID,
            binding_id,
            member_command(participant_ref="bot_x_001"),
            make_actor(),
        )
        service.leave(
            COLLET_ID,
            binding_id,
            member_command(participant_ref="bot_x_001"),
            make_actor("bot_x_001"),
        )
        assert len(store.list_bindings(AXES, COLLET_ID)) == bindings_before
        assert store.list_selections(AXES, binding_id, COLLET_ID) == []


class TestProtocolConformance:
    def test_service_satisfies_group_protocol(self):
        """Group 补全后 ``SingleGroupMethodService`` 满足
        ``GroupMethodOpsServiceProtocol`` 的 runtime_checkable isinstance 面。"""
        store, _, lifecycle, service = make_group_service()
        assert isinstance(service, GroupMethodOpsServiceProtocol)
