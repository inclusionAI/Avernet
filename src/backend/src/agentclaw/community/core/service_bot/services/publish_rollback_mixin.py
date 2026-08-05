"""Publish Rollback Mixin - 回滚相关业务逻辑。

从 BotPublishService 提取的回滚功能，用于满足 Rule 9 (Single Responsibility) 的文件行数限制。
"""
from datetime import datetime
from typing import TYPE_CHECKING, Callable

from agentclaw.community.core.service_bot.repository.models import PublishStatus
from agentclaw.community.core.service_bot.services.publish_exceptions import (
    BotPublishServiceError,
    PublishNotFoundError,
)
from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    from agentclaw.community.core.service_bot.repository.bot_publish_repository import BotPublishRepositoryProtocol
    from agentclaw.community.core.service_bot.services.publish_flow_service import PublishFlowService

logger = get_logger()


class PublishRollbackMixin:
    """回滚功能 Mixin，提供 can_rollback 和 rollback_publish 方法。

    使用此 Mixin 的类必须提供：
    - _repo: BotPublishRepositoryProtocol
    - _env: str
    - _publish_flow_service_provider: Callable[[], PublishFlowService]
    - update_publish_status_with_ext() 方法
    """

    _repo: "BotPublishRepositoryProtocol"
    _env: str
    _publish_flow_service_provider: Callable[[], "PublishFlowService"]

    def can_rollback(self: "PublishRollbackMixin", publish_id: int) -> tuple[bool, str]:
        """检查发布单是否可以回滚。

        回滚条件：
        1. 发布单状态必须是 SUCCESS
        2. 有上一个版本（last_pub_id > 0）
        3. 当前版本无 rollback_restored_from 标记（非通过回滚恢复）
        4. 无新版本基于当前版本（版本链未延伸）
        5. 目标版本状态为 UPGRADED
        6. 目标版本有构建产物

        Args:
            publish_id: 发布单 ID

        Returns:
            tuple[bool, str]: (是否可以回滚, 原因)
        """
        # 1. 查询发布单
        record = self._repo.get_by_id(publish_id)
        if not record:
            return False, f"发布单不存在: publish_id={publish_id}"

        # 2. 状态校验：只有 SUCCESS 可以回滚
        if record.status != PublishStatus.SUCCESS:
            return False, f"只有 SUCCESS 状态的发布单可以回滚，当前状态: {record.status}"

        # 3. 版本链校验：必须有上一个版本
        if not record.last_pub_id or record.last_pub_id <= 0:
            return False, "没有可回滚的目标版本（last_pub_id 为空）"

        # 4. 当前版本未通过回滚恢复（防止连续向前回滚）
        current_ext = record.ext or {}
        if current_ext.get("rollback_restored_from"):
            return False, "当前版本是通过回滚恢复的，不能继续向前回滚"

        # 5. 检查是否有新版本基于当前版本创建（版本链已向前延伸）
        next_record = self._repo.get_by_last_pub_id(publish_id)
        if next_record:
            # 额外校验 owner_id 和 env
            if next_record.owner_id == record.owner_id and next_record.env == self._env:
                return False, f"已有新版本 v{next_record.version} 基于当前版本创建，不能回滚"

        # 6. 目标版本存在性校验
        target = self._repo.get_by_id(record.last_pub_id)
        if not target:
            return False, f"目标版本不存在: last_pub_id={record.last_pub_id}"

        # 7. 目标版本状态校验（必须是 UPGRADED）
        if target.status != PublishStatus.UPGRADED:
            return False, f"目标版本状态不支持回滚: {target.status}，期望: UPGRADED"

        # 8. 构建产物校验
        target_ext = target.ext or {}
        if not target_ext.get("migration_path") and not target_ext.get("config_artifact"):
            return False, "目标版本缺少构建产物，无法回滚"

        return True, "可以回滚"

    async def rollback_publish(
        self: "PublishRollbackMixin",
        publish_id: int,
        operator: str = "system",
        reason: str | None = None,
    ) -> dict:
        """回滚当前线上版本到上一个稳定版本。

        操作流程：
        1. 校验是否可以回滚
        2. 将当前版本(publish_id)状态改为 DRAFT，记录 ext.rollback
        3. 将上一个版本(last_pub_id)状态恢复为 SUCCESS，标记 ext.rollback_restored_from
        4. 调用 PublishFlowService.execute_rollback 执行回滚部署
           - 目标版本状态从 SUCCESS 变为 ONLINE_PUB（部署中）
           - 前端应同步 target_publish_id 的部署进度

        Args:
            publish_id: 当前发布单 ID（必须是 SUCCESS 状态）
            operator: 操作者 ID
            reason: 回滚原因（可选）

        Returns:
            dict: {
                "rolled_back_publish_id": 4,    # 被回滚的版本
                "rolled_back_status": "draft",  # 当前版本变为草稿
                "target_publish_id": 3,          # 回滚目标版本
                "target_version": 3,
                "target_status": "online_pub",   # 目标版本状态（部署中）
                "deploy_status": "online_pub",   # 部署状态
                "deploy_message": "回滚发布已提交"  # 部署消息
            }

        Raises:
            PublishNotFoundError: 发布单不存在
            PublishStatusInvalidError: 发布单状态不支持回滚
            BotPublishServiceError: 回滚失败
        """
        logger.info(
            f"[PublishRollbackMixin.rollback_publish] called: publish_id={publish_id}, "
            f"operator={operator}, reason={reason}"
        )

        # 1. 校验是否可以回滚
        can_rollback, rollback_reason = self.can_rollback(publish_id)
        if not can_rollback:
            raise BotPublishServiceError(f"无法回滚: {rollback_reason}")

        # 2. 查询当前发布单和目标发布单
        current_record = self._repo.get_by_id(publish_id)
        if not current_record:
            raise PublishNotFoundError(f"Publish record not found: {publish_id}")

        target_record = self._repo.get_by_id(current_record.last_pub_id)
        if not target_record:
            raise PublishNotFoundError(
                f"Target publish record not found: last_pub_id={current_record.last_pub_id}"
            )

        # 3+4. (#197) Atomically flip both records (one transaction) to avoid a
        # "half-flip" that would leave can_rollback permanently refusing. The
        # demoted (currently-live) record goes SUCCESS→DRAFT (recording
        # ext.rollback); the restored (previous) record goes UPGRADED→SUCCESS
        # (marking rollback_restored_from).
        demoted_ext = current_record.ext or {}
        demoted_ext["rollback"] = {
            "rolled_back_at": datetime.now().isoformat(),
            "rolled_back_by": operator,
            "rollback_reason": reason,
            "target_publish_id": current_record.last_pub_id,
        }
        # Clear this version's online release refs before it re-enters DRAFT. The refs
        # (ext.publish.online = BaaS publish id, ext.binding.online = device binding)
        # must not leak across a later re-publish lifecycle.
        for section in ("publish", "binding"):
            refs = demoted_ext.get(section)
            if isinstance(refs, dict):
                refs.pop(PublishStage.ONLINE.value, None)
        restored_ext = target_record.ext or {}
        restored_ext["rollback_restored_from"] = publish_id

        demoted_ok, restored_ok = self._repo.rollback_flip(
            demoted_publish_id=publish_id,
            demoted_ext=demoted_ext,
            demoted_from_status=PublishStatus.SUCCESS.value,
            demoted_to_status=PublishStatus.DRAFT.value,
            restored_publish_id=current_record.last_pub_id,
            restored_ext=restored_ext,
            restored_from_status=PublishStatus.UPGRADED.value,
            restored_to_status=PublishStatus.SUCCESS.value,
        )
        if not (demoted_ok and restored_ok):
            raise BotPublishServiceError(
                f"回滚状态翻转失败（并发或状态不符）: demoted_ok={demoted_ok}, "
                f"restored_ok={restored_ok}, publish_id={publish_id}"
            )
        logger.info(
            f"[rollback_publish] Atomically flipped: demoted({publish_id}) SUCCESS→DRAFT, "
            f"restored({current_record.last_pub_id}) UPGRADED→SUCCESS"
        )

        # 5. 执行回滚部署（通过 PublishFlowService）
        flow_service = self._publish_flow_service_provider()
        deploy_result = await flow_service.execute_rollback(
            current_publish_id=publish_id,
            target_publish_id=current_record.last_pub_id,
            operator=operator,
        )

        return {
            "rolled_back_publish_id": publish_id,
            "rolled_back_status": PublishStatus.DRAFT.value,
            "target_publish_id": current_record.last_pub_id,
            "target_version": target_record.version,
            # 目标版本状态已由 execute_rollback 更新为 ONLINE_PUB（部署中）
            "target_status": deploy_result.status.value if hasattr(deploy_result.status, 'value') else str(deploy_result.status),
            "deploy_status": deploy_result.status.value if hasattr(deploy_result.status, 'value') else str(deploy_result.status),
            "deploy_message": deploy_result.message,
        }
