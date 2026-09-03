"""Repository contracts owned by the ``skill_center`` domain.
Moved here by the ``core/repository`` consolidation. Every member is
``@abstractmethod``: an implementation that omits one fails at construction
naming the missing member, instead of raising ``AttributeError`` at the call
site. Domain imports are ``TYPE_CHECKING``-only — see the module docstring in
``core/repository/README.md`` for why that direction is load-bearing.
"""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Iterable
from typing import Any, List, Optional, Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.skill_center.materialization_contract import (
        MaterializingSkillVersion,
        PublishedMaterializedSkillVersion,
    )
    from agentclaw.community.core.work_orders.models import (
        WorkOrderNotificationDraft,
        WorkOrderRecord,
        WorkOrderReviewResult,
        WorkOrderStatus,
    )

from .skill_center_types import (
    SpaceCreateData,
    SpaceRecord,
    SpaceSkillCreateData,
    SpaceSkillCreationRecord,
    SpaceSkillCreationReplayRecord,
    SpaceSkillOwnerGrantData,
    SpaceSkillOwnershipData,
    SpaceSkillQueryRecord,
    SpaceSkillReadRecord,
    SpaceSkillGrantItem,
    SpaceSkillGrantSetRecord,
    DraftEditLeaseRecord,
    SkillVersionRecord,
    SpaceSkillDraftRecord,
    DraftDeleteRecord,
    DraftUpgradeRecord,
    SkillUpgradeIdentityRecord,
    SkillUpgradeRequestRecord,
)


@runtime_checkable
class SpaceSkillRepository(Protocol):
    """Tenant-scoped persistence seam for additive Space Skill facts."""

    @abstractmethod
    def create_space(self, data: SpaceCreateData) -> SpaceRecord: ...

    @abstractmethod
    def get_space(self, space_id: int, *, env: str) -> SpaceRecord | None: ...

    @abstractmethod
    def create_space_skill(
        self,
        *,
        skill_data: SpaceSkillCreateData,
        ownership_data: SpaceSkillOwnershipData,
        owner_grant_data: SpaceSkillOwnerGrantData,
    ) -> SpaceSkillCreationRecord:
        """Atomically persist the initial identity, ownership and owner grant."""
        ...

    @abstractmethod
    def get_creation_by_request_id(
        self, *, request_id: str, env: str
    ) -> SpaceSkillCreationReplayRecord | None:
        """Resolve the resource permanently bound to one creation request."""
        ...

    @abstractmethod
    def list_space_skills(
        self,
        *,
        space_id: int,
        actor_id: str,
        env: str,
        keyword: str | None,
        offset: int,
        limit: int,
    ) -> tuple[int, list[SpaceSkillQueryRecord]]:
        """Return a stable, database-paginated Space Skill projection."""
        ...

    @abstractmethod
    def list_grants(
        self, *, space_id: int, skill_id: int, actor_id: str, env: str
    ) -> SpaceSkillGrantSetRecord: ...

    @abstractmethod
    def get_active_role(
        self, *, space_id: int, skill_id: int, actor_id: str, env: str
    ) -> str | None: ...

    @abstractmethod
    def add_manager(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        manager_user_id: str,
        env: str,
    ) -> SpaceSkillGrantItem: ...

    @abstractmethod
    def remove_manager(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        manager_user_id: str,
        env: str,
    ) -> SpaceSkillGrantItem: ...

    @abstractmethod
    def transfer_owner(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        new_owner_user_id: str,
        reason: str | None,
        env: str,
        retain_previous_owner_as_manager: bool = False,
    ) -> SpaceSkillGrantSetRecord: ...


@runtime_checkable
class SpaceSkillReadRepository(Protocol):
    """Read model for Space Skill workshop summaries and details."""

    @abstractmethod
    def list_skills(
        self,
        *,
        space_id: int,
        actor_id: str,
        env: str,
        keyword: str | None,
        offset: int,
        limit: int,
    ) -> tuple[int, list[SpaceSkillReadRecord]]: ...

    @abstractmethod
    def get_skill(
        self, *, space_id: int, skill_id: int, actor_id: str, env: str
    ) -> SpaceSkillReadRecord: ...


