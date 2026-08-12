"""
Bot cleanup service — 清理已删除 Bot 的关联数据。

迁移自 services/openclawserver/server/services/cleanup_service.py，
遵循新架构规范，通过 repository 协议访问数据。
"""
from agentclaw.community.log import get_logger
from typing import Dict, Any

from injector import inject

from agentclaw.community.core.bot_startup_script.protocols import (
    StartupScriptPurgeProtocol,
)
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
        startup_script_purge: StartupScriptPurgeProtocol,
    ):
        """
        Args:
            skill_repo: SkillRepository 实例（支持 delete_by_bot_id）
            skill_set_repo: SkillSetRepository 实例（支持 delete_by_bot_id）
            startup_script_purge: 启动脚本的删除side。必填而非可选——漏接的后果
                是脚本行静默残留，正是本次要修的问题本身。
        """
        self._skill_repo = skill_repo
        self._skill_set_repo = skill_set_repo
        self._startup_script_purge = startup_script_purge

    def cleanup_single_bot_data(
        self, bot_id: str, user_id: str, *, entity_id: str
    ) -> Dict[str, Any]:
        """清理单个 Bot 的关联数据。

        Args:
            bot_id: Bot ID
            user_id: 用户 ID
            entity_id: 拥有该 Bot 的实体 ID。启动脚本按 ``(entity_id, bot_id)``
                存储，``user_id`` 只是*通常*等于它（团队实体下并不相等），
                所以这里必须单独传入，不能拿 ``user_id`` 顶替。

        Returns:
            清理结果统计
        """
        logger.info(f"[BotCleanupService] Cleaning up data for bot {bot_id}, user {user_id}")

        result: Dict[str, Any] = {
            "skills_deleted": 0,
            "skill_sets_deleted": 0,
            "resources_deleted": 0,
            "startup_script_deleted": False,
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

        # 3. 清理启动脚本（issue #926）
        #
        # Bot 删除是软删除，没有任何级联能删掉这一行；不扫就会永久残留：
        # 明文可执行内容活得比它的 Bot 还久，而且 create_bot 允许调用方指定
        # bot_id、软删的 Bot 又被视为不存在——同一个 (entity_id, bot_id) 一旦
        # 被重建，新 Bot 每次启动都会执行上一个 Bot 的脚本。
        #
        # entity_id 为空说明这只 Bot 根本没有身份可用来存脚本（写入路径要求
        # 两者都在），不是错误，跳过即可。
        if entity_id:
            try:
                result["startup_script_deleted"] = self._startup_script_purge.delete(
                    entity_id=entity_id, bot_id=bot_id
                )
            except Exception as e:
                # 与上面两步一致：清理失败只记录，不阻断删除。残留一行的代价，
                # 远小于让一次清理失败把 Bot 卡在"删不掉"的状态。
                error_msg = f"Cleanup startup_script error for bot {bot_id}: {e}"
                logger.error(f"[BotCleanupService] {error_msg}")
                result["errors"].append(error_msg)

        logger.info(
            f"[BotCleanupService] Cleanup completed for bot {bot_id}: "
            f"skills={result['skills_deleted']}, skill_sets={result['skill_sets_deleted']}, "
            f"startup_script={result['startup_script_deleted']}"
        )

        return result
