"""Group Method profile contracts; fixtures in group_profile_support."""

import pytest
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.l5_methods.group_profile import (
    MEMBER_TERMINAL_STATES,
    GroupMemberBinding,
    GroupRoster,
    MemberState,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    InMemoryMethodStore,
)

from tests.community.core.task.context_collab.group_profile_support import (
    AXES,
    COLLET_ID,
    GROUP_MACHINE,
    make_group_store,
    member_dict,
    roster_dict,
)


class TestGroupRosterValue:
    def test_round_trip(self):
        roster = GroupRoster.from_dict(roster_dict(revision=7))
        assert roster.to_dict() == roster_dict(revision=7)
        assert GroupRoster.from_dict(roster.to_dict()) == roster

    def test_unknown_field_rejected(self):
        with pytest.raises(ContextCollabValidationError, match="unknown field"):
            GroupRoster.from_dict({**roster_dict(), "revision_hint": 1})

    def test_required_fields_non_empty(self):
        for field in ("roster_id", "method_binding_id"):
            with pytest.raises(ContextCollabValidationError):
                GroupRoster.from_dict(roster_dict(**{field: ""}))

    def test_revision_positive_integer(self):
        assert GroupRoster.from_dict(roster_dict()).revision == 1
        for bad in (0, -1, "1", 1.0, True, None):
            with pytest.raises(ContextCollabValidationError, match="revision"):
                GroupRoster.from_dict(roster_dict(revision=bad))


class TestGroupMemberValue:
    def test_round_trip(self):
        data = member_dict(
            role_binding_refs=["role_binding_py_001"],
            left_at="2026-11-12T09:00:00+00:00",
        )
        member = GroupMemberBinding.from_dict(data)
        assert member.to_dict() == data
        assert GroupMemberBinding.from_dict(member.to_dict()) == member

    def test_state_wire_validation(self):
        assert MemberState.from_wire("INVITED") is MemberState.INVITED
        assert MemberState.from_wire("REPLACED") is MemberState.REPLACED
        with pytest.raises(ContextCollabValidationError, match="wire"):
            MemberState.from_wire("PAUSED")

    def test_state_vocabulary_matches_registered_machine(self):
        """防漂移:``GroupMemberBinding`` 状态词汇与 G4 注册机器逐字一致。"""
        machine_states = {state.name for state in GROUP_MACHINE.states}
        enum_states = {state.value for state in MemberState}
        assert (
            machine_states
            == enum_states
            == {
                "INVITED",
                "ACTIVE",
                "SUSPENDED",
                "LEFT",
                "REMOVED",
                "REPLACED",
            }
        )

    def test_terminal_state_projection_follows_machine_governance(self):
        """终态集合(重复参与者裁断依据)自注册机器治理类别派生,
        不另建平行词汇(LEFT/REMOVED/REPLACED)。"""
        assert MEMBER_TERMINAL_STATES == frozenset(
            {MemberState.LEFT, MemberState.REMOVED, MemberState.REPLACED}
        )

    def test_unknown_field_rejected(self):
        with pytest.raises(ContextCollabValidationError, match="unknown field"):
            GroupMemberBinding.from_dict({**member_dict(), "extra": 1})

    def test_required_fields_non_empty(self):
        for field in (
            "group_member_binding_id",
            "method_binding_id",
            "participant_ref",
            "state",
        ):
            with pytest.raises(ContextCollabValidationError):
                GroupMemberBinding.from_dict(member_dict(**{field: ""}))

    def test_address_is_a_string(self):
        assert GroupMemberBinding.from_dict(member_dict(address="")).address == ""
        with pytest.raises(ContextCollabValidationError, match="address"):
            GroupMemberBinding.from_dict(member_dict(address=None))

    def test_role_refs_missing_to_empty_and_empty_entry_rejected(self):
        data = member_dict()
        data.pop("role_binding_refs")
        assert GroupMemberBinding.from_dict(data).role_binding_refs == ()
        with pytest.raises(ContextCollabValidationError, match="role_binding_refs"):
            GroupMemberBinding.from_dict(member_dict(role_binding_refs=[""]))

    def test_joined_left_at_none_or_nonempty(self):
        assert (
            GroupMemberBinding.from_dict(member_dict(joined_at=None)).joined_at is None
        )
        with pytest.raises(ContextCollabValidationError, match="joined_at"):
            GroupMemberBinding.from_dict(member_dict(joined_at=""))
        with pytest.raises(ContextCollabValidationError, match="left_at"):
            GroupMemberBinding.from_dict(member_dict(left_at=""))

    def test_audit_fields_required_but_may_be_empty(self):
        member = GroupMemberBinding.from_dict(member_dict(transition_reason=""))
        assert member.transition_reason == ""
        data = member_dict()
        data.pop("transitioned_by")
        with pytest.raises(ContextCollabValidationError, match="transitioned_by"):
            GroupMemberBinding.from_dict(data)


