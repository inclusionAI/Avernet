"""Rollback deploy + BaaS bot teardown, mixed into PublishFlowService."""
from __future__ import annotations


from agentclaw.community.core.service_bot.repository.models import (
    BotPublishRecord,
    PublishStatus,
)
from agentclaw.community.core.service_bot.schemas.publish_schemas import PublishFlowResult
from agentclaw.community.core.service_bot.services.bot_publish_service import (
    PublishNotFoundError,
)
from agentclaw.community.core.service_bot.services.publish_flow.errors import (
    PublishFlowServiceError,
)
from agentclaw.community.core.service_bot.services.publish_flow.tasks import (
    enqueue_progress_poll,
)
from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.log import get_logger

logger = get_logger()

DEVICE_COUNT_CONFIG_BUSINESS_CODE = "service_bot_device_count"
DEVICE_COUNT_DEFAULT_PARAM_CODE = "default"


class RollbackOpsMixin:
    """Rollback deploy + BaaS bot teardown, mixed into PublishFlowService."""

    async def execute_rollback(
        self,
        current_publish_id: int,
        target_publish_id: int,
        operator: str,
    ) -> "PublishFlowResult":
        """Execute the rollback deployment.

        Re-deploys to online using the target version's configuration
        (migration_path/config_artifact/binding). The rollback deployment is performed
        on the target version; the frontend should track the deployment progress of target_publish_id.

        Args:
            current_publish_id: Current version ID (already changed to DRAFT, used only to obtain owner_id and bot_id)
            target_publish_id: Target version ID (rollback target, already changed to SUCCESS, deployed on this version)
            operator: Operator

        Returns:
            PublishFlowResult: Deployment result, with publish_id being target_publish_id
        """
        from agentclaw.community.core.service_bot.schemas.publish_schemas import PublishFlowResult

        logger.info(
            f"[PublishFlowService.execute_rollback] called: "
            f"current_publish_id={current_publish_id}, target_publish_id={target_publish_id}, "
            f"operator={operator}"
        )

        # 1. Get the target version record
        target_record = self._publish_service.get_publish_by_id(target_publish_id)
        if not target_record:
            raise PublishNotFoundError(f"Target publish record not found: {target_publish_id}")

        current_record = self._publish_service.get_publish_by_id(current_publish_id)
        if not current_record:
            raise PublishNotFoundError(f"Current publish record not found: {current_publish_id}")

        # 2. Get the target version's build artifact
        target_ext = self._get_latest_ext(target_publish_id)
        migration_path = target_ext.get("migration_path")
        config_artifact = target_ext.get("config_artifact")

        if not migration_path and not config_artifact:
            raise PublishFlowServiceError(
                f"Target version is missing build artifact: target_publish_id={target_publish_id}"
            )

        # 3. Get the online binding from the target version
        online_binding_id = target_ext.get("binding", {}).get(PublishStage.ONLINE.value)
        if not online_binding_id:
            raise PublishFlowServiceError(
                f"Target version is missing online binding: target_publish_id={target_publish_id}"
            )

        binding = self._publish_service.get_device_binding_by_id(online_binding_id)
        if not binding or not binding.device_id:
            raise PublishFlowServiceError(
                f"Device binding record not found or missing device_id: binding_id={online_binding_id}"
            )

        bot_uuid = binding.device_id

        # 4. Get Bot info (obtained from current_record, since it is the original publish record)
        owner_id = current_record.owner_id
        bot = self._bot_service.get_bot(bot_id=current_record.source_bot_id, user_id=owner_id)
        if not bot:
            raise PublishFlowServiceError(f"Bot not found: {current_record.source_bot_id}")

