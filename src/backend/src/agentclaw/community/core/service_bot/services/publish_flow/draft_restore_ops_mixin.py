"""Draft restore operations for the service-bot publish flow."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

from agentclaw.community.core.devices.models import DeviceBindingStatus
from agentclaw.community.core.service_bot.repository.models import PublishStatus
from agentclaw.community.core.service_bot.services.publish_exceptions import (
    PublishNotFoundError,
    PublishStatusInvalidError,
)
from agentclaw.community.core.service_bot.services.publish_flow.errors import (
    PublishFlowServiceError,
)
from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    pass


logger = get_logger()


class DraftRestoreOpsMixin:
    """Restore a historical artifact into the editable draft container."""

    async def execute_restore_draft(
        self,
        *,
        draft_publish_id: int,
        source_publish_id: int,
        operator: str,
    ) -> dict:
        """Restore the draft without advancing the publish state machine."""
        draft = self._publish_service.get_publish_by_id(draft_publish_id)
        source = self._publish_service.get_publish_by_id(source_publish_id)
        if not draft or not source:
            raise PublishNotFoundError(
                "Draft/source publish not found: "
                f"{draft_publish_id}/{source_publish_id}"
            )
        if draft.status != PublishStatus.DRAFT:
            raise PublishStatusInvalidError(
                f"Only DRAFT can be restored, got {draft.status}"
            )
        if draft.last_pub_id != source_publish_id:
            raise PublishFlowServiceError("恢复来源不是当前草稿的上一版本")

        source_ext = copy.deepcopy(source.ext or {})
        migration_path = source_ext.get("migration_path")
        if not migration_path:
            raise PublishFlowServiceError("上一版本缺少 migration_path 构造物")

        owner_id = self._get_owner_id(draft)
        bot = self._bot_service.get_bot(
            bot_id=draft.source_bot_id,
            user_id=owner_id,
        )
        if not bot:
            raise PublishFlowServiceError(f"Bot不存在: {draft.source_bot_id}")

        binding_id = bot.get("binding_id")
        if not binding_id:
            raise PublishFlowServiceError("草稿 Bot 缺少 binding_id")
        binding = self._publish_service.get_device_binding_by_id(binding_id)
        if not binding or not binding.device_id:
            raise PublishFlowServiceError(
                "草稿设备绑定不存在或缺少 device_id: "
                f"binding_id={binding_id}"
            )
        if binding.status != DeviceBindingStatus.ACTIVE.value:
            raise PublishFlowServiceError(
                f"草稿容器未就绪: binding_id={binding_id}, status={binding.status}"
            )

        result = await self._build_service.restore_draft_async(
            bot=bot,
            source_version=source.version,
            artifact_ext=source_ext,
        )

        logger.info(
            "[DraftRestoreOpsMixin.execute_restore_draft] restored: "
            "draft_publish_id=%s source_publish_id=%s binding_id=%s type=%s",
            draft_publish_id,
            source_publish_id,
            binding_id,
            result.get("restore_type"),
        )
        return {
            "draft_binding_id": binding_id,
            "status": "success",
            **result,
        }
