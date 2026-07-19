"""Bot restart (re-deploy) operations, mixed into PublishFlowService."""
from __future__ import annotations

from agentclaw.community.core.service_bot.repository.models import (
    PublishOperationKind,
    PublishStatus,
)
from agentclaw.community.core.service_bot.services.publish_flow.operation_runner import (
    PublishOperationError,
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
        """Submit a Bot restart (durable, crash-safe).

        Determines the current stage from the publish record status, validates the
        binding/bot/artifact, and enqueues the durable restart task (#197). The
        actual re-deploy runs in ``execute_restart`` through the operation runner —
        this method returns as soon as the task is submitted (it does not wait for
        the re-deploy). Replaces the former fire-and-forget ``asyncio.create_task``.

        Flow:
        1. Resolve + validate the target (record → stage → binding → bot → artifact)
        2. Enqueue the durable restart task

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

        # Resolve + validate the restart target (record → stage → binding → bot →
        # artifact). On any failure ``error`` is the response dict to return.
        error, stage, bot_uuid = self._resolve_restart_request(publish_id)
        if error is not None:
            return error

        # (#197) Enqueue the DURABLE restart task instead of a fire-and-forget
        # asyncio task. The handler re-resolves and runs the re-deploy through the
        # operation runner (crash-safe, idempotent).
        from agentclaw.community.core.service_bot.services.publish_flow.tasks import (
            enqueue_restart,
        )

        enqueue_restart(
            self._task_queue_service,
            publish_id=publish_id,
            stage=stage.value,
            operator=operator,
        )

        logger.info(
            f"[PublishFlowService.restart_bot] Restart task enqueued: "
            f"publish_id={publish_id}, bot_uuid={bot_uuid}, stage={stage.value}, "
            f"operator={operator}"
        )

        return {
            "success": True,
            "message": f"Restart task submitted, stage: {stage.value}",
            "stage": stage.value,
            "bot_uuid": bot_uuid,
        }

    def _resolve_restart_request(self, publish_id: int):
        """Validate + resolve a restart request from ``publish_id``.

        Returns ``(error, stage, bot_uuid)``: on failure ``error`` is the response
        dict (``success=False`` + message) and stage/bot_uuid are None; on success
        ``error`` is None and stage/bot_uuid are populated. The build-artifact
        presence is also checked here so the durable handler always has one."""
        publish_record = self._publish_service.get_publish_by_id(publish_id)
        if not publish_record:
            logger.warning(
                f"[PublishFlowService.restart_bot] Publish record not found: publish_id={publish_id}"
            )
            return {
                "success": False,
                "message": f"Publish record not found: publish_id={publish_id}",
            }, None, None

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
            }, None, None

        # (#197) The previous ext.restart marker is NO LONGER cleared here.
        # Clearing it before the new workflow id was recorded was a crash hazard
        # (a crash after clear left no marker at all). The durable restart op now
        # owns idempotency; the marker is dual-written by ``execute_restart`` once
        # the new workflow id is recorded.
        ext = self._get_latest_ext(publish_id)

        binding_id = (ext.get("binding", {}) or {}).get(stage.value)
        if not binding_id:
            logger.warning(
                f"[PublishFlowService.restart_bot] "
                f"No binding_id found for stage={stage.value}, publish_id={publish_id}"
            )
            return {
                "success": False,
                "message": f"No binding info found for stage {stage.value}",
                "stage": stage.value,
            }, None, None

        binding = self._publish_service.get_device_binding_by_id(binding_id)
        if not binding:
            logger.warning(
                f"[PublishFlowService.restart_bot] Device binding not found: binding_id={binding_id}"
            )
            return {
                "success": False,
                "message": f"Device binding record not found: binding_id={binding_id}",
            }, None, None

        bot_uuid = binding.device_id
        if not bot_uuid:
            logger.warning(
                f"[PublishFlowService.restart_bot] No device_id in binding: binding_id={binding_id}"
            )
            return {
                "success": False,
                "message": f"Device binding record has no device_id: binding_id={binding_id}",
            }, None, None

        bot = self._bot_service.get_bot(
            bot_id=publish_record.source_bot_id, user_id=publish_record.owner_id
        )
        if not bot:
            logger.warning(
                f"[PublishFlowService.restart_bot] Bot not found: bot_id={publish_record.source_bot_id}"
            )
            return {
                "success": False,
                "message": f"Bot not found: {publish_record.source_bot_id}",
            }, None, None

        if not ext.get("migration_path") and not ext.get("config_artifact"):
            logger.warning(
                f"[PublishFlowService.restart_bot] No build artifact in ext: publish_id={publish_id}"
            )
            return {
                "success": False,
                "message": f"Publish record is missing build artifact: publish_id={publish_id}",
            }, None, None

        return None, stage, bot_uuid

    async def execute_restart(
        self,
        publish_id: int,
        stage: str,
        operator: str,
    ) -> dict:
        """Durable Bot restart work (#197): re-deploy the existing bot via the
        upgrade interface, through the operation runner so a crash-resume adopts
        the in-doubt restart workflow instead of issuing a second one.

        Re-resolves everything from ``publish_id`` (the durable task carries only
        ids), then: open a ``restart`` op keyed to the bot, acquire the workflow
        (upgrade; BOT_NOT_FOUND falls back to a create with the same artifact),
        dual-write ``ext.restart.<stage>`` for the status read, refresh the teclaw
        read handle, and complete the op. Returns ``{success, message}``."""
        stage_enum = PublishStage(stage)
        publish_record = self._publish_service.get_publish_by_id(publish_id)
        if not publish_record:
            return {"success": False, "message": f"Publish record not found: {publish_id}"}

        ext = self._get_latest_ext(publish_id)
        binding_id = (ext.get("binding") or {}).get(stage_enum.value)
        if not binding_id:
            return {"success": False, "message": f"No binding for stage {stage_enum.value}"}
        binding = self._publish_service.get_device_binding_by_id(binding_id)
        if not binding or not binding.device_id:
            return {"success": False, "message": f"Device binding missing device_id: {binding_id}"}
        bot_uuid = binding.device_id

        bot = self._bot_service.get_bot(
            bot_id=publish_record.source_bot_id, user_id=publish_record.owner_id
        )
        if not bot:
            return {"success": False, "message": f"Bot not found: {publish_record.source_bot_id}"}

        migration_path = ext.get("migration_path")
        config_artifact = ext.get("config_artifact")
        if not migration_path and not config_artifact:
            return {"success": False, "message": f"Missing build artifact: publish_id={publish_id}"}

        version = f"{publish_record.version}"
        # STORED overrides slot: reproduce what was promoted (not a live re-fetch),
        # so restarting a non-latest stage never delivers another stage's channels.
        delivery = self._ext_state.compose_stored(publish_record.ext or {}, stage_enum)

        op = self._operation_runner.open_operation(
            publish_id=publish_id,
            kind=PublishOperationKind.RESTART,
            stage=stage_enum,
            bot_uuid=bot_uuid,
            operator=operator,
        )

        async def _issue():
            restart_result = await self._build_service.upgrade_async(
                bot_uuid=bot_uuid,
                bot=bot,
                user_id=publish_record.owner_id,
                device_count=1,
                migration_path=migration_path,
                publish_stage=stage_enum,
                version=version,
                delivery=delivery,
            )
            if (
                restart_result.get("success") is False
                and restart_result.get("error_code") == "BOT_NOT_FOUND"
            ):
                # NOTE (#197, known limitation): the BOT_NOT_FOUND fallback recreates
                # the bot inline. Because restart reuses the existing binding (which
                # still points at the gone bot_uuid) rather than minting a new one,
                # this recreate leg is NOT fully crash-idempotent — a crash in the
                # narrow window after the create but before the workflow is recorded
                # can re-create a second orphan bot on resume (adopt-by-query queries
                # the OLD, gone bot and finds nothing). This is a rare path
                # (restarting an already-destroyed bot) and a pre-existing shape;
                # a proper fix (mint a fresh binding + a dedicated recreate op, like
                # upgrade_release's first-release fallback) is tracked as a follow-up.
                logger.warning(
                    "[PublishFlowService.execute_restart] target bot not found, "
                    "fallback to first release: publish_id=%s bot_uuid=%s stage=%s",
                    publish_id, bot_uuid, stage_enum.value,
                )
                restart_result = await self._build_service.release_async(
                    bot=bot,
                    user_id=publish_record.owner_id,
                    migration_path=migration_path,
                    device_count=1,
                    publish_stage=stage_enum,
                    version=version,
                    delivery=delivery,
                )
            return restart_result

        # NOTE: acquire_workflow exceptions are NOT caught + failed here. A genuine
        # crash leaves the op non-terminal so the durable task retry re-runs and the
        # SAME op resumes → adopt-by-query (existing bot). Catching + fail_operation
        # would open a fresh attempt on retry and re-issue a second restart. A
        # transient BaaS error propagates → the task handler returns/raises and the
        # queue retries, converging via adoption. acquire_workflow guarantees a
        # recorded publish_id (raises PublishOperationError otherwise).
        op = await self._operation_runner.acquire_workflow(op, _issue)
        restart_publish_id = op.baas_publish_id
        if restart_publish_id is None:
            # Defensive: acquire_workflow guarantees a recorded id (issue/adopt);
            # completing with None would hide an un-recorded workflow now that
            # complete() also accepts PENDING (#197).
            raise PublishOperationError(
                f"restart did not record a BaaS publish_id: publish_id={publish_id}"
            )

        # Refresh the teclaw read handle to the restart workflow (best-effort).
        self.refresh_publish_handle(binding_id, restart_publish_id)

        # Dual-write ext.restart.<stage> for sync_restart_progress / #157 (the
        # ledger op is the source of truth; ext is the read handle).
        try:
            def _mutate(latest_ext: dict) -> None:
                latest_ext.setdefault("restart", {})[stage_enum.value] = restart_publish_id

            self._mutate_and_update_ext(publish_id=publish_id, mutator=_mutate)
        except Exception as save_error:
            logger.warning(
                "[PublishFlowService.execute_restart] failed to save ext.restart: "
                "publish_id=%s error=%s", publish_id, save_error,
            )

        self._operation_runner.complete_operation(op)
        logger.info(
            "[PublishFlowService.execute_restart] restart submitted: bot_uuid=%s "
            "stage=%s restart_publish_id=%s", bot_uuid, stage_enum.value, restart_publish_id,
        )
        return {"success": True, "message": f"Restart submitted, stage: {stage_enum.value}",
                "stage": stage_enum.value, "restart_publish_id": restart_publish_id}

