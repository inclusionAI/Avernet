"""Eval-environment publish/teardown + status query, mixed in."""
from __future__ import annotations

from typing import Any, Dict

from agentclaw.community.core.service_bot.repository.models import (
    PublishOperationKind,
    PublishStatus,
)
from agentclaw.community.core.service_bot.services.bot_publish_service import (
    PublishNotFoundError,
)
from agentclaw.community.core.service_bot.services.arka_image_pin import (
    resolve_publish_image_pin,
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
        image_pin = resolve_publish_image_pin(publish_record)

        ext_info = {}
        if biz_id:
            ext_info["biz_id"] = biz_id

        # (#197) Crash-safe issuance via the operation runner. Eval is a CREATION
        # (no bot to adopt), so a crash after the BaaS create but before the id is
        # recorded re-creates a bounded orphan — the in-flight PENDING op makes that
        # orphan observable. The workflow id + bot_uuid land in the ledger.
        op = self._operation_runner.open_operation(
            publish_id=publish_id,
            kind=PublishOperationKind.EVAL_PUBLISH,
            stage=publish_stage,
            operator=operator,
            params={"biz_id": biz_id} if biz_id else None,
        )

        async def _issue():
            return await self._build_service.release_async(
                bot=bot,
                user_id=owner_id,
                migration_path=migration_path,
                device_count=1,
                publish_stage=publish_stage,
                version=str(publish_record.version or 1),
                delivery=delivery,
                ext_info=ext_info,
                docker_image=image_pin.docker_image,
            )

        op = await self._operation_runner.acquire_workflow(op, _issue)
        bot_uuid = op.bot_uuid
        baas_publish_id = op.baas_publish_id
        if not bot_uuid:
            raise PublishFlowServiceError("Eval-environment release failed: BaaS returned no bot_uuid")
        if baas_publish_id is None:
            # Defensive: completing with None would hide an un-recorded workflow now
            # that complete() also accepts PENDING (#197).
            raise PublishFlowServiceError(
                f"Eval release did not record a BaaS publish_id: publish_id={publish_id}"
            )
        self._operation_runner.complete_operation(op)

        # Enqueue the TTL teardown safety net: if the quality task never reaches
        # to_env_released, the orphaned eval bot is destroyed after the TTL. The
        # explicit post-eval teardown (delay 0) converges on the same runner op.
        from agentclaw.community.core.service_bot.services.publish_flow.tasks import (
            enqueue_eval_teardown,
            _EVAL_TEARDOWN_TTL_SECONDS,
        )

        enqueue_eval_teardown(
            self._task_queue_service,
            publish_id=publish_id,
            bot_uuid=bot_uuid,
            operator=operator,
            delay_seconds=_EVAL_TEARDOWN_TTL_SECONDS,
        )

        # All-auto approval (#197): the eval CREATE workflow is auto-approved
        # server-side — no client approve.
        result = {
            "success": True,
            "publish_id": publish_id,
            "stage": publish_stage.value,
            "bot_uuid": bot_uuid,
            "baas_publish_id": baas_publish_id,
            "baas_bot_status": None,
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
        publish_id: int = 0,
    ) -> dict:
        """Tear down the eval environment (#197: enqueue the durable teardown).

        Depends only on the eval environment's own bot_uuid; a caller may pass the
        originating ``publish_id`` so this early teardown and the TTL safety net
        (enqueued at ``eval_publish``) converge on the SAME runner op — otherwise it
        defaults to 0. Enqueuing (rather than destroying inline) makes the teardown
        crash-safe and idempotent via the ``eval_teardown`` op; the sync caller does
        not block on the BaaS destroy. Does not touch the main publish record.
        """
        if not bot_uuid:
            raise PublishFlowServiceError("bot_uuid must not be empty")

        from agentclaw.community.core.service_bot.services.publish_flow.tasks import (
            enqueue_eval_teardown,
        )

        enqueue_eval_teardown(
            self._task_queue_service,
            publish_id=publish_id,
            bot_uuid=bot_uuid,
            operator=operator,
        )
        result = {
            "success": True,
            "bot_uuid": bot_uuid,
            "message": "Eval environment teardown enqueued",
        }
        logger.info(
            f"[PublishFlowService.eval_teardown] Teardown enqueued: {result}"
        )
        return result

    async def execute_eval_teardown(
        self,
        *,
        publish_id: int,
        bot_uuid: str,
        operator: str = "system",
    ) -> dict:
        """Durable eval teardown work (#197): destroy the eval bot through the
        operation runner so a crash-resume adopts the in-doubt DESTROY workflow
        (existing bot) instead of issuing a second destroy.

        Keyed on ``(publish_id, eval_teardown)`` with ``bot_uuid``, so the TTL and
        the explicit teardown that share a publish_id resume the same op. Returns
        ``{success, bot_uuid, baas_publish_id}``."""
        if not bot_uuid:
            raise PublishFlowServiceError("bot_uuid must not be empty")

        op = self._operation_runner.open_operation(
            publish_id=publish_id,
            kind=PublishOperationKind.EVAL_TEARDOWN,
            stage=PublishStage.EVAL,
            bot_uuid=bot_uuid,
            operator=operator,
        )

        async def _issue():
            return self._baas_service.destroy_bot(
                bot_uuid=bot_uuid,
                operator=operator,
                request_id=op.request_id,
            )

        # acquire_workflow exceptions propagate (durable retry resumes the same
        # non-terminal op → adopt-by-query), mirroring execute_restart.
        op = await self._operation_runner.acquire_workflow(op, _issue)
        destroy_publish_id = op.baas_publish_id
        self._operation_runner.complete_operation(op)

        result = {
            "success": True,
            "bot_uuid": bot_uuid,
            "baas_publish_id": destroy_publish_id,
            "message": "Eval environment teardown submitted",
        }
        logger.info(
            f"[PublishFlowService.execute_eval_teardown] Destroy success: {result}"
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
