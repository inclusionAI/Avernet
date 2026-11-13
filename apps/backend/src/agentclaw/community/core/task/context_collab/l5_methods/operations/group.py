"""Layer-owned Single/Group operations."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.method_state_machines import (
    METHOD_MACHINE_REGISTRY,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.state_machine import (
    resolve_transition,
)
from agentclaw.community.core.task.context_collab.l5_methods.group_profile import (
    MEMBER_TERMINAL_STATES,
    GroupMemberBinding,
    GroupRoster,
    MemberState,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    GroupChangeRoleCommand,
    GroupMemberCommand,
    GroupReplaceMemberCommand,
    ProfileOpAcceptedResult,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    _shape_of,
    current_axes,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    StoreAxes,
)

_GROUP_MACHINE = METHOD_MACHINE_REGISTRY["group_member"]

_GROUP_MEMBER_REQUEST_FIELDS = (
    "method_binding_id",
    "participant_ref",
    "address",
    "role_binding_refs",
    "transition_reason",
)


def _now() -> str:
    """UTC ISO 时间戳;与 lifecycle 服务同款,不注入时钟。"""
    return datetime.now(timezone.utc).isoformat()


def _resolve_member_state(from_state: str, event: str) -> str:
    """经 G4 机器裁决 group_member 迁移;无出边即校验失败。"""
    new_state = resolve_transition(_GROUP_MACHINE, from_state, event, {})
    if new_state is None:
        raise ContextCollabValidationError(
            f"group member state {from_state!r} does not accept event {event!r}"
        )
    return new_state


def _require_participant_ref(value: str) -> str:
    """group 成员参与者引用格式校验(非空前缀形状,spec §12 第 11 条);
    存在性校验是 P2。"""
    if not isinstance(value, str) or not value.strip():
        raise ContextCollabValidationError(
            f"participant_ref must be a non-empty string, got: {value!r}"
        )
    return value


def _require_handover_ref(value: str | None) -> str | None:
    """remove_member 的未完成工作转移目标(作者裁决):P1 只做格式校验
    (可缺省;给了就必须是非空串),不执行任何转移。"""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContextCollabValidationError(
            f"handover_to must be a non-empty string or null, got: {value!r}; "
            "P1 validates the reference shape only and performs no handover"
        )
    return value


class GroupOperations:
    def _ensure_roster(
        self, axes: StoreAxes, method_binding_id: str, collab_context_id: str
    ) -> GroupRoster:
        """惰性建 roster(嵌套 Group binding 无创建投影):每 binding 至多
        一份,确定性 id ``roster_<binding_id>``,初始 revision 1;既有即返回。"""
        roster = self._store.get_roster(axes, method_binding_id, collab_context_id)
        if roster is None:
            return self._store.insert_roster(
                axes,
                GroupRoster.from_dict(
                    {
                        "roster_id": f"roster_{method_binding_id}",
                        "method_binding_id": method_binding_id,
                        "revision": 1,
                    }
                ),
                collab_context_id,
            )
        return roster

    def _roster(
        self, axes: StoreAxes, method_binding_id: str, collab_context_id: str
    ) -> GroupRoster:
        """读 roster 并取 snapshot revision(成员写操作的乐观并发基准);
        roster 缺失即 NotFound(成员行必然建在 roster 之上)。"""
        roster = self._store.get_roster(axes, method_binding_id, collab_context_id)
        if roster is None:
            raise ContextCollabNotFoundError(
                f"group roster for method binding {method_binding_id!r} not "
                f"found in collaboration context {collab_context_id!r}"
            )
        return roster

    def _current_member(
        self,
        axes: StoreAxes,
        method_binding_id: str,
        collab_context_id: str,
        participant_ref: str,
    ) -> GroupMemberBinding:
        """定位参与者的非终态在册成员行(终态 LEFT/REMOVED/REPLACED 行
        保留审计、不是操作目标);缺失抛 NotFound。"""
        for member in reversed(
            self._store.list_members(axes, method_binding_id, collab_context_id)
        ):
            if (
                member.participant_ref == participant_ref
                and member.state not in MEMBER_TERMINAL_STATES
            ):
                return member
        raise ContextCollabNotFoundError(
            f"non-terminal group member {participant_ref!r} not found on "
            f"method binding {method_binding_id!r} in collaboration "
            f"context {collab_context_id!r}"
        )

    def _owner_gate(
        self,
        op: str,
        collab_context_id: str,
        method_binding_id: str,
        context: ActorContext,
    ) -> StoreAxes:
        """group owner 面操作共用前置:加载 → RF#2 → G1 授权
        (load→机器→写的完整链由各用例自行收尾)。"""
        axes = current_axes()
        binding = self._load_binding(axes, collab_context_id, method_binding_id)
        self._require_active_binding(op, binding)
        self._authorize(op, binding, context, collab_context_id)
        return axes

    def _transition_member(
        self,
        op: str,
        collab_context_id: str,
        method_binding_id: str,
        command: GroupMemberCommand,
        context: ActorContext,
        event: str,
        *,
        joined_now: bool,
        left_now: bool,
    ) -> ProfileOpAcceptedResult:
        """add/suspend/resume/leave/remove 共用编排:机器裁决 → 换新冻结
        实例(审计四字段 + joined_at/left_at)→ 存储锁内 revision 比对
        一次落定。leave 是 self-help(owner 面单列,与成员行比对授权)。"""
        participant_ref = _require_participant_ref(command.participant_ref)
        axes = current_axes()
        binding = self._load_binding(axes, collab_context_id, method_binding_id)
        self._require_active_binding(op, binding)
        if op == "leave":
            # self-help 先定位目标行再授权(G1 比对 member.participant_ref)。
            member = self._current_member(
                axes, method_binding_id, collab_context_id, participant_ref
            )
            self._authorize(op, binding, context, collab_context_id, member=member)
        else:
            self._authorize(op, binding, context, collab_context_id)
            member = self._current_member(
                axes, method_binding_id, collab_context_id, participant_ref
            )
        new_state = _resolve_member_state(member.state.value, event)
        now = _now()
        updated = replace(
            member,
            state=MemberState.from_wire(new_state),
            joined_at=now if joined_now else member.joined_at,
            left_at=now if left_now else member.left_at,
            transitioned_from=member.state.value,
            transition_reason=command.reason or event,
            transitioned_by=context.actor.principal_id,
            transitioned_at=now,
        )
        roster = self._roster(axes, method_binding_id, collab_context_id)
        self._store.update_member_state(
            axes, updated, roster.revision, collab_context_id
        )
        return ProfileOpAcceptedResult(
            accepted=True,
            collab_context_id=collab_context_id,
            method_binding_id=method_binding_id,
            object_kind="group_member",
            object_id=member.group_member_binding_id,
            state=new_state,
        )

    def invite(
        self,
        collab_context_id: str,
        method_binding_id: str,
        command: GroupMemberCommand,
        context: ActorContext,
    ) -> ProfileOpAcceptedResult:
        """Invite 用例:建 INVITED 成员行,不经迁移规则(机器初始态)。

        参与者已有非终态在册成员行即拒绝(存储层唯一断言);roster 缺失时
        惰性建(嵌套 Group binding);幂等键 ``member_binding_id``(§8.3
        同款:同 ID 同请求形态返回既有已受理对象,异形态 409 语义)。
        """
        participant_ref = _require_participant_ref(command.participant_ref)
        axes = self._owner_gate("invite", collab_context_id, method_binding_id, context)
        roster = self._ensure_roster(axes, method_binding_id, collab_context_id)
        now = _now()
        member = GroupMemberBinding.from_dict(
            {
                "group_member_binding_id": (
                    command.member_binding_id or f"gm_{uuid4().hex[:12]}"
                ),
                "method_binding_id": method_binding_id,
                "participant_ref": participant_ref,
                "address": command.address,
                "role_binding_refs": list(command.role_binding_refs),
                "state": MemberState.INVITED.value,
                "joined_at": None,
                "left_at": None,
                "transitioned_from": "",
                "transition_reason": command.reason or "group.invite",
                "transitioned_by": context.actor.principal_id,
                "transitioned_at": now,
            }
        )
        if command.member_binding_id is not None:
            existing = next(
                (
                    row
                    for row in self._store.list_members(
                        axes, method_binding_id, collab_context_id
                    )
                    if row.group_member_binding_id == command.member_binding_id
                ),
                None,
            )
            if existing is not None:
                # §8.3 幂等重放(与 select/bind 同款):同 ID 同"请求形态"
                # 返回既有已受理对象(不重写审计、不重复 bump)。
                if _shape_of(
                    existing.to_dict(), _GROUP_MEMBER_REQUEST_FIELDS
                ) == _shape_of(member.to_dict(), _GROUP_MEMBER_REQUEST_FIELDS):
                    return ProfileOpAcceptedResult(
                        accepted=True,
                        collab_context_id=collab_context_id,
                        method_binding_id=method_binding_id,
                        object_kind="group_member",
                        object_id=existing.group_member_binding_id,
                        state=existing.state.value,
                    )
                raise ContextCollabValidationError(
                    f"idempotent re-submit of group member "
                    f"{command.member_binding_id!r} differs from the stored "
                    "shape"
                )
        stored = self._store.insert_member(
            axes, member, roster.revision, collab_context_id
        )
        return ProfileOpAcceptedResult(
            accepted=True,
            collab_context_id=collab_context_id,
            method_binding_id=method_binding_id,
            object_kind="group_member",
            object_id=stored.group_member_binding_id,
            state=stored.state.value,
        )

    def add_member(
        self,
        collab_context_id: str,
        method_binding_id: str,
        command: GroupMemberCommand,
        context: ActorContext,
    ) -> ProfileOpAcceptedResult:
        """AddMember 用例:INVITED─group.add_member→ACTIVE,落 joined_at。"""
        return self._transition_member(
            "add_member",
            collab_context_id,
            method_binding_id,
            command,
            context,
            "group.add_member",
            joined_now=True,
            left_now=False,
        )

    def suspend_member(
        self,
        collab_context_id: str,
        method_binding_id: str,
        command: GroupMemberCommand,
        context: ActorContext,
    ) -> ProfileOpAcceptedResult:
        """SuspendMember 用例:ACTIVE─group.suspend_member→SUSPENDED。"""
        return self._transition_member(
            "suspend_member",
            collab_context_id,
            method_binding_id,
            command,
            context,
            "group.suspend_member",
            joined_now=False,
            left_now=False,
        )

    def resume_member(
        self,
        collab_context_id: str,
        method_binding_id: str,
        command: GroupMemberCommand,
        context: ActorContext,
    ) -> ProfileOpAcceptedResult:
        """ResumeMember 用例(作者裁决补漏):SUSPENDED─group.resume_member
        →ACTIVE,保证成员状态机全状态可达。"""
        return self._transition_member(
            "resume_member",
            collab_context_id,
            method_binding_id,
            command,
            context,
            "group.resume_member",
            joined_now=False,
            left_now=False,
        )

    def leave(
        self,
        collab_context_id: str,
        method_binding_id: str,
        command: GroupMemberCommand,
        context: ActorContext,
    ) -> ProfileOpAcceptedResult:
        """Leave 用例(成员本人自助,作者裁决):ACTIVE/SUSPENDED──
        group.leave→LEFT,落 left_at;授权比对 actor 与目标行
        ``participant_ref`` 字符串相等,owner 也不得替人退出。"""
        return self._transition_member(
            "leave",
            collab_context_id,
            method_binding_id,
            command,
            context,
            "group.leave",
            joined_now=False,
            left_now=True,
        )

    def remove_member(
        self,
        collab_context_id: str,
        method_binding_id: str,
        command: GroupMemberCommand,
        context: ActorContext,
    ) -> ProfileOpAcceptedResult:
        """RemoveMember 用例:ACTIVE/SUSPENDED──group.remove_member→
        REMOVED;``handover_to`` 是未完成工作转移目标,P1 只做格式校验
        不执行转移(作者裁决)。"""
        _require_handover_ref(command.handover_to)
        return self._transition_member(
            "remove_member",
            collab_context_id,
            method_binding_id,
            command,
            context,
            "group.remove_member",
            joined_now=False,
            left_now=True,
        )

    def replace_member(
        self,
        collab_context_id: str,
        method_binding_id: str,
        command: GroupReplaceMemberCommand,
        context: ActorContext,
    ) -> ProfileOpAcceptedResult:
        """ReplaceMember 用例:存储锁内一次落定旧 ACTIVE→REPLACED / 新成员
        →ACTIVE(单次 roster revision bump)。

        旧行经 ``group.replace_member`` 机器裁决(唯一出边在 ACTIVE),
        新行直接以 ACTIVE 创建(不经 INVITED);新参与者命中其他非终态
        在册成员由存储层拒绝;并发换手由锁内 revision 比对仲裁(409)。
        """
        old_participant = _require_participant_ref(command.old_participant_ref)
        new_participant = _require_participant_ref(command.new_participant_ref)
        axes = current_axes()
        binding = self._load_binding(axes, collab_context_id, method_binding_id)
        self._require_active_binding("replace_member", binding)
        self._authorize("replace_member", binding, context, collab_context_id)
        roster = self._roster(axes, method_binding_id, collab_context_id)
        old = self._current_member(
            axes, method_binding_id, collab_context_id, old_participant
        )
        replaced_state = _resolve_member_state(old.state.value, "group.replace_member")
        now = _now()
        old_replaced = replace(
            old,
            state=MemberState.from_wire(replaced_state),
            left_at=now,
            transitioned_from=old.state.value,
            transition_reason=command.reason or "group.replace_member",
            transitioned_by=context.actor.principal_id,
            transitioned_at=now,
        )
        new_member = GroupMemberBinding.from_dict(
            {
                "group_member_binding_id": f"gm_{uuid4().hex[:12]}",
                "method_binding_id": method_binding_id,
                "participant_ref": new_participant,
                "address": command.address,
                "role_binding_refs": list(command.role_binding_refs),
                "state": MemberState.ACTIVE.value,
                "joined_at": now,
                "left_at": None,
                "transitioned_from": "",
                "transition_reason": command.reason or "group.replace_member",
                "transitioned_by": context.actor.principal_id,
                "transitioned_at": now,
            }
        )
        self._store.replace_member(
            axes, old_replaced, new_member, roster.revision, collab_context_id
        )
        return ProfileOpAcceptedResult(
            accepted=True,
            collab_context_id=collab_context_id,
            method_binding_id=method_binding_id,
            object_kind="group_member",
            object_id=new_member.group_member_binding_id,
            state=new_member.state.value,
        )

    def change_role(
        self,
        collab_context_id: str,
        method_binding_id: str,
        command: GroupChangeRoleCommand,
        context: ActorContext,
    ) -> ProfileOpAcceptedResult:
        """ChangeRole 用例(owner-only,不经自助路径——成员自改
        role_binding_refs 属提权,2026-11-12 复审裁决收窄)。

        只写 ``role_binding_refs``(格式校验经 ``from_dict`` 整形重验),
        无状态迁移、审计四字段不覆写;``expected_revision`` 与存储锁内
        roster revision 比对,不匹配抛 409 语义,roster revision +1。
        """
        participant_ref = _require_participant_ref(command.participant_ref)
        axes = self._owner_gate(
            "change_role", collab_context_id, method_binding_id, context
        )
        member = self._current_member(
            axes, method_binding_id, collab_context_id, participant_ref
        )
        updated = GroupMemberBinding.from_dict(
            {
                **member.to_dict(),
                "role_binding_refs": list(command.role_binding_refs),
            }
        )
        self._store.update_member_state(
            axes, updated, command.expected_revision, collab_context_id
        )
        return ProfileOpAcceptedResult(
            accepted=True,
            collab_context_id=collab_context_id,
            method_binding_id=method_binding_id,
            object_kind="group_member",
            object_id=member.group_member_binding_id,
            state=member.state.value,
        )