class TestGroupStoreSlice:
    def test_insert_roster_idempotent_and_one_per_binding(self):
        store = InMemoryMethodStore()
        roster = store.insert_roster(
            AXES, GroupRoster.from_dict(roster_dict()), COLLET_ID
        )
        again = store.insert_roster(
            AXES, GroupRoster.from_dict(roster_dict()), COLLET_ID
        )
        assert again is roster
        # 同 binding 换 roster_id 的第二个 roster → 拒绝(revision 语义
        # 假定每 binding 至多一份 roster)。
        with pytest.raises(
            ContextCollabValidationError, match="differs from the stored shape"
        ):
            store.insert_roster(
                AXES,
                GroupRoster.from_dict(roster_dict(roster_id="roster_002")),
                COLLET_ID,
            )
        # 回查与缺键。
        assert store.get_roster(AXES, "mb_group_alpha", COLLET_ID) is roster
        assert store.get_roster(AXES, "mb_missing", COLLET_ID) is None

    def test_bump_roster_revision_mismatch_conflicts_and_writes_nothing(self):
        """RF#5:expected_revision 不匹配抛 ContextCollabVersionConflict,
        且 roster 不被静默改写;roster 缺失抛 NotFound。"""
        store = make_group_store()
        bumped = store.bump_roster_revision(AXES, "mb_group_alpha", 1, COLLET_ID)
        assert bumped.revision == 2
        with pytest.raises(ContextCollabVersionConflict):
            store.bump_roster_revision(AXES, "mb_group_alpha", 1, COLLET_ID)
        assert store.get_roster(AXES, "mb_group_alpha", COLLET_ID).revision == 2
        with pytest.raises(ContextCollabNotFoundError):
            store.bump_roster_revision(AXES, "mb_missing", 1, COLLET_ID)

    def test_insert_member_bumps_revision_and_rejects_non_terminal_duplicate(self):
        store = make_group_store()
        member = store.insert_member(
            AXES, GroupMemberBinding.from_dict(member_dict()), 1, COLLET_ID
        )
        assert member.state is MemberState.ACTIVE
        assert store.get_roster(AXES, "mb_group_alpha", COLLET_ID).revision == 2
        # 幂等重放:同键同 to_dict 返回既有实例,不重复 bump。
        replay = store.insert_member(
            AXES, GroupMemberBinding.from_dict(member_dict()), 2, COLLET_ID
        )
        assert replay is member
        assert store.get_roster(AXES, "mb_group_alpha", COLLET_ID).revision == 2
        # 重复参与者(非终态在册)拒绝。
        with pytest.raises(ContextCollabValidationError, match="non-terminal"):
            store.insert_member(
                AXES,
                GroupMemberBinding.from_dict(
                    member_dict(group_member_binding_id="gm_002")
                ),
                2,
                COLLET_ID,
            )
        # 终态在册的同参与者可再入;roster 缺失 → NotFound。
        revived = GroupMemberBinding.from_dict(
            member_dict(
                group_member_binding_id="gm_003",
                state="INVITED",
                joined_at=None,
            )
        )
        assert revived.state is MemberState.INVITED
        with pytest.raises(ContextCollabNotFoundError):
            store.insert_member(AXES, revived, 1, "collet_no_roster")

    def test_update_member_state_overwrites_missing_rejects(self):
        store = make_group_store()
        store.insert_member(
            AXES, GroupMemberBinding.from_dict(member_dict()), 1, COLLET_ID
        )
        suspended = GroupMemberBinding.from_dict(
            member_dict(state="SUSPENDED", transitioned_from="ACTIVE")
        )
        store.update_member_state(AXES, suspended, 2, COLLET_ID)
        listed = store.list_members(AXES, "mb_group_alpha", COLLET_ID)
        assert [m.state for m in listed] == [MemberState.SUSPENDED]
        assert store.get_roster(AXES, "mb_group_alpha", COLLET_ID).revision == 3
        ghost = GroupMemberBinding.from_dict(
            member_dict(group_member_binding_id="gm_ghost")
        )
        with pytest.raises(ContextCollabNotFoundError):
            store.update_member_state(AXES, ghost, 3, COLLET_ID)

    def test_replace_member_atomic_single_bump(self):
        """原子双写:旧→REPLACED、新→ACTIVE 一次落定,**单次** revision
        bump;陈旧 expected_revision → 版本冲突,两行都不落库。"""
        store = make_group_store()
        store.insert_member(
            AXES, GroupMemberBinding.from_dict(member_dict()), 1, COLLET_ID
        )
        replaced_old = GroupMemberBinding.from_dict(
            member_dict(
                state="REPLACED",
                left_at="2026-11-12T09:00:00+00:00",
                transitioned_from="ACTIVE",
            )
        )
        new_active = GroupMemberBinding.from_dict(
            member_dict(
                group_member_binding_id="gm_002",
                participant_ref="bot_beta_001",
                transitioned_from="",
                transition_reason="group.replace_member",
            )
        )
        store.replace_member(AXES, replaced_old, new_active, 2, COLLET_ID)
        assert store.get_roster(AXES, "mb_group_alpha", COLLET_ID).revision == 3
        listed = store.list_members(AXES, "mb_group_alpha", COLLET_ID)
        assert [(m.group_member_binding_id, m.state) for m in listed] == [
            ("gm_001", MemberState.REPLACED),
            ("gm_002", MemberState.ACTIVE),
        ]
        # 新旧必须同 binding 且 id 相异。
        with pytest.raises(ContextCollabValidationError, match="method_binding_id"):
            store.replace_member(
                AXES,
                replaced_old,
                GroupMemberBinding.from_dict(
                    member_dict(
                        participant_ref="bot_gamma_001",
                        method_binding_id="mb_other_group",
                    )
                ),
                3,
                COLLET_ID,
            )
        with pytest.raises(
            ContextCollabValidationError, match="group_member_binding_id"
        ):
            store.replace_member(
                AXES,
                replaced_old,
                GroupMemberBinding.from_dict(
                    member_dict(
                        participant_ref="bot_gamma_001",
                        group_member_binding_id="gm_001",
                    )
                ),
                3,
                COLLET_ID,
            )
        # 陈旧 revision → 版本冲突,两个新实例都不得落库。
        with pytest.raises(ContextCollabVersionConflict):
            store.replace_member(
                AXES,
                replaced_old,
                GroupMemberBinding.from_dict(
                    member_dict(
                        group_member_binding_id="gm_004",
                        participant_ref="bot_delta_001",
                    )
                ),
                99,
                COLLET_ID,
            )
        assert [
            m.group_member_binding_id
            for m in store.list_members(AXES, "mb_group_alpha", COLLET_ID)
        ] == ["gm_001", "gm_002"]
        assert store.get_roster(AXES, "mb_group_alpha", COLLET_ID).revision == 3

    def test_list_members_insertion_order_and_context_scoped(self):
        store = make_group_store()
        other_ctx = "collet_group_002"
        store.insert_roster(AXES, GroupRoster.from_dict(roster_dict()), other_ctx)
        store.insert_member(
            AXES, GroupMemberBinding.from_dict(member_dict()), 1, COLLET_ID
        )
        store.insert_member(
            AXES,
            GroupMemberBinding.from_dict(
                member_dict(
                    group_member_binding_id="gm_002", participant_ref="bot_beta_001"
                )
            ),
            2,
            COLLET_ID,
        )
        store.insert_member(
            AXES, GroupMemberBinding.from_dict(member_dict()), 1, other_ctx
        )
        assert [
            m.group_member_binding_id
            for m in store.list_members(AXES, "mb_group_alpha", COLLET_ID)
        ] == ["gm_001", "gm_002"]
        assert [
            m.group_member_binding_id
            for m in store.list_members(AXES, "mb_group_alpha", other_ctx)
        ] == ["gm_001"]
