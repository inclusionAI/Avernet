"""L5 方法存储 group roster/member 切片(Task 10,spec §4.2)。

键位纪律:roster 键四段——每 (context, binding) 至多一份 roster,
method_binding_id 即 roster 域键,roster_id 只是行内幂等事实;group
member 键五段与 selection 键同形,成员 id 段是
(method_binding_id, group_member_binding_id) 复合。roster 乐观并发
经 ``_bump_roster_revision_locked`` 锁内比对承载(spec §4.2 RF #5,
revision 不匹配抛 ``ContextCollabVersionConflict``),并对参与者强制
"非终态不得重复在册"。方法体自 ``method_store.py`` 原样搬迁(纯文件
拆分,零行为变更)。
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.l5_methods.group_profile import (
    MEMBER_TERMINAL_STATES,
    GroupMemberBinding,
    GroupRoster,
)

if TYPE_CHECKING:  # pragma: no cover - 仅供注解,type check 期解析
    from agentclaw.community.core.task.context_collab.persistence.method_store import (
        StoreAxes,
    )

_RosterKey = tuple[str, str, str, str]
_GroupMemberKey = tuple[str, str, str, str, str]


class GroupSlice:
    """``MethodStoreProtocol`` 的 group roster/member 切片实现。"""

    def insert_roster(
        self,
        axes: "StoreAxes",
        roster: GroupRoster,
        collab_context_id: str,
    ) -> GroupRoster:
        """幂等插入 roster:同 (context, binding) 同 ``to_dict`` 返回既有
        实例;已有 roster 时异形态(含换 roster_id)一律拒绝——每 binding
        至多一份 roster,乐观并发语义假定单一 revision 权威行。"""
        key = (axes.avernet_tenant, axes.env, collab_context_id,
               roster.method_binding_id)
        with self._lock:
            existing = self._rosters.get(key)
            if existing is not None:
                if existing.to_dict() == roster.to_dict():
                    return existing
                raise ContextCollabValidationError(
                    f"a roster already exists for method binding "
                    f"{roster.method_binding_id!r} in collaboration context "
                    f"{collab_context_id!r} (roster {existing.roster_id!r}); "
                    f"idempotent re-submit of roster {roster.roster_id!r} "
                    "differs from the stored shape"
                )
            self._rosters[key] = roster
            return roster

    def get_roster(
        self,
        axes: "StoreAxes",
        method_binding_id: str,
        collab_context_id: str,
    ) -> GroupRoster | None:
        key = (axes.avernet_tenant, axes.env, collab_context_id,
               method_binding_id)
        with self._lock:
            return self._rosters.get(key)

    def bump_roster_revision(
        self,
        axes: "StoreAxes",
        method_binding_id: str,
        expected_revision: int,
        collab_context_id: str,
    ) -> GroupRoster:
        """锁内 revision 比对并 +1(RF #5):roster 缺失抛
        ``ContextCollabNotFoundError``,revision 不匹配抛
        ``ContextCollabVersionConflict``,roster 不被静默改写。"""
        with self._lock:
            return self._bump_roster_revision_locked(
                axes, collab_context_id, method_binding_id, expected_revision
            )

    def insert_member(
        self,
        axes: "StoreAxes",
        member: GroupMemberBinding,
        expected_revision: int,
        collab_context_id: str,
    ) -> GroupMemberBinding:
        """幂等插入成员行 + revision 比对 + 非终态参与者唯一断言。

        同键(tenant, env, collab_context_id, method_binding_id,
        group_member_binding_id)同 ``to_dict`` 返回既有实例、不重复
        bump;同键异形态抛 ``ContextCollabValidationError``。新行若与
        非终态在册成员同 ``participant_ref`` 拒绝(终态 LEFT/REMOVED/
        REPLACED 成员可再入)。插入成功与 roster revision +1 在同一
        把锁内一次落定。
        """
        key = self._member_key(axes, collab_context_id, member)
        with self._lock:
            existing = self._members.get(key)
            if existing is not None:
                if existing.to_dict() == member.to_dict():
                    return existing
                raise ContextCollabValidationError(
                    f"idempotent re-submit of group member "
                    f"{member.group_member_binding_id!r} differs from the "
                    "stored shape"
                )
            self._reject_member_duplicate_locked(
                axes, collab_context_id,
                member.method_binding_id, member.participant_ref,
                exclude_id=None,
            )
            self._bump_roster_revision_locked(
                axes, collab_context_id, member.method_binding_id,
                expected_revision,
            )
            self._members[key] = member
            return member

    def update_member_state(
        self,
        axes: "StoreAxes",
        member: GroupMemberBinding,
        expected_revision: int,
        collab_context_id: str,
    ) -> None:
        """锁内覆盖成员行实例 + revision 比对一次落定。

        成员状态迁移(add/suspend/resume/leave/remove)与 ``change_role``
        的 role_binding_refs 改写同经此口换新实例——revision 不匹配抛
        ``ContextCollabVersionConflict``;键必须已存在,否则抛
        ``ContextCollabNotFoundError``。
        """
        key = self._member_key(axes, collab_context_id, member)
        with self._lock:
            if key not in self._members:
                raise ContextCollabNotFoundError(
                    f"group member {member.group_member_binding_id!r} not "
                    f"found on method binding {member.method_binding_id!r} "
                    f"in collaboration context {collab_context_id!r}"
                )
            self._bump_roster_revision_locked(
                axes, collab_context_id, member.method_binding_id,
                expected_revision,
            )
            self._members[key] = member

    def replace_member(
        self,
        axes: "StoreAxes",
        old: GroupMemberBinding,
        new: GroupMemberBinding,
        expected_revision: int,
        collab_context_id: str,
    ) -> None:
        """锁内原子换人(spec §4.2 ReplaceMember):单次 revision bump。

        前置:``old`` / ``new`` 必须同 ``method_binding_id`` 且成员 id
        相异。锁内裁决:``old`` 键必须仍在库,否则抛
        ``ContextCollabNotFoundError``;新参与者不得命中其他非终态在
        册成员(排除即将转终态的 ``old`` 行);revision 比对通过后一次
        写两条——``old`` 键位落其 REPLACED 实例、``new`` 键位落其
        ACTIVE 实例,roster revision 恰 +1。
        """
        if new.method_binding_id != old.method_binding_id:
            raise ContextCollabValidationError(
                f"replace_member requires the same method_binding_id: "
                f"old={old.method_binding_id!r}, "
                f"new={new.method_binding_id!r}"
            )
        if new.group_member_binding_id == old.group_member_binding_id:
            raise ContextCollabValidationError(
                f"replace_member requires a distinct "
                f"group_member_binding_id for the new member, got "
                f"{new.group_member_binding_id!r} twice"
            )
        old_key = self._member_key(axes, collab_context_id, old)
        with self._lock:
            if old_key not in self._members:
                raise ContextCollabNotFoundError(
                    f"group member {old.group_member_binding_id!r} not found "
                    f"on method binding {old.method_binding_id!r} in "
                    f"collaboration context {collab_context_id!r}"
                )
            self._reject_member_duplicate_locked(
                axes, collab_context_id, new.method_binding_id,
                new.participant_ref,
                exclude_id=old.group_member_binding_id,
            )
            self._bump_roster_revision_locked(
                axes, collab_context_id, old.method_binding_id,
                expected_revision,
            )
            self._members[old_key] = old
            self._members[
                self._member_key(axes, collab_context_id, new)
            ] = new

    def list_members(
        self,
        axes: "StoreAxes",
        method_binding_id: str,
        collab_context_id: str,
    ) -> list[GroupMemberBinding]:
        """按插入序返回该 axes + context + binding 下的全部成员行,
        不做状态过滤。"""
        with self._lock:
            return self._context_members_locked(
                axes, collab_context_id, method_binding_id
            )

    # ------------------------------------------------------------------
    # 内部:键构造与锁内扫描
    # ------------------------------------------------------------------

    @staticmethod
    def _member_key(
        axes: "StoreAxes", collab_context_id: str, member: GroupMemberBinding
    ) -> _GroupMemberKey:
        # 成员 id 只在 binding 域内充当幂等键,id 段取 (binding, member)
        # 复合——成员行按 collab_context_id 锚定,跨上下文互不可见。
        return (
            axes.avernet_tenant,
            axes.env,
            collab_context_id,
            member.method_binding_id,
            member.group_member_binding_id,
        )

    def _context_members_locked(
        self,
        axes: "StoreAxes",
        collab_context_id: str,
        method_binding_id: str,
    ) -> list[GroupMemberBinding]:
        """锁内扫描:该 axes + context + binding 下的全部成员行,
        保持插入序。"""
        return [
            member
            for key, member in self._members.items()
            if key[0] == axes.avernet_tenant
            and key[1] == axes.env
            and key[2] == collab_context_id
            and key[3] == method_binding_id
        ]

    def _bump_roster_revision_locked(
        self,
        axes: "StoreAxes",
        collab_context_id: str,
        method_binding_id: str,
        expected_revision: int,
    ) -> GroupRoster:
        """锁内 revision 比对并 +1;调用方必须已持 store 锁。"""
        key = (axes.avernet_tenant, axes.env, collab_context_id,
               method_binding_id)
        roster = self._rosters.get(key)
        if roster is None:
            raise ContextCollabNotFoundError(
                f"group roster for method binding {method_binding_id!r} not "
                f"found in collaboration context {collab_context_id!r}"
            )
        if roster.revision != expected_revision:
            raise ContextCollabVersionConflict(
                f"group roster for method binding {method_binding_id!r} is at "
                f"revision {roster.revision}, expected {expected_revision!r}"
            )
        bumped = replace(roster, revision=roster.revision + 1)
        self._rosters[key] = bumped
        return bumped

    def _reject_member_duplicate_locked(
        self,
        axes: "StoreAxes",
        collab_context_id: str,
        method_binding_id: str,
        participant_ref: str,
        exclude_id: str | None,
    ) -> None:
        """锁内唯一断言:同参与者已有非终态在册成员行即拒绝(invite 与
        ReplaceMember 新行共用;换人场景排除即将转终态的旧行)。"""
        for stored in self._context_members_locked(
            axes, collab_context_id, method_binding_id
        ):
            if (
                stored.participant_ref == participant_ref
                and stored.state not in MEMBER_TERMINAL_STATES
                and stored.group_member_binding_id != exclude_id
            ):
                raise ContextCollabValidationError(
                    f"participant {participant_ref!r} is already in the "
                    f"roster of method binding {method_binding_id!r} in a "
                    f"non-terminal state ({stored.state.value!r}); only "
                    "terminal members can re-enter"
                )
