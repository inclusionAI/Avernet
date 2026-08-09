"""Repository contracts owned by the ``skill_center`` domain.

Moved here by the ``core/repository`` consolidation. Every member is
``@abstractmethod``: an implementation that omits one fails at construction
naming the missing member, instead of raising ``AttributeError`` at the call
site. Domain imports are ``TYPE_CHECKING``-only — see the module docstring in
``core/repository/README.md`` for why that direction is load-bearing.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import List, Optional, Protocol, runtime_checkable


@runtime_checkable
class SkillRepository(Protocol):
    """技能 Repository 接口"""

    @abstractmethod
    def get_by_id(self, skill_id: str) -> dict | None:
        ...

    @abstractmethod
    def get_by_uuid(self, skill_uuid: str, env: str | None = None) -> dict | None:
        """根据 skill_uuid（UUID 字符串）查询技能记录。"""
        ...

    @abstractmethod
    def get_by_git_path(self, git_path: str) -> dict | None:
        ...

    @abstractmethod
    def get_by_link_name(self, link_name: str, bolt_id: str | None = None) -> dict | None:
        ...

    @abstractmethod
    def list_skills(self, user_id: str | None = None, bolt_id: str | None = 'default',
                    env: str | None = None) -> list[dict]:
        ...

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
    def list_bot_local_skills(
        self,
        *,
        bot_id: str,
        user_id: str,
        page: int,
        page_size: int,
        active: bool | None,
        keyword: str | None,
    ) -> tuple[int, list[dict]]:
        """Page exact Bot-owned ``local://`` desired-state Skill metadata."""
        ...

    @abstractmethod
    def get_bot_local_skill(
        self, *, skill_id: str, bot_id: str, user_id: str
    ) -> dict | None:
        """Return one exact Bot-owned ``local://`` Skill with desired state."""
        ...

    @abstractmethod
    def create(self, skill_data: dict) -> dict:
        ...

    @abstractmethod
    def update(self, skill_id: str, skill_data: dict) -> dict | None:
        ...

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
        requires_runtime_restore: bool,
        cleanup_work_id: int,
    ) -> int | None:
        """Atomically switch package authority and commit old-package cleanup."""
        ...

    @abstractmethod
    def delete(self, skill_id: str) -> bool:
        ...

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
        quarantine_locator: str,
        cleanup_work_id: int,
    ) -> int | None:
        """Atomically delete scoped state and commit prepared cleanup work."""
        ...

    @abstractmethod
    def check_skill_blocked_by_bot(self, name: str, env: str | None = None) -> list[str]:
        """Return bot ids whose active skill-sets reference this skill
        (deletion blockers); empty list if none."""
        ...

    @abstractmethod
    def delete_by_name_with_cascade(self, name: str, env: str | None = None) -> dict:
        """Delete the skill by name and cascade related rows; returns a
        summary dict of what was removed."""
        ...

    @abstractmethod
    def update_risk_tags(self, skill_id: str, risk_tags: list) -> dict | None:
        ...

    @abstractmethod
    def update_mcp_dependencies(self, skill_id: str, mcp_dependencies: list) -> dict | None:
        ...

    @abstractmethod
    def get_by_name_global_include_deleted(self, name: str, user_id: str | None = None) -> dict | None:
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
        self, bot_id: str, entity_type: str, owner_id: str,
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
    def get_by_id(self, skill_set_id: str) -> dict | None:
        ...

    @abstractmethod
    def get_default(
        self,
        user_id: str | None = None,
        bolt_id: str | None = None,
        engine_type: str | None = None,
    ) -> dict | None:
        ...

    @abstractmethod
    def list_all(
        self,
        user_id: str | None = None,
        bolt_id: str | None = None,
        engine_type: str | None = None,
    ) -> list[dict]:
        ...

    @abstractmethod
    def create(self, skill_set_data: dict) -> dict:
        ...

    @abstractmethod
    def update(self, skill_set_id: str, skill_set_data: dict) -> dict | None:
        ...

    @abstractmethod
    def delete(self, skill_set_id: str) -> bool:
        ...

    @abstractmethod
    def add_skill_to_set(
        self, skill_set_id: str, skill_id: str, user_id: str | None = None
    ) -> bool:
        ...

    @abstractmethod
    def get_skills_in_set(self, skill_set_id: str) -> list[dict]:
        ...

    @abstractmethod
    def find_affected_bots_by_skill_uuid(
        self, skill_uuid: str, env: str | None = None,
    ) -> list[dict]:
        """找出所有受 skill_uuid 升级影响的 bot。

        语义：只返回 普通 active SkillSet（is_default=0 且 is_active=1，bolt_id 非空）
        所属、且未删除的 bot。default SkillSet 不主动传播，依赖会话启动兜底。

        engine_type 取自 ac_bots.active_engine（source of truth），不取 SkillSet 自带值。

        返回每条 dict：{"bot_id", "active_engine", "owner_id"}
        """
        ...

    @abstractmethod
    def remove_skill_from_set(self, skill_set_id: str, skill_id: str) -> bool:
        ...

    @abstractmethod
    def add_default_skill_exclusion(
        self, user_id: str, bot_id: str, skill_set_id: int, skill_id: int
    ) -> bool:
        ...

    @abstractmethod
    def remove_default_skill_exclusion(
        self, user_id: str, bot_id: str, skill_set_id: int, skill_id: int
    ) -> bool:
        ...

    @abstractmethod
    def remove_all_default_skill_exclusions(
        self, user_id: str, bot_id: str, skill_id: int
    ) -> bool:
        """Clear every default-set exclusion for one Bot-owned Skill."""
        ...

    @abstractmethod
    def add_mcp_to_set(self, skill_set_id: str, server_code: str, name: str,
                       description: str | None = None, icon: str | None = None,
                       user_id: str | None = None, env: str | None = None) -> bool:
        ...

    @abstractmethod
    def get_mcp_servers_in_set(self, skill_set_id: str) -> list[dict]:
        ...

    @abstractmethod
    def get_mcp_servers_in_set_for_env(
        self, skill_set_id: str, *, env: str
    ) -> list[dict]:
        """Return associations belonging to one explicit environment."""
        ...

    @abstractmethod
    def remove_mcp_from_set(self, skill_set_id: str, server_code: str) -> bool:
        ...

    @abstractmethod
    def get_active_skill_set(
        self,
        user_id: str | None = None,
        bolt_id: str | None = None,
        engine_type: str | None = None,
    ) -> dict | None:
        ...

    @abstractmethod
    def set_active_skill_set(
        self,
        skill_set_id: str,
        user_id: str | None = None,
        bolt_id: str | None = None,
        engine_type: str | None = None,
    ) -> bool:
        ...

    @abstractmethod
    def clear_active_skill_set(
        self,
        user_id: str | None = None,
        bolt_id: str | None = None,
        engine_type: str | None = None,
    ) -> bool:
        ...

    @abstractmethod
    def get_all_active_skill_sets(
        self,
        user_id: str | None = None,
        bolt_id: str | None = None,
        engine_type: str | None = None,
    ) -> list[dict]:
        ...

    @abstractmethod
    def get_all_active_skill_sets_for_env(
        self,
        *,
        user_id: str | None = None,
        bolt_id: str | None = None,
        engine_type: str | None = None,
        env: str,
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
    ) -> bool:
        ...

    @abstractmethod
    def deactivate_skill_set(
        self,
        skill_set_id: str,
        user_id: str | None = None,
        bolt_id: str | None = None,
        engine_type: str | None = None,
    ) -> bool:
        ...

    @abstractmethod
    def activate_default_skillset(self) -> int:
        ...

    @abstractmethod
    def list_all_exclude_deleted(
        self,
        user_id: str | None = None,
        bolt_id: str | None = None,
        engine_type: str | None = None,
    ) -> list[dict]:
        """列出所有技能集（排除已删除 Bot 的），用于重名检查避免误判。"""
        ...

    @abstractmethod
    def get_skill_set_by_name_include_deleted(self, name: str, user_id: str, bolt_id: str | None = None) -> dict | None:
        """根据名称查找技能集（包括已删除 Bot 的），用于复用记录避免唯一约束冲突。

        Args:
            name: 技能集名称
            user_id: 用户 ID
            bolt_id: Bot ID，用于区分不同 bot 下的同名技能集
        """
        ...

    @abstractmethod
    def get_all_user_mcps(self, user_id: str) -> list[dict]:
        ...

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
    def create(self, code: str, name: str, parent_code: str,
               path: str, level: int, sort_order: int) -> dict:
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
    def create(self, data: dict) -> dict:
        ...

    @abstractmethod
    def mark_success(self, skill_uuid: str, version: str, env: str, checksum: str = None) -> None:
        ...


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
    def find_recent(self, skill_uuid: str, env: str, within_seconds: int) -> dict | None:
        """查找 within_seconds 内同 (skill_uuid, env) 的最新 done/pending 记录。"""
        ...


