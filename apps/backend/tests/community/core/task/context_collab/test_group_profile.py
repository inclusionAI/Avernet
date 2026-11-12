"""Task 10:Group 方法剖面(GroupRoster / GroupMemberBinding + 八操作)单测。

覆盖(spec §4.2):
- 值对象:round-trip / 状态 wire 校验 / 未知字段拒绝 / refs 校验 /
  G4 注册机器词汇对齐(防漂移,6 状态);
- store 切片:roster 幂等插入、``bump_roster_revision`` 版本比对
  (RF #5:不匹配 → ContextCollabVersionConflict 且写不落库)、成员
  CRUD 在 revision 守卫下、``replace_member`` 锁内原子双写单次 bump、
  非终态参与者重复拒绝、list 按插入序;
- 服务八操作(invite/add_member/suspend_member/resume_member/leave/
  remove_member/replace_member/change_role):成员状态机 6 状态全覆盖、
  9 条迁移规则全部经 G4 机器裁决,ResumeMember/Leave 可达性 pin;
- 授权矩阵(T6):7 个 owner 操作 principal/executor 放行、陌生人拒绝;
  ChangeRole 不走自助路径(成员自改属提权,只有 owner 可改);Leave 是
  成员本人自助(与目标行 participant_ref 字符串相等);
- RF#2:binding 非 ACTIVE 时八个副作用操作一律拒绝;
- invite 拒绝已有非终态成员行的参与者;离开后重新 invite 合法;
- roster 乐观并发:`test_roster_revision_conflict`(陈旧 expected_revision
  → 409 语义,写不静默成功);
- ``project_created_context`` 补挂 roster + 初始成员(principal/executor
  ACTIVE 成员行,spec §9.2);
- G1 边界:roster 不建 L3 Membership、不扩资源权限——group 全操作后
  binding/selection 切片零变化(纯断言);
- 协议:Group 补全后 ``SingleGroupMethodService`` 整体满足
  ``GroupMethodOpsServiceProtocol`` 的 runtime_checkable isinstance 面。

collet 仓储用手写 fake(受控返回 ``ContextStatusRecord``),store 用真
``InMemoryMethodStore``,lifecycle 用真 ``MethodLifecycleService``。
"""

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.method_authorization import (
    GROUP_OWNER_OPS,
    GROUP_SELF_HELP_OPS,
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
from agentclaw.community.core.task.context_collab.l5_methods.group_profile import (
    MEMBER_TERMINAL_STATES,
    GroupMemberBinding,
    GroupRoster,
    MemberState,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    BindingState,
)
from agentclaw.community.core.task.context_collab.method_service_api import (
    BindMethodCommand,
    GroupChangeRoleCommand,
    GroupMemberCommand,
    GroupMethodOpsServiceProtocol,
    GroupReplaceMemberCommand,
    MethodStateChangeCommand,
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
    project_created_context,
)
from agentclaw.community.core.task.context_collab.services.single_group_method_service import (
    SingleGroupMethodService,
)

COLLET_ID = "collet_group_001"
PRINCIPAL_ID = "u_2001"
EXECUTOR_ID = "bot_group_exec_001"
NOW = "2026-11-12T08:00:00+00:00"

OBJECTIVE = "为 BBS 公开讨论落 Group 剖面 roster/成员语义测试"

AXES = current_axes()

GROUP_MACHINE = METHOD_MACHINE_REGISTRY["group_member"]


def make_actor(principal_id: str = PRINCIPAL_ID) -> ActorContext:
    return ActorContext(actor=PrincipalRef("human", principal_id), source="api")


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


def make_group_service():
    """真 store + 真 authorizer + 真 lifecycle + 受控 fake 仓储的服务四件套。"""
    store = InMemoryMethodStore()
    repo = _FakeCollabRepository({COLLET_ID: _status_record()})
    lifecycle = MethodLifecycleService(
        store=store,
        authorizer=StructuralMethodOpsAuthorizer(),
        collab_repo=repo,
    )
    service = SingleGroupMethodService(
        store=store,
        authorizer=StructuralMethodOpsAuthorizer(),
        collab_repo=repo,
        lifecycle=lifecycle,
    )
    return store, repo, lifecycle, service


def active_group_binding(
    lifecycle: MethodLifecycleService,
    binding_id: str = "mb_group_alpha",
    collab_context_id: str = COLLET_ID,
) -> str:
    """bind + activate 一条嵌套 Group binding(group 八操作的前置)。"""
    lifecycle.bind(
        collab_context_id,
        BindMethodCommand(
            method_kind="Group",
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


def member_command(**overrides) -> GroupMemberCommand:
    """group 成员操作共用命令基型;字段可覆盖。"""
    fields = {"participant_ref": "bot_alpha_001", "reason": ""}
    fields.update(overrides)
    return GroupMemberCommand(**fields)


def roster_dict(**overrides) -> dict:
    """GroupRoster 的 ``from_dict`` 输入基型;字段可覆盖。"""
    fields = {
        "roster_id": "roster_001",
        "method_binding_id": "mb_group_alpha",
        "revision": 1,
    }
    fields.update(overrides)
    return fields


def member_dict(**overrides) -> dict:
    """GroupMemberBinding 的 ``from_dict`` 输入基型;字段可覆盖。"""
    fields = {
        "group_member_binding_id": "gm_001",
        "method_binding_id": "mb_group_alpha",
        "participant_ref": "bot_alpha_001",
        "address": "agent://bot_alpha_001",
        "role_binding_refs": [],
        "state": "ACTIVE",
        "joined_at": NOW,
        "left_at": None,
        "transitioned_from": "INVITED",
        "transition_reason": "group.add_member",
        "transitioned_by": PRINCIPAL_ID,
        "transitioned_at": NOW,
    }
    fields.update(overrides)
    return fields


# ---------------------------------------------------------------------------
# 值对象(spec §4.2 YAML + §6.3 审计四字段)
# ---------------------------------------------------------------------------


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
        assert machine_states == enum_states == {
            "INVITED", "ACTIVE", "SUSPENDED", "LEFT", "REMOVED", "REPLACED",
        }

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
        assert GroupMemberBinding.from_dict(
            member_dict(address="")
        ).address == ""
        with pytest.raises(ContextCollabValidationError, match="address"):
            GroupMemberBinding.from_dict(member_dict(address=None))

    def test_role_refs_missing_to_empty_and_empty_entry_rejected(self):
        data = member_dict()
        data.pop("role_binding_refs")
        assert GroupMemberBinding.from_dict(data).role_binding_refs == ()
        with pytest.raises(ContextCollabValidationError, match="role_binding_refs"):
            GroupMemberBinding.from_dict(member_dict(role_binding_refs=[""]))

    def test_joined_left_at_none_or_nonempty(self):
        assert GroupMemberBinding.from_dict(
            member_dict(joined_at=None)
        ).joined_at is None
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


# ---------------------------------------------------------------------------
# store 切片(roster revision 乐观并发 + 成员 CRUD + 原子换人;spec §6.1/§4.2)
# ---------------------------------------------------------------------------


def make_group_store():
    """带一条 revision=1 roster 的裸 store(store 切片测试前置)。"""
    store = InMemoryMethodStore()
    roster = store.insert_roster(
        AXES, GroupRoster.from_dict(roster_dict()), COLLET_ID
    )
    assert roster.revision == 1
    return store


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
        assert (
            store.get_roster(AXES, "mb_group_alpha", COLLET_ID) is roster
        )
        assert store.get_roster(AXES, "mb_missing", COLLET_ID) is None

    def test_bump_roster_revision_mismatch_conflicts_and_writes_nothing(self):
        """RF#5:expected_revision 不匹配抛 ContextCollabVersionConflict,
        且 roster 不被静默改写;roster 缺失抛 NotFound。"""
        store = make_group_store()
        bumped = store.bump_roster_revision(
            AXES, "mb_group_alpha", 1, COLLET_ID
        )
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
        with pytest.raises(
            ContextCollabValidationError, match="non-terminal"
        ):
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
        ghost = GroupMemberBinding.from_dict(member_dict(
            group_member_binding_id="gm_ghost"
        ))
        with pytest.raises(ContextCollabNotFoundError):
            store.update_member_state(AXES, ghost, 3, COLLET_ID)

    def test_replace_member_atomic_single_bump(self):
        """原子双写:旧→REPLACED、新→ACTIVE 一次落定,**单次** revision
        bump;陈旧 expected_revision → 版本冲突,两行都不落库。"""
        store = make_group_store()
        old = store.insert_member(
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
                    member_dict(participant_ref="bot_gamma_001",
                                method_binding_id="mb_other_group")
                ),
                3,
                COLLET_ID,
            )
        with pytest.raises(ContextCollabValidationError, match="group_member_binding_id"):
            store.replace_member(
                AXES,
                replaced_old,
                GroupMemberBinding.from_dict(
                    member_dict(participant_ref="bot_gamma_001",
                                group_member_binding_id="gm_001")
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
        store.insert_roster(
            AXES, GroupRoster.from_dict(roster_dict()), other_ctx
        )
        store.insert_member(
            AXES, GroupMemberBinding.from_dict(member_dict()), 1, COLLET_ID
        )
        store.insert_member(
            AXES,
            GroupMemberBinding.from_dict(
                member_dict(group_member_binding_id="gm_002",
                            participant_ref="bot_beta_001")
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


# ---------------------------------------------------------------------------
# 服务八操作(6 状态全覆盖 + 9 条迁移规则经机器裁决 + 幂等/守卫)
# ---------------------------------------------------------------------------

OWNER_OPS_ALL = (
    "invite",
    "add_member",
    "suspend_member",
    "resume_member",
    "remove_member",
    "replace_member",
    "change_role",
)


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
            COLLET_ID, binding_id, member_command(participant_ref="bot_alpha_001"),
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
            COLLET_ID, binding_id, member_command(participant_ref="bot_su_001"),
            make_actor(),
        )
        service.add_member(
            COLLET_ID, binding_id, member_command(participant_ref="bot_su_001"),
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
                COLLET_ID, binding_id, member_command(participant_ref=participant),
                make_actor(),
            )
            service.add_member(
                COLLET_ID, binding_id, member_command(participant_ref=participant),
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
                COLLET_ID, binding_id, member_command(participant_ref=participant),
                make_actor(),
            )
            service.add_member(
                COLLET_ID, binding_id, member_command(participant_ref=participant),
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
            COLLET_ID, binding_id, member_command(participant_ref="bot_rm_002"),
            make_actor(),
        )
        removed_suspended = service.remove_member(
            COLLET_ID, binding_id, member_command(participant_ref="bot_rm_002"),
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
            COLLET_ID, binding_id, member_command(participant_ref="bot_rm_003"),
            make_actor(),
        )
        with pytest.raises(
            ContextCollabValidationError, match="handover_to"
        ):
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
            COLLET_ID, binding_id, member_command(participant_ref="bot_old_001"),
            make_actor(),
        )
        service.add_member(
            COLLET_ID, binding_id, member_command(participant_ref="bot_old_001"),
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
        assert rows["bot_new_001"].role_binding_refs == (
            "role_binding_reviewer",
        )
        # 非终态在册的新参与者拒绝。
        service.invite(
            COLLET_ID, binding_id, member_command(participant_ref="bot_busy_001"),
            make_actor(),
        )
        with pytest.raises(
            ContextCollabValidationError, match="non-terminal"
        ):
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
            COLLET_ID, binding_id, member_command(participant_ref="bot_role_001"),
            make_actor(),
        )
        transitioned_at = next(
            m.transitioned_at
            for m in store.list_members(AXES, binding_id, COLLET_ID)
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
        with pytest.raises(
            ContextCollabValidationError, match="role_binding_refs"
        ):
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
            COLLET_ID, binding_id, member_command(participant_ref="bot_st_001"),
            make_actor(),
        )
        service.add_member(
            COLLET_ID, binding_id, member_command(participant_ref="bot_st_001"),
            make_actor(),
        )
        service.invite(
            COLLET_ID, binding_id, member_command(participant_ref="bot_st_002"),
            make_actor(),
        )
        with pytest.raises(
            ContextCollabValidationError, match="does not accept event"
        ):
            service.suspend_member(
                COLLET_ID, binding_id, member_command(participant_ref="bot_st_002"),
                make_actor(),
            )
        with pytest.raises(
            ContextCollabValidationError, match="does not accept event"
        ):
            service.resume_member(
                COLLET_ID, binding_id, member_command(participant_ref="bot_st_001"),
                make_actor(),
            )
        with pytest.raises(
            ContextCollabValidationError, match="does not accept event"
        ):
            service.remove_member(
                COLLET_ID, binding_id, member_command(participant_ref="bot_st_002"),
                make_actor(),
            )
        with pytest.raises(
            ContextCollabValidationError, match="does not accept event"
        ):
            service.add_member(
                COLLET_ID, binding_id, member_command(participant_ref="bot_st_001"),
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
                COLLET_ID, binding_id, member_command(participant_ref="bot_ghost"),
                make_actor(),
            ),
            lambda: service.suspend_member(
                COLLET_ID, binding_id, member_command(participant_ref="bot_ghost"),
                make_actor(),
            ),
            lambda: service.resume_member(
                COLLET_ID, binding_id, member_command(participant_ref="bot_ghost"),
                make_actor(),
            ),
            lambda: service.remove_member(
                COLLET_ID, binding_id, member_command(participant_ref="bot_ghost"),
                make_actor(),
            ),
            lambda: service.leave(
                COLLET_ID, binding_id, member_command(participant_ref="bot_ghost"),
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
            service.invite(
                COLLET_ID, "mb_ghost", member_command(), make_actor()
            )


class TestGroupGuards:
    def test_invite_rejects_non_terminal_duplicate_but_allows_returning(self):
        _, _, lifecycle, service = make_group_service()
        binding_id = active_group_binding(lifecycle)
        service.invite(
            COLLET_ID, binding_id, member_command(participant_ref="bot_dup_001"),
            make_actor(),
        )
        with pytest.raises(ContextCollabValidationError, match="non-terminal"):
            service.invite(
                COLLET_ID, binding_id, member_command(participant_ref="bot_dup_001"),
                make_actor(),
            )
        # 走完生命周期(add→leave 终态)后可重新 invite。
        service.add_member(
            COLLET_ID, binding_id, member_command(participant_ref="bot_dup_001"),
            make_actor(),
        )
        service.leave(
            COLLET_ID, binding_id, member_command(participant_ref="bot_dup_001"),
            make_actor("bot_dup_001"),
        )
        reinvited = service.invite(
            COLLET_ID, binding_id, member_command(participant_ref="bot_dup_001"),
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
                COLLET_ID, binding_id, member_command(participant_ref=participant),
                make_actor(),
            )
            service.add_member(
                COLLET_ID, binding_id, member_command(participant_ref=participant),
                make_actor(),
            )
        service.suspend_member(
            COLLET_ID, binding_id, member_command(participant_ref="u_member_002"),
            make_actor(),
        )
        stranger = make_actor("u_9999")
        revision = store.get_roster(AXES, binding_id, COLLET_ID).revision
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service.invite(
                COLLET_ID, binding_id, member_command(participant_ref="bot_new"),
                stranger,
            )
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service.add_member(
                COLLET_ID, binding_id, member_command(participant_ref="u_member_001"),
                stranger,
            )
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service.suspend_member(
                COLLET_ID, binding_id, member_command(participant_ref="u_member_001"),
                stranger,
            )
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service.resume_member(
                COLLET_ID, binding_id, member_command(participant_ref="u_member_002"),
                stranger,
            )
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service.remove_member(
                COLLET_ID, binding_id, member_command(participant_ref="u_member_001"),
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
            COLLET_ID, binding_id, member_command(participant_ref="u_member_001"),
            make_actor(),
        )
        service.add_member(
            COLLET_ID, binding_id, member_command(participant_ref="u_member_001"),
            make_actor(),
        )
        # 陌生人拒绝。
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service.leave(
                COLLET_ID, binding_id, member_command(participant_ref="u_member_001"),
                make_actor("u_9999"),
            )
        # owner 想替成员行 leave:PRINCIPAL_ID ≠ u_member_001,拒绝。
        with pytest.raises(ContextCollabValidationError, match=PRINCIPAL_ID):
            service.leave(
                COLLET_ID, binding_id, member_command(participant_ref="u_member_001"),
                make_actor(PRINCIPAL_ID),
            )
        left = service.leave(
            COLLET_ID, binding_id, member_command(participant_ref="u_member_001"),
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
            COLLET_ID, binding_id, member_command(participant_ref="u_member_001"),
            make_actor(),
        )
        service.add_member(
            COLLET_ID, binding_id, member_command(participant_ref="u_member_001"),
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
            COLLET_ID, binding_id, member_command(participant_ref="u_member_001"),
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


# ---------------------------------------------------------------------------
# project_created_context 的 roster + 初始成员投影(spec §9.2)
# ---------------------------------------------------------------------------


class TestCreatedContextProjection:
    def test_project_created_context_creates_roster_and_initial_members(self):
        store = InMemoryMethodStore()
        repo = _FakeCollabRepository({COLLET_ID: _status_record()})
        record = repo.get_current_status(COLLET_ID)
        binding = project_created_context(
            store, AXES, COLLET_ID, record.collet.context_spec,
            make_actor(), now=NOW,
        )
        assert "Group" in binding.method_names
        roster = store.get_roster(AXES, binding.method_binding_id, COLLET_ID)
        assert roster is not None
        assert roster.method_binding_id == binding.method_binding_id
        assert roster.revision == 3  # 1 + 两次成员插入 bump
        members = {
            m.participant_ref: m
            for m in store.list_members(
                AXES, binding.method_binding_id, COLLET_ID
            )
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
            store, AXES, COLLET_ID, record.collet.context_spec,
            make_actor(), now="2026-11-12T09:30:00+00:00",
        )
        assert again.method_binding_id == binding.method_binding_id
        assert (
            store.get_roster(AXES, binding.method_binding_id, COLLET_ID).revision
            == 3
        )
        assert len(store.list_members(
            AXES, binding.method_binding_id, COLLET_ID
        )) == 2

    def test_single_context_projects_no_roster(self):
        """Single 顶层方法无 Group 面向:不投影 roster/成员行。"""
        store = InMemoryMethodStore()
        repo = _FakeCollabRepository(
            {COLLET_ID: _status_record(method_kind="Single")}
        )
        record = repo.get_current_status(COLLET_ID)
        binding = project_created_context(
            store, AXES, COLLET_ID, record.collet.context_spec,
            make_actor(), now=NOW,
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
            COLLET_ID, binding_id, member_command(participant_ref="bot_x_001"),
            make_actor(),
        )
        service.add_member(
            COLLET_ID, binding_id, member_command(participant_ref="bot_x_001"),
            make_actor(),
        )
        service.leave(
            COLLET_ID, binding_id, member_command(participant_ref="bot_x_001"),
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