@runtime_checkable
class SkillEditorRequestRepositoryProtocol(Protocol):
    """Atomic Skill-owned seam spanning editor requests and their Work Orders."""

    @abstractmethod
    def create_skill_editor_request(
        self,
        *,
        space_id: int,
        skill_id: int,
        applicant_user_id: str,
        applicant_name: str,
        apply_reason: str,
        env: str,
    ) -> WorkOrderRecord: ...

    @abstractmethod
    def review_skill_editor_request(
        self,
        *,
        work_order_id: int,
        reviewer_user_id: str,
        review_remark: str | None,
        target_status: WorkOrderStatus,
        notification: WorkOrderNotificationDraft,
        env: str,
    ) -> WorkOrderReviewResult: ...

    @staticmethod
    @abstractmethod
    def reroute_pending_reviewer(
        session: Any,
        *,
        skill_id: int,
        previous_owner_user_id: str,
        new_owner_user_id: str,
        env: str,
    ) -> None: ...


@runtime_checkable
class DraftEditLeaseRepository(Protocol):
    """Repository contract for a permanent, fenced Team Draft Lease.

    A future Draft-content write must add one aggregate command here that checks
    the holder/token and writes content metadata in the same transaction.  This
    contract deliberately exposes no validate-then-write operation: a takeover
    between those two calls would defeat fencing.
    """

    @abstractmethod
    def get_lease(
        self, *, space_id: int, skill_id: int, env: str
    ) -> DraftEditLeaseRecord | None: ...

    @abstractmethod
    def acquire(
        self, *, space_id: int, skill_id: int, actor_id: str, env: str
    ) -> DraftEditLeaseRecord: ...

    @abstractmethod
    def release(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        fencing_token: int,
        env: str,
    ) -> DraftEditLeaseRecord: ...

    @abstractmethod
    def takeover(
        self, *, space_id: int, skill_id: int, actor_id: str, env: str
    ) -> DraftEditLeaseRecord: ...


@runtime_checkable
class SpaceSkillDraftRepository(Protocol):
    """Atomic Draft revision and fencing persistence seam."""

    @abstractmethod
    def get_draft(
        self, *, space_id: int, skill_id: int, env: str
    ) -> SpaceSkillDraftRecord: ...

    @abstractmethod
    def get_draft_for_mutation(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        expected_revision_id: str,
        fencing_token: int | None,
        env: str,
    ) -> SpaceSkillDraftRecord:
        """Preauthorize editor, revision and fencing before external Draft I/O."""
        ...

    @abstractmethod
    def replace_draft_revision(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        expected_revision_id: str,
        fencing_token: int | None,
        new_locator: str,
        new_description: str,
        source_commit_sha: str | None = None,
        env: str,
    ) -> str:
        """Commit one EDITING revision CAS and return the previous locator."""
        ...

    @abstractmethod
    def delete_draft(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        expected_revision_id: str,
        fencing_token: int | None,
        env: str,
    ) -> DraftDeleteRecord: ...

    @abstractmethod
    def get_skill_for_upgrade(
        self, *, space_id: int, skill_id: int, actor_id: str, env: str
    ) -> SkillUpgradeIdentityRecord: ...

    @abstractmethod
    def get_upgrade_by_request_id(
        self, *, request_id: str, env: str
    ) -> SkillUpgradeRequestRecord | None: ...

    @abstractmethod
    def create_upgrade_draft(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        request_id: str,
        expected_version_id: int,
        target_version: int,
        new_locator: str,
        new_description: str,
        env: str,
    ) -> DraftUpgradeRecord: ...


@runtime_checkable
class SkillVersionRepositoryProtocol(Protocol):
    """Read-only persistence seam for immutable published Skill Versions."""

    @abstractmethod
    def list_latest_published(
        self, *, env: str, skill_ids: tuple[int, ...]
    ) -> tuple[SkillVersionRecord, ...]:
        """Return at most one highest-ordinal PUBLISHED row per Skill."""
        ...

    @abstractmethod
    def get_exact_published(
        self, *, env: str, skill_id: int, skill_version_id: int
    ) -> SkillVersionRecord | None:
        """Return the addressed PUBLISHED row, never MATERIALIZING."""
        ...

    @abstractmethod
    def get_published_by_ordinal(
        self, *, env: str, skill_id: int, version_ordinal: int
    ) -> SkillVersionRecord | None:
        """Return one exact PUBLISHED business Version for Copy."""
        ...


