"""
Skill Center Repository Protocol definitions.

These protocols define the interface contract that any skill/skillset
storage implementation must satisfy (SQLite, a hosted relational store, etc.).

Implementation: single unified ORM bodies that run on both prod
OceanBase and local SQLite via the injected DatabasePlugin —
- ``agentclaw.community.plugins.skill_repository`` (SkillRepository +
  SkillSetRepository; faithful prod port)
- ``agentclaw.community.plugins.skill_member_repository``
- ``agentclaw.community.plugins.skill_category_repository``
"""
from typing import List, Optional, Protocol, runtime_checkable


class ActiveSkillSetReferenceError(RuntimeError):
    """A Skill became referenced by an active custom SkillSet."""


@runtime_checkable
class SkillRepository(Protocol):
    """技能 Repository 接口"""

    def get_by_id(self, skill_id: str) -> dict | None:
        ...

    def get_by_uuid(self, skill_uuid: str, env: str | None = None) -> dict | None:
        """根据 skill_uuid（UUID 字符串）查询技能记录。"""
        ...

    def get_by_git_path(self, git_path: str) -> dict | None:
        ...

    def get_by_link_name(self, link_name: str, bolt_id: str | None = None) -> dict | None:
        ...

    def list_skills(self, user_id: str | None = None, bolt_id: str | None = 'default',
                    env: str | None = None) -> list[dict]:
        ...

    def get_bot_local_by_name(
        self,
        *,
        bot_id: str,
        name: str,
        user_id: str | None = None,
    ) -> dict | None:
        """精确查询 Bot 自有的同名 local 技能，不包含全局行。"""
        ...

    def list_bot_local_by_name(self, *, bot_id: str, name: str) -> list[dict]:
        """Return every exact Bot-local same-name row for ambiguity handling.

        The caller resolves legacy owner semantics after it holds the Bot Skill
        layout edit lock; this method deliberately does not pick a winner.
        """
        ...

    def get_bot_local_by_locator(
        self, *, bot_id: str, user_id: str, locator: str
    ) -> dict | None:
        """Return the current exact Bot-owned Local Skill for one locator."""
        ...

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

    def get_bot_local_skill(
        self, *, skill_id: str, bot_id: str, user_id: str
    ) -> dict | None:
        """Return one exact Bot-owned ``local://`` Skill with desired state."""
        ...

    def create(self, skill_data: dict) -> dict:
        ...

    def update(self, skill_id: str, skill_data: dict) -> dict | None:
        ...

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

    def delete(self, skill_id: str) -> bool:
        ...

    def list_skill_set_references(
        self,
        skill_id: str,
        skill_uuid: str | None = None,
    ) -> list[dict]:
        """Return every current-environment SkillSet association for a Skill."""

    def add_default_skill_exclusion(
        self, user_id: str, bot_id: str, skill_set_id: int, skill_id: int
    ) -> bool:
        ...

    def remove_default_skill_exclusion(
        self, user_id: str, bot_id: str, skill_set_id: int, skill_id: int
    ) -> bool:
        ...

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

    def check_skill_blocked_by_bot(self, name: str, env: str | None = None) -> list[str]:
        """Return bot ids whose active skill-sets reference this skill
        (deletion blockers); empty list if none."""
        ...

    def delete_by_name_with_cascade(self, name: str, env: str | None = None) -> dict:
        """Delete the skill by name and cascade related rows; returns a
        summary dict of what was removed."""
        ...

    def update_risk_tags(self, skill_id: str, risk_tags: list) -> dict | None:
        ...

    def update_mcp_dependencies(self, skill_id: str, mcp_dependencies: list) -> dict | None:
        ...

    def get_by_name_global_include_deleted(self, name: str, user_id: str | None = None) -> dict | None:
        """根据技能名称全局查询（包括已删除 Bot 的技能），用于复用已删除记录避免唯一约束冲突。"""
        ...

    def delete_by_bot_id(self, bot_id: str) -> int:
        """Delete all skills associated with a bot.

        Args:
            bot_id: Bot ID (maps to bolt_id column)

        Returns:
            Number of deleted records
        """
        ...

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

    def get_by_id(self, skill_set_id: str) -> dict | None:
        ...

    def get_default(
        self,
        user_id: str | None = None,
        bolt_id: str | None = None,
        engine_type: str | None = None,
    ) -> dict | None:
        ...

    def list_all(
        self,
        user_id: str | None = None,
        bolt_id: str | None = None,
        engine_type: str | None = None,
    ) -> list[dict]:
        ...

    def create(self, skill_set_data: dict) -> dict:
        ...

    def update(self, skill_set_id: str, skill_set_data: dict) -> dict | None:
        ...

    def delete(self, skill_set_id: str) -> bool:
        ...

    def add_skill_to_set(
        self, skill_set_id: str, skill_id: str, user_id: str | None = None
    ) -> bool:
        ...

    def get_skills_in_set(self, skill_set_id: str) -> list[dict]:
        ...

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

    def remove_skill_from_set(self, skill_set_id: str, skill_id: str) -> bool:
        ...

    def add_default_skill_exclusion(
        self, user_id: str, bot_id: str, skill_set_id: int, skill_id: int
    ) -> bool:
        ...

    def remove_default_skill_exclusion(
        self, user_id: str, bot_id: str, skill_set_id: int, skill_id: int
    ) -> bool:
        ...

    def add_mcp_to_set(self, skill_set_id: str, server_code: str, name: str,
                       description: str | None = None, icon: str | None = None,
                       user_id: str | None = None, env: str | None = None) -> bool:
        ...

    def get_mcp_servers_in_set(self, skill_set_id: str) -> list[dict]:
        ...

    def get_mcp_servers_in_set_for_env(
        self, skill_set_id: str, *, env: str
    ) -> list[dict]:
        """Return associations belonging to one explicit environment."""
        ...

    def remove_mcp_from_set(self, skill_set_id: str, server_code: str) -> bool:
        ...

    def get_active_skill_set(
        self,
        user_id: str | None = None,
        bolt_id: str | None = None,
        engine_type: str | None = None,
    ) -> dict | None:
        ...

    def set_active_skill_set(
        self,
        skill_set_id: str,
        user_id: str | None = None,
        bolt_id: str | None = None,
        engine_type: str | None = None,
    ) -> bool:
        ...

    def clear_active_skill_set(
        self,
        user_id: str | None = None,
        bolt_id: str | None = None,
        engine_type: str | None = None,
    ) -> bool:
        ...

    def get_all_active_skill_sets(
        self,
        user_id: str | None = None,
        bolt_id: str | None = None,
        engine_type: str | None = None,
    ) -> list[dict]:
        ...

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

    def activate_skill_set(
        self,
        skill_set_id: str,
        user_id: str | None = None,
        bolt_id: str | None = None,
        engine_type: str | None = None,
    ) -> bool:
        ...

    def deactivate_skill_set(
        self,
        skill_set_id: str,
        user_id: str | None = None,
        bolt_id: str | None = None,
        engine_type: str | None = None,
    ) -> bool:
        ...

    def activate_default_skillset(self) -> int:
        ...

    def list_all_exclude_deleted(
        self,
        user_id: str | None = None,
        bolt_id: str | None = None,
        engine_type: str | None = None,
    ) -> list[dict]:
        """列出所有技能集（排除已删除 Bot 的），用于重名检查避免误判。"""
        ...

    def get_skill_set_by_name_include_deleted(self, name: str, user_id: str, bolt_id: str | None = None) -> dict | None:
        """根据名称查找技能集（包括已删除 Bot 的），用于复用记录避免唯一约束冲突。

        Args:
            name: 技能集名称
            user_id: 用户 ID
            bolt_id: Bot ID，用于区分不同 bot 下的同名技能集
        """
        ...

    def get_all_user_mcps(self, user_id: str) -> list[dict]:
        ...

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

    def get_members_by_skill_uuid(self, skill_uuid: str) -> List[dict]:
        """获取指定技能的所有成员

        Args:
            skill_uuid: 技能 UUID

        Returns:
            成员列表，每个成员包含：id, skill_uuid, user_id, role, gmt_create, gmt_modified
        """
        ...

    def get_member(self, skill_uuid: str, user_id: str) -> Optional[dict]:
        """获取指定技能的单个成员信息

        Args:
            skill_uuid: 技能 UUID
            user_id: 用户 ID

        Returns:
            成员信息字典，不存在则返回 None
        """
        ...

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

    def is_member(self, skill_uuid: str, user_id: str) -> bool:
        """检查用户是否为技能成员

        Args:
            skill_uuid: 技能 UUID
            user_id: 用户 ID

        Returns:
            是否为成员
        """
        ...

    def get_member_role(self, skill_uuid: str, user_id: str) -> Optional[str]:
        """获取用户在指定技能中的角色

        Args:
            skill_uuid: 技能 UUID
            user_id: 用户 ID

        Returns:
            角色字符串 ("admin" | "member")，不是成员则返回 None
        """
        ...

    def get_skill_uuids_by_user_id(self, user_id: str) -> List[str]:
        """获取用户参与的所有技能 UUID 列表

        Args:
            user_id: 用户 ID

        Returns:
            该用户参与的所有技能 UUID 列表
        """
        ...

    def has_admin_role(self, skill_uuid: str, user_id: str) -> bool:
        """检查用户是否为技能管理员

        Args:
            skill_uuid: 技能 UUID
            user_id: 用户 ID

        Returns:
            是否为管理员
        """
        ...

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

    def list_active(self) -> list[dict]:
        """列出所有启用的类目（status=1），按 level、sort_order 排序。"""
        ...

    def get_by_code(self, code: str) -> Optional[dict]:
        """根据 code 获取类目。"""
        ...

    def create(self, code: str, name: str, parent_code: str,
               path: str, level: int, sort_order: int) -> dict:
        """创建类目，返回创建后的 dict。"""
        ...

    def update(self, code: str, **fields) -> Optional[dict]:
        """更新类目字段，返回更新后的 dict，不存在则返回 None。"""
        ...

    def list_descendant_codes(self, path: str) -> list[str]:
        """列出 path 前缀匹配的所有启用类目的 code（含自身）。"""
        ...
        ...
