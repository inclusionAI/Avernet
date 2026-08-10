"""
Bot cleanup service — 清理已删除 Bot 的关联数据。

迁移自 services/openclawserver/server/services/cleanup_service.py，
遵循新架构规范，通过 repository 协议访问数据。
"""
from agentclaw.community.log import get_logger
from typing import Dict, Any

from injector import inject

from agentclaw.community.core.repository.protocols.skill_center import SkillSetRepository
from agentclaw.community.core.repository.protocols.skill_center import SkillRepository

logger = get_logger()


class BotCleanupService:
    """清理 Bot 关联的脏数据（技能、技能集）。"""

    @inject
    def __init__(
        self,
        skill_repo: SkillRepository,
        skill_set_repo: SkillSetRepository,
    ):
        """
        Args:
            skill_repo: SkillRepository 实例（支持 delete_by_bot_id）
            skill_set_repo: SkillSetRepository 实例（支持 delete_by_bot_id）
        """
        self._skill_repo = skill_repo
        self._skill_set_repo = skill_set_repo

    def cleanup_single_bot_data(self, bot_id: str, user_id: str) -> Dict[str, Any]:
        """清理单个 Bot 的关联数据。

        Args:
            bot_id: Bot ID
            user_id: 用户 ID

        Returns:
            清理结果统计
        """
        logger.info(f"[BotCleanupService] Cleaning up data for bot {bot_id}, user {user_id}")

        result: Dict[str, Any] = {
            "skills_deleted": 0,
            "skill_sets_deleted": 0,
            "resources_deleted": 0,
            "errors": [],
        }

        # 1. 清理技能
        try:
            result["skills_deleted"] = self._skill_repo.delete_by_bot_id(bot_id)
        except Exception as e:
            error_msg = f"Cleanup skills error for bot {bot_id}: {e}"
            logger.error(f"[BotCleanupService] {error_msg}")
            result["errors"].append(error_msg)

        # 2. 清理技能集（含关联表）
        try:
            result["skill_sets_deleted"] = self._skill_set_repo.delete_by_bot_id(bot_id)
        except Exception as e:
            error_msg = f"Cleanup skill_sets error for bot {bot_id}: {e}"
            logger.error(f"[BotCleanupService] {error_msg}")
            result["errors"].append(error_msg)

        logger.info(
            f"[BotCleanupService] Cleanup completed for bot {bot_id}: "
            f"skills={result['skills_deleted']}, skill_sets={result['skill_sets_deleted']}"
        )

        return result