@runtime_checkable
class SkillVersionMaterializationRepositoryProtocol(Protocol):
    """Persistence boundary for exact-Version Ready-Gate publication."""

    @abstractmethod
    def get_materialization_target(
        self, *, env: str, skill_id: int, skill_version_id: int
    ) -> MaterializingSkillVersion | None: ...

    @abstractmethod
    def publish_materialized(
        self,
        *,
        env: str,
        skill_id: int,
        skill_version_id: int,
        name: str,
        metadata_json: str,
        description: str,
    ) -> PublishedMaterializedSkillVersion: ...


@runtime_checkable
class SkillRepository(Protocol):
    """技能 Repository 接口"""

    @abstractmethod
    def get_by_id(self, skill_id: str) -> dict | None: ...

    @abstractmethod
    def get_by_uuid(self, skill_uuid: str, env: str | None = None) -> dict | None:
        """根据 skill_uuid（UUID 字符串）查询技能记录。"""
        ...

    @abstractmethod
    def get_by_git_path(self, git_path: str) -> dict | None: ...

    @abstractmethod
    def get_by_link_name(
        self, link_name: str, bolt_id: str | None = None
    ) -> dict | None: ...

    @abstractmethod
    def list_skills(
        self,
        user_id: str | None = None,
        bolt_id: str | None = "default",
        env: str | None = None,
    ) -> list[dict]: ...

    @abstractmethod
    def get_bot_local_by_name(
        self,
        *,
        bot_id: str,
        name: str,
        user_id: str | None = None,
    ) -> dict | None:
        """精确查询 Bot 自有的同名 local 技能，不包含全局行。"""
        ...

    @abstractmethod
    def list_bot_local_by_name(self, *, bot_id: str, name: str) -> list[dict]:
        """Return every exact Bot-local same-name row for ambiguity handling.

        The caller resolves legacy owner semantics after it holds the Bot Skill
        layout edit lock; this method deliberately does not pick a winner.
        """
        ...

    @abstractmethod
    def get_bot_local_by_locator(
        self, *, bot_id: str, user_id: str, locator: str
    ) -> dict | None:
        """Return the current exact Bot-owned Local Skill for one locator."""
        ...

    @abstractmethod
    def list_bot_skills(
        self,
        *,
        bot_id: str,
        user_id: str,
        skill_set_member_ids: Iterable[int],
        page: int,
        page_size: int,
        active: bool | None,
        keyword: str | None,
        source: str | None = None,
    ) -> tuple[int, list[dict]]:
        """Page desired-state metadata for every Skill one Bot reaches.

        ``skill_set_member_ids`` are the Skills a SkillSet bridges to the Bot,
        resolved by the SkillSet control plane; Bot-owned rows are found here.
        ``source=LOCAL`` narrows to Bot-owned ``local://`` rows only.
        """
        ...

    @abstractmethod
    def get_bot_local_skill(
        self, *, skill_id: str, bot_id: str, user_id: str
    ) -> dict | None:
        """Return one exact Bot-owned ``local://`` Skill with desired state."""
        ...

    @abstractmethod
    def list_bot_installed_skills(
        self, *, env: str, owner_id: str, bot_id: str
    ) -> list[dict]:
        """Return active-only Installation assets for one Bot."""
        ...

    @abstractmethod
    def create(self, skill_data: dict) -> dict: ...

    @abstractmethod
    def update(self, skill_id: str, skill_data: dict) -> dict | None: ...

    @abstractmethod
    def replace_bot_local_skill(
        self,
        *,
        skill_id: str,
        owner_id: str,
        bot_id: str,
        old_locator: str,
        new_locator: str,
        description: str,
    ) -> dict | None:
        """Atomically switch package authority at a canonical locator."""
        ...

    @abstractmethod
    def delete(self, skill_id: str) -> bool: ...

    @abstractmethod
    def list_skill_set_references(
        self,
        skill_id: str,
        skill_uuid: str | None = None,
    ) -> list[dict]:
        """Return every current-environment SkillSet association for a Skill."""

    @abstractmethod
    def delete_bot_local_skill(
        self,
        *,
        skill_id: str,
        owner_id: str,
        bot_id: str,
    ) -> bool | None:
        """Atomically delete scoped Local Skill state."""
        ...

    @abstractmethod
    def check_skill_blocked_by_bot(
        self, name: str, env: str | None = None
    ) -> list[str]:
        """Return bot ids whose active skill-sets reference this skill
        (deletion blockers); empty list if none."""
        ...

    @abstractmethod
    def delete_by_name_with_cascade(self, name: str, env: str | None = None) -> dict:
        """Delete the skill by name and cascade related rows; returns a
        summary dict of what was removed."""
        ...

    @abstractmethod
    def update_risk_tags(self, skill_id: str, risk_tags: list) -> dict | None: ...

    @abstractmethod
    def update_mcp_dependencies(
        self, skill_id: str, mcp_dependencies: list
    ) -> dict | None: ...

    @abstractmethod
    def get_by_name_global_include_deleted(
        self, name: str, user_id: str | None = None
    ) -> dict | None:
        """根据技能名称全局查询（包括已删除 Bot 的技能），用于复用已删除记录避免唯一约束冲突。"""
        ...

    @abstractmethod
    def delete_by_bot_id(self, bot_id: str) -> int:
        """Delete all skills associated with a bot.

        Args:
            bot_id: Bot ID (maps to bolt_id column)

        Returns:
            Number of deleted records
        """
        ...

    @abstractmethod
    def get_active_skills_by_bot(
        self,
        bot_id: str,
        entity_type: str,
        owner_id: str,
    ) -> list[dict]:
        """查询指定 bot 的所有 active skills。

        通过 skill_set_skill 关联表 join skill_set，条件：
        - skill_set.bolt_id == bot_id
        - skill_set.is_active == True

        Returns:
            [{"id": ..., "name": ..., "git_path": ..., "link_name": ...}, ...]
        """
        ...


