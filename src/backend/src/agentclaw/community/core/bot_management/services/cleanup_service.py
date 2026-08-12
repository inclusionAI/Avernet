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

    def purge_startup_script(self, *, entity_id: str, bot_id: str) -> bool:
        """删除 Bot 存储的启动脚本。**失败向上抛，不吞。**

        与下面 ``cleanup_single_bot_data`` 里的技能清理不同，这一项刻意不走
        "记录日志然后继续"：

        * 残留的不是惰性元数据，而是**明文可执行内容**。而且 ``create_bot``
          允许调用方指定 ``bot_id``、软删的 Bot 又被视为不存在，所以同一个
          ``(entity_id, bot_id)`` 一旦被重建，这段脚本会在新 Bot 的每次启动里
          执行——一个新主人从没写过的脚本。
        * 本仓库对同类清理已经有先例：``delete_bot`` 里的 app grant 回收同样
          "先于一切破坏性步骤、失败直接抛"，理由写在那里——失败时 Bot 还完好，
          最坏的结果只是脚本被删而 Bot 存活，重新 PUT 即可恢复。
        * 本 PR 自己也已经做过同一个判断：``_resolve_startup_script`` 原本吞掉
          读失败，现在改为上抛，理由是吞掉只会得到"启动了、报告就绪、其实没配置"
          的静默错误状态。删除侧吞掉失败是同一个形状。

        真被卡住的风险比看上去小：这次写入和软删打的是**同一个后端数据库**，
        写不进去通常意味着这次删除本来也会失败。
        """
        return self._startup_script_purge.delete(entity_id=entity_id, bot_id=bot_id)

    def purge_startup_script_written_by(
        self, *, entity_id: str, bot_id: str, bot_incarnation: int
    ) -> bool:
        """删除启动脚本，但**仅当它仍属于该 incarnation**。失败同样上抛。

        用于软删之后的第二次清扫。那时 Bot 已经不在了，标识符因此是空闲的：
        它可以被重建，而新 Bot 完全可能在这次清扫之前合法地写入自己的脚本。
        无条件删除会把它一并抹掉——一个 Bot 的删除毁掉另一个 Bot 的数据。

        软删**之前**的那次清扫不需要这个条件：那时 Bot 还活着，标识符不可能
        已经属于别人。
        """
        return self._startup_script_purge.delete_written_by(
            entity_id=entity_id, bot_id=bot_id, bot_incarnation=bot_incarnation
        )

    def cleanup_single_bot_data(self, bot_id: str, user_id: str) -> Dict[str, Any]:
        """清理单个 Bot 的关联数据。

        启动脚本**不在**这里清理——它由 ``purge_startup_script`` 在软删之前
        单独处理，失败要上抛。见那里的说明。

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
