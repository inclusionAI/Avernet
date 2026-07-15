"""Eval-environment publish/teardown + status query, mixed in."""
from __future__ import annotations

from typing import Any, Dict

from agentclaw.community.core.service_bot.repository.models import (
    PublishStatus,
)
from agentclaw.community.core.service_bot.services.bot_publish_service import (
    PublishNotFoundError,
)
from agentclaw.community.core.service_bot.services.publish_flow.errors import (
    PublishFlowServiceError,
)
from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.log import get_logger

logger = get_logger()

DEVICE_COUNT_CONFIG_BUSINESS_CODE = "service_bot_device_count"
DEVICE_COUNT_DEFAULT_PARAM_CODE = "default"


class EvalPublishMixin:
    """Eval-environment publish/teardown + status query, mixed in."""

    async def eval_publish(
        self,
        publish_id: int,
        operator: str,
        biz_id: str = "",
    ) -> dict:
        """Publish to the eval environment.

        The eval environment is a side branch outside the main publish flow:
        - it does not advance the main publish-record state machine
        - it does not write publish.ext / binding
        - it only reuses the publish record's build artifact + bot base info

        This path always targets the EVAL stage, so the stage is not a parameter.
        """
        # This flow is EVAL-only; the stage is fixed here rather than taken as an arg.
        publish_stage = PublishStage.EVAL
        logger.info(
            f"[PublishFlowService.eval_publish] Start release: "
            f"publish_id={publish_id}, operator={operator}"
        )

        publish_record = self._publish_service.get_publish_by_id(publish_id)
        if not publish_record:
            raise PublishNotFoundError(f"Publish order not found: {publish_id}")

        owner_id = self._get_owner_id(publish_record)
        migration_path = (publish_record.ext or {}).get("migration_path", "")
        config_artifact = (publish_record.ext or {}).get("config_artifact")
        if not migration_path and not config_artifact:
            raise PublishFlowServiceError("Build artifact path not found; run the build first")

        bot = self._bot_service.get_bot(
            bot_id=publish_record.source_bot_id,
            user_id=owner_id,
        )
        if not bot:
            raise PublishFlowServiceError(f"Bot not found: {publish_record.source_bot_id}")

        # Compose through the single delivery seam (LIVE overrides re-fetch); the raw
        # config_artifact read above is only the build-artifact presence guard. Eval
        # does not persist, so the applied overrides are discarded.
        delivery, _ = self._ext_state.compose_live(publish_record, publish_stage)

        ext_info = {}
        if biz_id:
            ext_info["biz_id"] = biz_id

        # Release to the eval environment.
        release_result = await self._build_service.release_async(
            bot=bot,
            user_id=owner_id,
            migration_path=migration_path,
            device_count=1,
            publish_stage=publish_stage,
            version=str(publish_record.version or 1),
            delivery=delivery,
            ext_info=ext_info,
        )

        bot_uuid = release_result.get("bot_uuid")
        baas_publish_id = release_result.get("publish_id")
        if not bot_uuid:
            raise PublishFlowServiceError("Eval-environment release failed: BaaS returned no bot_uuid")

        request_id = self._build_service.generate_request_id(
            bot=bot,
            publish_stage=publish_stage.value,
        )
        # Best-effort approve; skip when BaaS returned no publish workflow id.
        if baas_publish_id:
            self.approve_baas_publish(
                baas_publish_id=baas_publish_id,
                operator=operator,
                stage=publish_stage,
                request_id=request_id,
            )

        result = {
            "success": True,
            "publish_id": publish_id,
            "stage": publish_stage.value,
            "bot_uuid": bot_uuid,
            "baas_publish_id": baas_publish_id,
            "baas_bot_status": release_result.get("status"),
        }
        logger.info(
            f"[PublishFlowService.eval_publish] Release success: {result}"
        )
        return result

    def eval_teardown(
        self,
        bot_uuid: str,
        *,
        operator: str = "system",
        request_bot: dict | None = None,
    ) -> dict:
        """Tear down the eval environment.

        Depends only on the eval environment's own bot_uuid; a caller may later
        read/pass it from a dedicated eval-task table. Does not touch the main
        publish record's ext/binding.
        """
        if not bot_uuid:
            raise PublishFlowServiceError("bot_uuid must not be empty")

        request_bot = request_bot or {"bot_id": bot_uuid}
        request_id = self._build_service.generate_request_id(
            bot=request_bot,
            publish_stage="destroy_eval",
        )
        destroy_result = self._baas_service.destroy_bot(
            bot_uuid=bot_uuid,
            operator=operator,
            request_id=request_id,
        )
        destroy_publish_id = destroy_result.get("publish_id")
        # Best-effort approve; skip when BaaS returned no publish workflow id.
        if destroy_publish_id:
            self.approve_baas_publish(
                baas_publish_id=destroy_publish_id,
                operator=operator,
                stage=PublishStage.EVAL,
                request_id=request_id,
            )
        result = {
            "success": True,
            "bot_uuid": bot_uuid,
            "baas_publish_id": destroy_publish_id,
            "message": "Eval environment teardown submitted",
        }
        logger.info(
            f"[PublishFlowService.eval_teardown] Destroy success: {result}"
        )
        return result

    def get_publish_bot_status(
        self,
        publish_id: int,
        stage: PublishStage,
    ) -> Dict[str, Any]:
        """Query the BaaS bot status / detail for the given publish-record stage."""
        logger.info(
            f"[PublishFlowService.get_publish_bot_status] Start query: publish_id={publish_id}, stage={stage.value}"
        )

        publish_record = self._publish_service.get_publish_by_id(publish_id)
        if not publish_record:
            raise PublishNotFoundError(f"Publish order not found: {publish_id}")

        ext = publish_record.ext or {}
        binding_info = ext.get("binding", {})
        binding_id = binding_info.get(stage.value)
        if not binding_id:
            raise PublishFlowServiceError(f"No binding found for the {stage.value} stage")

        binding = self._publish_service.get_device_binding_by_id(binding_id)
        if not binding:
            raise PublishFlowServiceError(f"Binding record not found: binding_id={binding_id}")

        bot_uuid = getattr(binding, "device_id", "")
        if not bot_uuid:
            raise PublishFlowServiceError(f"Binding record missing bot_uuid: binding_id={binding_id}")

        baas_bot = self._baas_service.get_bot(bot_uuid=bot_uuid)
        result = {
            "publish_id": publish_id,
            "stage": stage.value,
            "binding_id": binding_id,
            "bot_uuid": bot_uuid,
            "baas_bot_status": baas_bot.get("status"),
        }
        logger.info(
            f"[PublishFlowService.get_publish_bot_status] Query success: {result}"
        )
        return result

    def destroy_publish_history(
        self,
        publish_id: int,
        stage: PublishStage,
    ) -> dict:
        """Destroy publish history.

        Note: before calling this, advance the bot to the appropriate status:
        - verify stage (VERIFY): first roll back to draft (DRAFT)
        - online stage (ONLINE): first advance to offline (RELEASED)

        Destroy flow:
        1. Look up the BotPublishRecord by publish_id
        2. Call _destroy_bot_by_stage to destroy the BaaS-layer bot for the stage

        Args:
            publish_id: AgentClaw-layer publish record id
            stage: Publish stage (VERIFY/ONLINE)

        Returns:
            dict: Destroy result, containing:
                - success: whether it succeeded
                - bot_destroyed: whether a bot was destroyed
                - message: result message

        Raises:
            PublishNotFoundError: publish record not found
            PublishFlowServiceError: destroy failed
        """
        logger.info(f"[PublishFlowService.destroy_publish_history] Starting destroy: publish_id={publish_id}, stage={stage.value}")

        # Step 1: look up the publish record
        publish_record = self._publish_service.get_publish_by_id(publish_id)
        if not publish_record:
            raise PublishNotFoundError(f"Publish order not found: {publish_id}")

        current_status = PublishStatus(publish_record.status)

        # Check status: only DRAFT and RELEASED can be destroyed.
        allowed_statuses = [PublishStatus.DRAFT, PublishStatus.RELEASED]
        if current_status not in allowed_statuses:
            raise PublishFlowServiceError(
                f"Publish record status does not allow destroy: current status={current_status}, "
                f"allowed statuses: {[s.value for s in allowed_statuses]}"
            )

        result = {
            "success": True,
            "bot_destroyed": False,
            "message": "",
        }

        # Step 2: destroy the BaaS-layer bot
        try:
            self._destroy_bot_by_stage(publish_record, stage)
            result["bot_destroyed"] = True
            logger.info(
                f"[PublishFlowService.destroy_publish_history] "
                f"Bot destroyed: publish_id={publish_id}, stage={stage.value}"
            )

        except Exception as e:
            logger.warning(
                f"[PublishFlowService.destroy_publish_history]"
                f"Failed to destroy BaaS bots: publish_id={publish_id}, stage={stage.value}, error={e}"
            )
            # A failed bot destroy does not block the overall flow.

        result["message"] = f"Publish history destroy completed: publish_id={publish_id}, stage={stage.value}"

        logger.info(f"[PublishFlowService.destroy_publish_history] Destroy completed: {result}")

        return result