@runtime_checkable
class SkillSetRepository(Protocol):
    """技能集 Repository 接口"""

    @abstractmethod
    def get_by_id(self, skill_set_id: str) -> dict | None: ...

    @abstractmethod
    def get_default(
        self,
        user_id: str | None = None,
        bolt_id: str | None = None,
        engine_type: str | None = None,
    ) -> dict | None: ...

    @abstractmethod
    def list_all(
        self,
        user_id: str | None = None,
        bolt_id: str | None = None,
        engine_type: str | None = None,
        default_skill_set_bolt_id: str | None = None,
        default_skill_set_engine_type: str | None = None,
    ) -> list[dict]: ...

    @abstractmethod
    def create(self, skill_set_data: dict) -> dict: ...

    @abstractmethod
    def update(self, skill_set_id: str, skill_set_data: dict) -> dict | None: ...

    @abstractmethod
    def delete(self, skill_set_id: str) -> bool: ...

    @abstractmethod
    def add_skill_to_set(
        self, skill_set_id: str, skill_id: str, user_id: str | None = None
    ) -> bool: ...

    @abstractmethod
    def get_skills_in_set(self, skill_set_id: str) -> list[dict]: ...

    @abstractmethod
    def find_affected_bots_by_skill_uuid(
        self,
        skill_uuid: str,
        env: str | None = None,
    ) -> list[dict]:
        """找出所有受 skill_uuid 升级影响的 bot。

        语义：只返回 普通 active SkillSet（is_default=0 且 is_active=1，bolt_id 非空）
        所属、且未删除的 bot。default SkillSet 不主动传播，依赖会话启动兜底。

        engine_type 取自 ac_bots.active_engine（source of truth），不取 SkillSet 自带值。

        返回每条 dict：{"bot_id", "active_engine", "owner_id"}
        """
        ...

    @abstractmethod
    def remove_skill_from_set(self, skill_set_id: str, skill_id: str) -> bool: ...

    @abstractmethod
    def add_default_skill_exclusion(
        self, user_id: str, bot_id: str, skill_set_id: int, skill_id: int
    ) -> bool: ...

    @abstractmethod
    def remove_default_skill_exclusion(
        self, user_id: str, bot_id: str, skill_set_id: int, skill_id: int
    ) -> bool: ...

    @abstractmethod
    def remove_all_default_skill_exclusions(
        self, user_id: str, bot_id: str, skill_id: int
    ) -> bool:
        """Clear every default-set exclusion for one Bot-owned Skill."""
        ...

    @abstractmethod
    def add_mcp_to_set(
        self,
        skill_set_id: str,
        server_code: str,
        name: str,
        description: str | None = None,
        icon: str | None = None,
        user_id: str | None = None,
        env: str | None = None,
    ) -> bool: ...

    @abstractmethod
    def get_mcp_servers_in_set(self, skill_set_id: str) -> list[dict]: ...

    @abstractmethod
    def get_mcp_servers_in_set_for_env(
        self, skill_set_id: str, *, env: str
    ) -> list[dict]:
        """Return associations belonging to one explicit environment."""
        ...

    @abstractmethod
    def remove_mcp_from_set(self, skill_set_id: str, server_code: str) -> bool: ...

    @abstractmethod
    def get_active_skill_set(
        self,
        user_id: str | None = None,
        bolt_id: str | None = None,
        engine_type: str | None = None,
    ) -> dict | None: ...

    @abstractmethod
    def set_active_skill_set(
        self,
        skill_set_id: str,
        user_id: str | None = None,
        bolt_id: str | None = None,
        engine_type: str | None = None,
    ) -> bool: ...

    @abstractmethod
    def clear_active_skill_set(
        self,
        user_id: str | None = None,
        bolt_id: str | None = None,
        engine_type: str | None = None,
    ) -> bool: ...

    @abstractmethod
    def get_all_active_skill_sets(
        self,
        user_id: str | None = None,
        bolt_id: str | None = None,
        engine_type: str | None = None,
        default_skill_set_bolt_id: str | None = None,
        default_skill_set_engine_type: str | None = None,
    ) -> list[dict]: ...

    @abstractmethod
    def get_all_active_skill_sets_for_env(
        self,
        *,
        user_id: str | None = None,
        bolt_id: str | None = None,
        engine_type: str | None = None,
        env: str,
        default_skill_set_bolt_id: str | None = None,
        default_skill_set_engine_type: str | None = None,
    ) -> list[dict]:
        """Return active sets using an explicit environment, never runtime env."""
        ...

    @abstractmethod
    def activate_skill_set(
        self,
        skill_set_id: str,
        user_id: str | None = None,
        bolt_id: str | None = None,
        engine_type: str | None = None,
    ) -> bool: ...

    @abstractmethod
    def deactivate_skill_set(
        self,
        skill_set_id: str,
        user_id: str | None = None,
        bolt_id: str | None = None,
        engine_type: str | None = None,
    ) -> bool: ...

    @abstractmethod
    def activate_default_skillset(self) -> int: ...

    @abstractmethod
    def list_all_exclude_deleted(
        self,
        user_id: str | None = None,
        bolt_id: str | None = None,
        engine_type: str | None = None,
        default_skill_set_bolt_id: str | None = None,
        default_skill_set_engine_type: str | None = None,
    ) -> list[dict]:
        """列出所有技能集（排除已删除 Bot 的），用于重名检查避免误判。"""
        ...

    @abstractmethod
    def get_skill_set_by_name_include_deleted(
        self, name: str, user_id: str, bolt_id: str | None = None
    ) -> dict | None:
        """根据名称查找技能集（包括已删除 Bot 的），用于复用记录避免唯一约束冲突。

        Args:
            name: 技能集名称
            user_id: 用户 ID
            bolt_id: Bot ID，用于区分不同 bot 下的同名技能集
        """
        ...

    @abstractmethod
    def get_all_user_mcps(self, user_id: str) -> list[dict]: ...

    @abstractmethod
    def delete_by_bot_id(self, bot_id: str) -> int:
        """Delete all skill sets and their associations for a bot.

        Deletes from junction tables (skill_set_skill, skill_set_mcp_server)
        and ac_skill_set.

        Args:
            bot_id: Bot ID (maps to bolt_id column)

        Returns:
            Number of deleted skill set records
        """
        ...