@runtime_checkable
class LocalSkillCleanupRepository(Protocol):
    """Persist and progress retryable cleanup work within one exact Bot scope."""

    @abstractmethod
    def record_preparing(
        self,
        *,
        env: str,
        owner_id: str,
        bot_id: str,
        skill_id: str,
        package_locator: str,
    ) -> int | None:
        """Durably reserve a quarantine before authoritative bytes move."""
        ...

    @abstractmethod
    def record_pending(
        self,
        *,
        env: str,
        owner_id: str,
        bot_id: str,
        skill_id: str,
        package_locator: str,
        requires_runtime_restore: bool,
    ) -> int | None: ...

    @abstractmethod
    def record_repair_required(
        self,
        *,
        env: str,
        owner_id: str,
        bot_id: str,
        skill_id: str,
        package_locator: str,
    ) -> int | None: ...

    @abstractmethod
    def list_pending(self, *, env: str, owner_id: str, bot_id: str) -> list[dict]: ...

    @abstractmethod
    def list_repair_required(
        self,
        *,
        env: str,
        owner_id: str,
        bot_id: str,
        skill_id: str,
    ) -> list[dict]:
        """Return quarantines that must be restored before retrying this delete."""
        ...

    @abstractmethod
    def mark_cleaned(
        self, *, work_id: int, env: str, owner_id: str, bot_id: str
    ) -> bool: ...

    @abstractmethod
    def mark_failed(
        self, *, work_id: int, env: str, owner_id: str, bot_id: str, error: str
    ) -> bool: ...

    @abstractmethod
    def cancel_pending(
        self, *, work_id: int, env: str, owner_id: str, bot_id: str
    ) -> bool:
        """Cancel pending or not-yet-committed preparation work."""
        ...
