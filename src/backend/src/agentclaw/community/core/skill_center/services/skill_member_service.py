"""SkillMemberService — 技能成员管理服务

负责技能的成员权限管理，包括：
- 添加/移除成员
- 更新成员角色
- 查询成员列表
- 权限验证

通过 Repository 注入实现数据库操作，支持 SQLite 和关系存储两种模式：
- SQLite 模式：使用 SQLiteSkillMemberRepository（SQLAlchemy ORM）
- 关系存储/MySQL 模式：使用基于游标的 raw-SQL repository 实现
"""
from __future__ import annotations

from typing import List, Dict, Optional, Any

from injector import inject

from agentclaw.community.core.skill_center.services.repositories import SkillMemberRepository
from agentclaw.community.log import get_logger

logger = get_logger()


class SkillMemberService:
    """技能成员管理服务

    通过注入 SkillMemberRepository 实现数据库操作，
    不再包含 if/else 数据库模式判断逻辑。
    """

    @inject
    def __init__(self, repo: SkillMemberRepository):
        """
        Args:
            repo: SkillMemberRepository 实例（SQLite 或关系存储实现）
        """
        self._repo = repo

    # ========================================================================
    # 成员管理 CRUD
    # ========================================================================

    def get_members_by_skill_uuid(self, skill_uuid: str) -> List[Dict[str, Any]]:
        """获取指定技能的所有成员

        Args:
            skill_uuid: 技能 UUID

        Returns:
            成员列表，每个成员包含：user_id, role, gmt_create
        """
        return self._repo.get_members_by_skill_uuid(skill_uuid)

    def get_member(self, skill_uuid: str, user_id: str) -> Optional[Dict[str, Any]]:
        """获取指定技能的单个成员信息

        Args:
            skill_uuid: 技能 UUID
            user_id: 用户 ID

        Returns:
            成员信息，不存在则返回 None
        """
        return self._repo.get_member(skill_uuid, user_id)

    def add_member(
        self,
        skill_uuid: str,
        user_id: str,
        role: str = "member"
    ) -> Dict[str, Any]:
        """添加技能成员

        Args:
            skill_uuid: 技能 UUID
            user_id: 用户 ID
            role: 角色，可选 "admin" 或 "member"，默认 "member"

        Returns:
            新添加的成员信息

        Raises:
            ValueError: 如果成员已存在或角色不合法
        """
        return self._repo.add_member(skill_uuid, user_id, role)

    def add_members_batch(
        self,
        skill_uuid: str,
        members: List[Dict[str, str]]
    ) -> Dict[str, Any]:
        """批量添加技能成员

        Args:
            skill_uuid: 技能 UUID
            members: 成员列表，每个成员为 {"user_id": str, "role": str}

        Returns:
            添加结果：{"success": [...], "failed": [...]}
        """
        results = {"success": [], "failed": []}

        for member_data in members:
            user_id = member_data.get("user_id")
            role = member_data.get("role", "member")

            if not user_id:
                results["failed"].append({
                    "user_id": user_id,
                    "error": "user_id is required"
                })
                continue

            if role not in ("admin", "member"):
                results["failed"].append({
                    "user_id": user_id,
                    "error": f"Invalid role: {role}"
                })
                continue

            try:
                member = self._repo.add_member(skill_uuid, user_id, role)
                results["success"].append(member)
            except ValueError as e:
                results["failed"].append({
                    "user_id": user_id,
                    "error": str(e)
                })

        return results

    def remove_member(self, skill_uuid: str, user_id: str) -> bool:
        """移除技能成员

        Args:
            skill_uuid: 技能 UUID
            user_id: 用户 ID

        Returns:
            bool: 是否成功移除

        Raises:
            ValueError: 如果成员不存在
        """
        return self._repo.remove_member(skill_uuid, user_id)

    def update_member_role(
        self,
        skill_uuid: str,
        user_id: str,
        role: str
    ) -> Dict[str, Any]:
        """更新成员角色

        Args:
            skill_uuid: 技能 UUID
            user_id: 用户 ID
            role: 新角色，"admin" 或 "member"

        Returns:
            更新后的成员信息

        Raises:
            ValueError: 如果成员不存在或角色不合法
        """
        return self._repo.update_member_role(skill_uuid, user_id, role)

    def is_member(self, skill_uuid: str, user_id: str) -> bool:
        """检查用户是否为技能成员

        Args:
            skill_uuid: 技能 UUID
            user_id: 用户 ID

        Returns:
            bool: 是否为成员
        """
        return self._repo.is_member(skill_uuid, user_id)

    def get_member_role(self, skill_uuid: str, user_id: str) -> Optional[str]:
        """获取用户在指定技能中的角色

        Args:
            skill_uuid: 技能 UUID
            user_id: 用户 ID

        Returns:
            角色字符串 ("admin" | "member")，不是成员则返回 None
        """
        return self._repo.get_member_role(skill_uuid, user_id)

    def get_skill_uuids_by_user_id(self, user_id: str) -> List[str]:
        """获取用户参与的所有技能 UUID 列表

        Args:
            user_id: 用户 ID

        Returns:
            该用户参与的所有技能 UUID 列表
        """
        return self._repo.get_skill_uuids_by_user_id(user_id)

    def has_admin_role(self, skill_uuid: str, user_id: str) -> bool:
        """检查用户是否为技能管理员

        Args:
            skill_uuid: 技能 UUID
            user_id: 用户 ID

        Returns:
            bool: 是否为管理员
        """
        return self._repo.has_admin_role(skill_uuid, user_id)

    def get_members_by_skill_uuids(self, skill_uuids: List[str]) -> Dict[str, List[Dict[str, str]]]:
        """批量查询多个技能的成员

        Args:
            skill_uuids: 技能 UUID 列表

        Returns:
            字典 {skill_uuid: [{"user_id": str, "role": str}, ...]}
        """
        return self._repo.get_members_by_skill_uuids(skill_uuids)
