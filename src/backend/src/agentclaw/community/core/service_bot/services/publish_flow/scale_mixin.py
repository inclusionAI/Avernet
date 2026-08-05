"""Service-bot scale operations + device-count resolution, mixed in."""
from __future__ import annotations

from typing import Any

from agentclaw.community.core.service_bot.repository.models import (
    BotPublishRecord,
    PublishOperationKind,
    PublishStatus,
)
from agentclaw.community.core.service_bot.services.bot_publish_service import (
    PublishNotFoundError,
    PublishStatusInvalidError,
)
from agentclaw.community.core.service_bot.services.publish_flow.errors import (
    PublishFlowServiceError,
)
from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.log import get_logger

logger = get_logger()

DEVICE_COUNT_CONFIG_BUSINESS_CODE = "service_bot_device_count"
DEVICE_COUNT_DEFAULT_PARAM_CODE = "default"


class ScaleMixin:
    """Service-bot scale operations + device-count resolution, mixed in."""

    async def scale_bot(
        self,
        publish_id: int,
        operator: str = "system",
    ) -> dict:
        """Initiate a scale operation for a published service bot.

        Currently only supports service bots in the online stage (SUCCESS / ONLINE_PUB).
        Obtains bot_uuid from the publish record's online binding, then issues the
        BaaS scale through the operation runner (#197): a crash after the scale call
        but before the ext write adopts the in-doubt SCALE workflow by query on the
        next invocation instead of issuing a second scale. The runner supplies the
        deterministic, correlation-only request id (the old wall-clock id is gone).
        """
        logger.info(
            f"[PublishFlowService.scale_bot] called: publish_id={publish_id}, "
            f"operator={operator}"
        )

        publish_record = self._publish_service.get_publish_by_id(publish_id)
        if not publish_record:
            raise PublishNotFoundError(f"Publish order not found: {publish_id}")

        owner_id = self._get_owner_id(publish_record)
        bot = self._bot_service.get_bot(
            bot_id=publish_record.source_bot_id,
            user_id=owner_id,
        )
        if not bot:
            raise PublishFlowServiceError(f"Bot does not exist: {publish_record.source_bot_id}")

        active_engine = (bot.get("active_engine") or "").strip().lower()
        if not self._provider_behavior(bot).supports_scale:
            return {
                "success": True,
                "message": "Service bots on the teclaw engine do not support scaling",
                "publish_id": publish_id,
                "engine": active_engine,
                "supported": True,
            }

        current_status = PublishStatus(publish_record.status)
        if current_status != PublishStatus.SUCCESS:
            raise PublishStatusInvalidError(
                f"The current status {current_status} does not support scale operations"
            )

        ext = self._get_latest_ext(publish_id)
        online_binding_id = (ext.get("binding") or {}).get(PublishStage.ONLINE.value)
        if not online_binding_id:
            raise PublishFlowServiceError(
                f"Binding info for the online stage not found: publish_id={publish_id}"
            )

        binding = self._publish_service.get_device_binding_by_id(online_binding_id)
        if not binding or not binding.device_id:
            raise PublishFlowServiceError(
                f"Device binding record does not exist or is missing device_id: binding_id={online_binding_id}"
            )

        bot_uuid = binding.device_id
        target_count = self._resolve_scale_target_count(publish_record)
        image_pin = self.resolve_publish_image_pin(publish_record)
        pinned_image = image_pin.docker_image
        scale_config = (
            {"deploy_config": {"docker_image": pinned_image}}
            if pinned_image
            else None
        )

        # (#197) Crash-safe issuance via the operation runner (existing bot →
        # adopt-by-query on resume, never a second scale). The op's deterministic
        # request id is passed to BaaS as the correlation id.
        op = self._operation_runner.open_operation(
            publish_id=publish_id,
            kind=PublishOperationKind.SCALE,
            stage=PublishStage.ONLINE,
            bot_uuid=bot_uuid,
            operator=operator,
        )

        async def _issue():
            scale_kwargs = {
                "bot_uuid": bot_uuid,
                "owner_id": operator,
                "request_id": op.request_id,
                "target_count": target_count,
                "auto_approve_publish": True,
            }
            if scale_config is not None:
                scale_kwargs["config"] = scale_config
            return self._baas_service.scale_bot(
                **scale_kwargs,
            )

        op = await self._operation_runner.acquire_workflow(op, _issue)
        scale_publish_id = op.baas_publish_id
        if scale_publish_id is None:
            # Defensive: acquire_workflow guarantees a recorded id for a BaaS op
            # (issue/adopt), so completing with None would hide an un-recorded
            # workflow now that complete() also accepts PENDING (#197).
            raise PublishFlowServiceError(
                f"scale did not record a BaaS publish_id: publish_id={publish_id}"
            )

        # Dual-write ext.scale.publish_id (the ledger op is the source of truth;
        # ext is the read handle sync_scale_progress falls back to).
        if scale_publish_id is not None:
            def _mutate(latest_ext: dict) -> None:
                latest_ext.setdefault("scale", {})["publish_id"] = scale_publish_id

            self._mutate_and_update_ext(
                publish_id=publish_id,
                mutator=_mutate,
            )

        self._operation_runner.complete_operation(op)

        logger.info(
            f"[PublishFlowService.scale_bot] scale submitted: publish_id={publish_id}, "
            f"bot_uuid={bot_uuid}, target_count={target_count}, "
            f"scale_publish_id={scale_publish_id}"
        )

        return {
            "success": True,
            "message": "Scale task submitted",
            "publish_id": publish_id,
            "bot_uuid": bot_uuid,
            "target_count": target_count,
            "baas_publish_id": scale_publish_id,
            "data": {"publish_id": scale_publish_id},
        }

    def _resolve_scale_target_count(self, publish_record: BotPublishRecord) -> int:
        """Resolve the target device count for scaling a service Bot."""
        owner_id = self._get_owner_id(publish_record)
        bot = self._bot_service.get_bot(
            bot_id=publish_record.source_bot_id,
            user_id=owner_id,
        )
        if not bot:
            raise PublishFlowServiceError(f"Bot does not exist: {publish_record.source_bot_id}")

        ext_count = self._read_device_count_from_bot_ext(bot.get("ext"))
        if ext_count is not None:
            logger.info(
                "[PublishFlowService._resolve_scale_target_count] use bot ext device_count=%s for bot_id=%s",
                ext_count,
                publish_record.source_bot_id,
            )
            return ext_count

        default_count = self._get_default_scale_target_count()
        if default_count is not None:
            logger.info(
                "[PublishFlowService._resolve_scale_target_count] use common_config default device_count=%s for bot_id=%s",
                default_count,
                publish_record.source_bot_id,
            )
            return default_count

        raise PublishFlowServiceError(
            f"service_bot_config.device_count not found, and no default device_count is configured: bot_id={publish_record.source_bot_id}"
        )

    def _read_device_count_from_bot_ext(self, bot_ext: dict[str, Any] | None) -> int | None:
        if not isinstance(bot_ext, dict):
            return None
        service_bot_config = bot_ext.get("service_bot_config")
        if not isinstance(service_bot_config, dict):
            return None
        return self._normalize_device_count(
            service_bot_config.get("device_count"),
            source="bot.ext.service_bot_config.device_count",
        )

    def _get_default_scale_target_count(self) -> int | None:
        value = self._common_config_service.get_value(
            business_code=DEVICE_COUNT_CONFIG_BUSINESS_CODE,
            param_code=DEVICE_COUNT_DEFAULT_PARAM_CODE,
            env=get_current_env(),
            default=None,
        )
        logger.info(
            "[PublishFlowService._get_default_scale_target_count] common_config value=%s",
            value,
        )
        return self._normalize_device_count(
            value,
            source="common_config.service_bot_device_count.default",
        )

    def _normalize_device_count(self, value: Any, *, source: str) -> int | None:
        if value is None or value == "":
            return None
        try:
            count = int(value)
        except (TypeError, ValueError):
            logger.warning(
                "[PublishFlowService._normalize_device_count] invalid device_count from %s: %r",
                source,
                value,
            )
            return None
        if count < 1:
            logger.warning(
                "[PublishFlowService._normalize_device_count] non-positive device_count from %s: %r",
                source,
                value,
            )
            return None
        return count