# Compose the delivery artifact for ONLINE through the single seam
        # (STORED overrides slot: reproduce what was promoted, NOT a live
        # re-fetch).  compose_stored reads the target version's stored online
        # channel engine_overrides (DingTalk config incl. card_template_id),
        # reproducing what that version had when it was promoted — NOT a live
        # re-fetch, which would deliver the very channel state the user is
        # rolling away from.  No stored overrides (pre-feature record) or no
        # config_artifact (ARCA) → the stamp+overlay no-ops, preserving the base.
        delivery = self._ext_state.compose_stored(target_ext, PublishStage.ONLINE)

        # 5. Call the BaaS upgrade interface to re-deploy
        version = f"{target_record.version}"
        upgrade_result = await self._build_service.upgrade_async(
            bot_uuid=bot_uuid,
            bot=bot,
            user_id=owner_id,
            device_count=1,
            migration_path=migration_path,
            publish_stage=PublishStage.ONLINE,
            version=version,
            delivery=delivery,
        )

        baas_publish_id = upgrade_result.get("publish_id")
        if not baas_publish_id:
            raise PublishFlowServiceError("BaaS-layer upgrade did not return publish_id")

        # 6. Update the target version record with the new BaaS publish record ID (rollback deployment is performed on the target version)
        if "publish" not in target_ext:
            target_ext["publish"] = {}
        target_ext["publish"][PublishStage.ONLINE.value] = baas_publish_id

        self._update_publish_status(
            publish_id=target_publish_id,
            target_status=PublishStatus.ONLINE_PUB,
            source_status=PublishStatus.SUCCESS,
            ext=target_ext,
        )

        # 7. Approve the BaaS publish record
        request_id = self._build_service.generate_request_id(
            bot=bot,
            publish_stage="rollback",
        )
        self.approve_baas_publish(
            baas_publish_id=baas_publish_id,
            operator=operator,
            stage=PublishStage.ONLINE,
            request_id=request_id,
        )

        # 8. Enqueue the durable progress poll on the TARGET record. Rollback parks
        # the target at ONLINE_PUB with ext.publish.online set but never passes
        # through verify_flow/online_release, so pre-#105 only user /sync polling
        # ever finished it. The poll's advance_publish_progress reads
        # ext.publish.online and drives ONLINE_PUB → SUCCESS (binding activation +
        # supersede via _handle_sync_success), so the rollback self-completes with
        # no user polling — and becomes crash-safe.
        enqueue_progress_poll(self._task_queue_service, publish_id=target_publish_id)

        logger.info(
            f"[PublishFlowService.execute_rollback] Rollback deployment initiated: "
            f"current_publish_id={current_publish_id}, target_publish_id={target_publish_id}, "
            f"bot_uuid={bot_uuid}, baas_publish_id={baas_publish_id}"
        )

        return PublishFlowResult(
            publish_id=target_publish_id,
            status=PublishStatus.ONLINE_PUB,
            message="Rollback publish submitted",
            action="rollback",
            target_publish_id=target_publish_id,
            bot_uuid=bot_uuid,
            baas_publish_id=str(baas_publish_id),
            device_binding_id=online_binding_id,
        )

    def _destroy_bot_by_stage(
        self,
        publish_record: BotPublishRecord,
        stage: PublishStage,
    ) -> None:
        """Destroy the bot instance for the specified stage.

        Args:
            publish_record: Publish record
            stage: Publish stage (VERIFY/ONLINE)
        """
        ext = publish_record.ext or {}
        binding_info = ext.get("binding", {})
        binding_id = binding_info.get(stage.value)

        if not binding_id:
            logger.warning(
                f"[PublishFlowService._destroy_bot_by_stage] "
                f"No binding_id found for stage={stage.value}, skipping destroy"
            )
            return

        try:
            # Query device_binding to obtain bot_uuid
            binding = self._publish_service.get_device_binding_by_id(binding_id)
            if not binding:
                logger.warning(
                    f"[PublishFlowService._destroy_bot_by_stage] "
                    f"Device binding not found: binding_id={binding_id}"
                )
                return

            bot_uuid = binding.device_id
            if not bot_uuid:
                logger.warning(
                    f"[PublishFlowService._destroy_bot_by_stage] "
                    f"No device_id in binding: binding_id={binding_id}"
                )
                return

            logger.info(
                f"[PublishFlowService._destroy_bot_by_stage] "
                f"Destroying bot: bot_uuid={bot_uuid}, stage={stage.value}"
            )

            # Generate request_id (uses a special marker for the destroy scenario)
            request_id = self._build_service.generate_request_id(
                bot={"entity_id": binding.entity_id, "entity_type": binding.entity_type, "bot_id": publish_record.source_bot_id},
                publish_stage=f"destroy_{stage.value}",
            )

            # Call BaaS to destroy the bot
            destroy_result = self._baas_service.stop_bot(
                bot_uuid=bot_uuid,
                operator="system",
                request_id=request_id,
            )

            destroy_publish_id = destroy_result.get("publish_id")
            logger.info(
                f"[PublishFlowService._destroy_bot_by_stage] "
                f"Bot destroy initiated: bot_uuid={bot_uuid}, stage={stage.value}, destroy_publish_id={destroy_publish_id}"
            )

            # Approve the destroy workflow record
            if destroy_publish_id:
                self.approve_baas_publish(
                    baas_publish_id=destroy_publish_id,
                    operator="system",
                    stage=stage,
                    request_id=request_id,
                )
                logger.info(
                    f"[PublishFlowService._destroy_bot_by_stage] "
                    f"Bot destroy approved: bot_uuid={bot_uuid}, stage={stage.value}, destroy_publish_id={destroy_publish_id}"
                )

            # Update device_binding status to RELEASED (DeviceBindingMixin owns
            # the binding write).
            self._release_binding(binding_id, destroy_publish_id=destroy_publish_id)

        except Exception as e:
            logger.warning(
                f"[PublishFlowService._destroy_bot_by_stage] "
                f"Failed to destroy bot: binding_id={binding_id}, stage={stage.value}, error={e}"
            )