@runtime_checkable
class SkillMemberRepository(Protocol):
    """技能成员 Repository 接口

    管理技能的成员权限，包括添加/移除成员、更新角色、查询成员等。
    """

    @abstractmethod
    def get_members_by_skill_uuid(self, skill_uuid: str) -> List[dict]:
        """获取指定技能的所有成员

        Args:
            skill_uuid: 技能 UUID

        Returns:
            成员列表，每个成员包含：id, skill_uuid, user_id, role, gmt_create, gmt_modified
        """
        ...

    @abstractmethod
    def get_member(self, skill_uuid: str, user_id: str) -> Optional[dict]:
        """获取指定技能的单个成员信息

        Args:
            skill_uuid: 技能 UUID
            user_id: 用户 ID

        Returns:
            成员信息字典，不存在则返回 None
        """
        ...

    @abstractmethod
    def add_member(self, skill_uuid: str, user_id: str, role: str = "member") -> dict:
        """添加技能成员

        Args:
            skill_uuid: 技能 UUID
            user_id: 用户 ID
            role: 角色，可选 "admin" 或 "member"，默认 "member"

        Returns:
            新添加的成员信息字典

        Raises:
            ValueError: 如果成员已存在或角色不合法
        """
        ...

    @abstractmethod
    def remove_member(self, skill_uuid: str, user_id: str) -> bool:
        """移除技能成员

        Args:
            skill_uuid: 技能 UUID
            user_id: 用户 ID

        Returns:
            是否成功移除

        Raises:
            ValueError: 如果成员不存在
        """
        ...

    @abstractmethod
    def update_member_role(self, skill_uuid: str, user_id: str, role: str) -> dict:
        """更新成员角色

        Args:
            skill_uuid: 技能 UUID
            user_id: 用户 ID
            role: 新角色，"admin" 或 "member"

        Returns:
            更新后的成员信息字典

        Raises:
            ValueError: 如果成员不存在或角色不合法
        """
        ...

    @abstractmethod
    def is_member(self, skill_uuid: str, user_id: str) -> bool:
        """检查用户是否为技能成员

        Args:
            skill_uuid: 技能 UUID
            user_id: 用户 ID

        Returns:
            是否为成员
        """
        ...

    @abstractmethod
    def get_member_role(self, skill_uuid: str, user_id: str) -> Optional[str]:
        """获取用户在指定技能中的角色

        Args:
            skill_uuid: 技能 UUID
            user_id: 用户 ID

        Returns:
            角色字符串 ("admin" | "member")，不是成员则返回 None
        """
        ...

    @abstractmethod
    def get_skill_uuids_by_user_id(self, user_id: str) -> List[str]:
        """获取用户参与的所有技能 UUID 列表

        Args:
            user_id: 用户 ID

        Returns:
            该用户参与的所有技能 UUID 列表
        """
        ...

    @abstractmethod
    def has_admin_role(self, skill_uuid: str, user_id: str) -> bool:
        """检查用户是否为技能管理员

        Args:
            skill_uuid: 技能 UUID
            user_id: 用户 ID

        Returns:
            是否为管理员
        """
        ...

    @abstractmethod
    def get_members_by_skill_uuids(self, skill_uuids: List[str]) -> dict:
        """批量查询多个技能的成员

        Args:
            skill_uuids: 技能 UUID 列表

        Returns:
            字典 {skill_uuid: [{"user_id": str, "role": str}, ...]}
        """
        ...


