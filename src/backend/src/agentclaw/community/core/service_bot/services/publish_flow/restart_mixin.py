"""Bot restart (re-deploy) operations, mixed into PublishFlowService."""
from __future__ import annotations

import asyncio
from typing import Any, Dict

from agentclaw.community.core.service_bot.repository.models import (
    BotPublishRecord,
    PublishStatus,
)
from agentclaw.community.core.service_bot.services.publish_flow.errors import (
    PublishFlowServiceError,
)
from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.log import get_logger

logger = get_logger()

DEVICE_COUNT_CONFIG_BUSINESS_CODE = "service_bot_device_count"
DEVICE_COUNT_DEFAULT_PARAM_CODE = "default"


class RestartMixin:
    """Bot restart (re-deploy) operations, mixed into PublishFlowService."""

    def restart_bot(
        self,
        publish_id: int,
        operator: str = "system",
    ) -> dict:
        """Restart the Bot (executed asynchronously).

        Determines the current stage from the publish record status, obtains the bot_uuid
        from the binding info, and calls the BaaS-layer upgrade interface to re-deploy.
        This method runs asynchronously via asyncio.create_task and does not wait for the result.

        Flow:
        1. Query the publish record by publish_id
        2. Determine the current stage from the publish record status (VERIFY/ONLINE)
        3. Get the binding_id for the corresponding stage from ext
        4. Query the device_binding record by binding_id to obtain device_id (i.e. bot_uuid)
        5. Call BotBuildService.upgrade_async() to re-deploy the Bot

        Args:
            publish_id: Publish record ID
            operator: Operator, defaults to "system"

        Returns:
            dict: Restart result, containing:
                - success: Whether the restart request was submitted successfully
                - message: Result message
                - stage: Publish stage (returned on success)
        """
        logger.info(
            f"[PublishFlowService.restart_bot] called: publish_id={publish_id}, operator={operator}"
        )

        # Step 1: Query the publish record
        publish_record = self._publish_service.get_publish_by_id(publish_id)
        if not publish_record:
            logger.warning(
                f"[PublishFlowService.restart_bot] Publish record not found: publish_id={publish_id}"
            )
            return {
                "success": False,
                "message": f"Publish record not found: publish_id={publish_id}",
            }

        # Step 2: Determine the current stage from the status
        current_status = PublishStatus(publish_record.status)
        stage = self._determine_restart_stage(current_status)
        if not stage:
            logger.warning(
                f"[PublishFlowService.restart_bot] "
                f"Cannot restart for status: {current_status}, publish_id={publish_id}"
            )
            return {
                "success": False,
                "message": f"Current status {current_status} does not support restart operation",
                "status": current_status,
            }

        # Step 3: Reset the BaaS restart publish record for the current stage
        ext = self._get_latest_ext(publish_id)
        if "restart" in ext and stage.value in ext.get("restart", {}):
            try:
                def _mutate(latest_ext: dict) -> None:
                    restart_info = latest_ext.get("restart", {})
                    if stage.value in restart_info:
                        restart_info.pop(stage.value, None)
                    if restart_info:
                        latest_ext["restart"] = restart_info
                    else:
                        latest_ext.pop("restart", None)

                ext = self._mutate_and_update_ext(
                    publish_id=publish_id,
                    mutator=_mutate,
                )
            except Exception as e:
                logger.warning(
                    f"[PublishFlowService.restart_bot] "
                    f"Failed to reset restart_publish_id: publish_id={publish_id}, error={e}"
                )
                ext = self._get_latest_ext(publish_id)

        # Step 4: Get the binding_id for the corresponding stage from ext
        binding_info = ext.get("binding", {})
        binding_id = binding_info.get(stage.value)

        if not binding_id:
            logger.warning(
                f"[PublishFlowService.restart_bot] "
                f"No binding_id found for stage={stage.value}, publish_id={publish_id}"
            )
            return {
                "success": False,
                "message": f"No binding info found for stage {stage.value}",
                "stage": stage.value,
            }

        # Step 5: Query the device_binding record by binding_id
        binding = self._publish_service.get_device_binding_by_id(binding_id)
        if not binding:
            logger.warning(
                f"[PublishFlowService.restart_bot] Device binding not found: binding_id={binding_id}"
            )
            return {
                "success": False,
                "message": f"Device binding record not found: binding_id={binding_id}",
            }

        bot_uuid = binding.device_id
        if not bot_uuid:
            logger.warning(
                f"[PublishFlowService.restart_bot] No device_id in binding: binding_id={binding_id}"
            )
            return {
                "success": False,
                "message": f"Device binding record has no device_id: binding_id={binding_id}",
            }

        bot_service = self._bot_service
        bot = bot_service.get_bot(bot_id=publish_record.source_bot_id, user_id=publish_record.owner_id)
        if not bot:
            logger.warning(
                f"[PublishFlowService.restart_bot] Bot not found: bot_id={publish_record.source_bot_id}"
            )
            return {
                "success": False,
                "message": f"Bot not found: {publish_record.source_bot_id}",
            }

        migration_path = ext.get("migration_path")
        config_artifact = ext.get("config_artifact")
        if not migration_path and not config_artifact:
            logger.warning(
                f"[PublishFlowService.restart_bot] No build artifact in ext: publish_id={publish_id}"
            )
            return {
                "success": False,
                "message": f"Publish record is missing build artifact: publish_id={publish_id}",
            }

        # Step 6: Execute the restart asynchronously
        asyncio.create_task(
            self._restart_bot_async(
                publish_id=publish_id,
                publish_record=publish_record,
                migration_path=migration_path,
                bot_uuid=bot_uuid,
                binding_id=binding_id,
                bot=bot,
                stage=stage,
                operator=operator,
            )
        )

        logger.info(
            f"[PublishFlowService.restart_bot] Restart task created: "
            f"publish_id={publish_id}, bot_uuid={bot_uuid}, stage={stage.value}, "
            f"operator={operator}, owner_id={publish_record.owner_id}"
        )

        return {
            "success": True,
            "message": f"Restart task submitted, stage: {stage.value}",
            "stage": stage.value,
            "bot_uuid": bot_uuid,
        }

    async def _restart_bot_async(
        self,
        publish_id: int,
        publish_record: BotPublishRecord,
        migration_path: str,
        bot_uuid: str,
        binding_id: int,
        bot: Dict[str, Any],
        stage: PublishStage,
        operator: str,
    ) -> None:
        """Execute the Bot restart asynchronously (re-deploy via the upgrade interface).

        Args:
            publish_id: Publish record ID
            publish_record: Publish record
            migration_path: Directory path after the Bot instance migration
            bot_uuid: Bot UUID
            bot: Bot info dictionary
            stage: Publish stage
            operator: Operator
        """
        logger.info(
            f"[PublishFlowService._restart_bot_async] Starting restart: "
            f"publish_id={publish_id}, bot_uuid={bot_uuid}, stage={stage.value}, "
            f"operator={operator}, owner_id={publish_record.owner_id}"
        )

        try:
            # Generate request_id (used for the later BaaS publish record approval)
            request_id = self._build_service.generate_request_id(
                bot=bot,
                publish_stage=f"restart_{stage.value}",
            )
            version = f"{publish_record.version}"

            # Compose the delivery artifact for THIS stage through the single seam
            # (STORED overrides slot: reproduce what was promoted, NOT a live
            # re-fetch). The seam reproduces the per-stage channels + stage stamp so a
            # restart of a non-latest stage never delivers another stage's channels —
            # the single-config-slot hazard is closed at the boundary.
            delivery = self._ext_state.compose_stored(publish_record.ext or {}, stage)
            restart_result = await self._build_service.upgrade_async(
                bot_uuid=bot_uuid,
                bot=bot,
                user_id=publish_record.owner_id,
                device_count=1,
                migration_path=migration_path,
                publish_stage=stage,
                version=version,
                delivery=delivery,
            )

            if restart_result.get("success") is False and restart_result.get("error_code") == "BOT_NOT_FOUND":
                logger.warning(
                    f"[PublishFlowService._restart_bot_async] "
                    f"Restart target bot not found, fallback to first release: "
                    f"publish_id={publish_id}, bot_uuid={bot_uuid}, stage={stage.value}"
                )
                restart_result = await self._build_service.release_async(
                    bot=bot,
                    user_id=publish_record.owner_id,
                    migration_path=migration_path,
                    device_count=1,
                    publish_stage=stage,
                    version=version,
                    # teclaw: the fallback must carry the same composed artifact,
                    # else create_teclaw_bot would receive an empty config.
                    delivery=delivery,
                )

            restart_publish_id = restart_result.get("publish_id")
            if not restart_publish_id:
                raise PublishFlowServiceError("BaaS-layer restart did not return publish_id")

            # Refresh the reused binding's teclaw status read handle to the
            # restart's publish workflow (no-op for non-teclaw; best-effort).
            self.refresh_publish_handle(
                binding_id, restart_publish_id
            )

            logger.info(
                f"[PublishFlowService._restart_bot_async] "
                f"Bot restart initiated: bot_uuid={bot_uuid}, stage={stage.value}, "
                f"publish_id={publish_id}, restart_publish_id={restart_publish_id}"
            )

            # Store restart_publish_id into ext: {"restart": {"<stage>": restart_publish_id}}
            try:
                def _mutate(ext: dict) -> None:
                    if "restart" not in ext:
                        ext["restart"] = {}
                    ext["restart"][stage.value] = restart_publish_id

                self._mutate_and_update_ext(
                    publish_id=publish_id,
                    mutator=_mutate,
                )
                logger.info(
                    f"[PublishFlowService._restart_bot_async] "
                    f"Restart publish_id saved to ext: publish_id={publish_id}, "
                    f"stage={stage.value}, restart_publish_id={restart_publish_id}"
                )
            except Exception as save_error:
                logger.warning(
                    f"[PublishFlowService._restart_bot_async] "
                    f"Failed to save restart_publish_id to ext: publish_id={publish_id}, "
                    f"error={save_error}"
                )

            # All-auto approval (#197): the restart workflow is auto-approved
            # server-side (upgrade/create payloads set auto_approve_publish=True)
            # — no client approve. (This whole path moves onto the durable task
            # queue + operation runner in Task 11.)
            logger.info(
                f"[PublishFlowService._restart_bot_async] "
                f"Bot restart submitted: bot_uuid={bot_uuid}, stage={stage.value}, "
                f"restart_publish_id={restart_publish_id}"
            )

        except Exception as e:
            logger.error(
                f"[PublishFlowService._restart_bot_async] "
                f"Failed to restart bot: publish_id={publish_id}, bot_uuid={bot_uuid}, error={e}"
            )