@runtime_checkable
class SkillCategoryRepository(Protocol):
    """技能类目 Repository 接口

    管理技能类目的 CRUD 和树形结构查询。
    """

    @abstractmethod
    def list_active(self) -> list[dict]:
        """列出所有启用的类目（status=1），按 level、sort_order 排序。"""
        ...

    @abstractmethod
    def get_by_code(self, code: str) -> Optional[dict]:
        """根据 code 获取类目。"""
        ...

    @abstractmethod
    def create(
        self,
        code: str,
        name: str,
        parent_code: str,
        path: str,
        level: int,
        sort_order: int,
    ) -> dict:
        """创建类目，返回创建后的 dict。"""
        ...

    @abstractmethod
    def update(self, code: str, **fields) -> Optional[dict]:
        """更新类目字段，返回更新后的 dict，不存在则返回 None。"""
        ...

    @abstractmethod
    def list_descendant_codes(self, path: str) -> list[str]:
        """列出 path 前缀匹配的所有启用类目的 code（含自身）。"""
        ...
        ...


@runtime_checkable
class SkillCenterSyncLogRepository(Protocol):
    """SC 同步日志 repository 协议（local SQLite / prod 关系存储实现）。"""

    @abstractmethod
    def create(self, data: dict) -> dict: ...

    @abstractmethod
    def mark_success(
        self, skill_uuid: str, version: str, env: str, checksum: str = None
    ) -> None: ...


@runtime_checkable
class SkillPropagationLogRepository(Protocol):
    """ac_skill_propagation_log 存储接口。"""

    @abstractmethod
    def create(self, data: dict) -> dict:
        """写入一条新 log 记录，返回含 propagation_id 的 dict。"""
        ...

    @abstractmethod
    def update(self, propagation_id: str, data: dict) -> None:
        """按 propagation_id 更新 log 记录。"""
        ...

    @abstractmethod
    def find_recent(
        self, skill_uuid: str, env: str, within_seconds: int
    ) -> dict | None:
        """查找 within_seconds 内同 (skill_uuid, env) 的最新 done/pending 记录。"""
        ...
