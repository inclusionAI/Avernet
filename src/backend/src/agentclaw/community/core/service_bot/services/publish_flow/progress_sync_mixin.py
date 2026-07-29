"""Progress-sync mixin for the publish flow.

The BaaS-publish progress polling and status advancement — the durable poll
task's engine (``advance_publish_progress``), the ``/scale/status`` and
``/restart_status`` entry points, and their SUCCESS/FAILURE handlers — split out
of ``PublishFlowService`` as a mixin. The user-facing ``/sync`` endpoint is a
read-only status report on the facade; only the poll task drives advancement
through this mixin. It shares ``self`` with the facade (same instance, same
collaborators), so the bodies stay interceptable by tests.
"""
from __future__ import annotations

from typing import Dict, Literal

from agentclaw.community.core.service_bot.repository.models import (
    BotPublishRecord,
    PublishOperationKind,
    PublishStatus,
)
from agentclaw.community.core.service_bot.schemas.publish_schemas import PublishFlowResult
from agentclaw.community.core.service_bot.services.bot_publish_service import (
    PublishNotFoundError,
)
from agentclaw.community.core.service_bot.services.publish_flow.errors import (
    PublishFlowServiceError,
)
from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.log import get_logger

logger = get_logger()

# The sync-success handler only ever runs for the two backend-driven publish
# stages, and the source/target publish status are 1:1 with the stage (both
# ``_determine_sync_stage`` and ``_determine_restart_stage`` only yield these two,
# and the restart path filters out VALIDATING/SUCCESS before the success handler).
# So they are derived from the stage rather than passed in.
_SyncStage = Literal[PublishStage.VERIFY, PublishStage.ONLINE]
_SUCCESS_SOURCE_STATUS: Dict[PublishStage, PublishStatus] = {
    PublishStage.VERIFY: PublishStatus.VALIDATE_PUB,
    PublishStage.ONLINE: PublishStatus.ONLINE_PUB,
}
_SUCCESS_TARGET_STATUS: Dict[PublishStage, PublishStatus] = {
    PublishStage.VERIFY: PublishStatus.VALIDATING,
    PublishStage.ONLINE: PublishStatus.SUCCESS,
}

# The failure handler marks the record FAILED from whatever non-terminal status it
# was in; unlike the success path this is genuinely variable (the restart path can
# fail from VALIDATING/SUCCESS too), so it stays an explicit parameter.
_FailureSourceStatus = Literal[
    PublishStatus.VALIDATE_PUB,
    PublishStatus.ONLINE_PUB,
    PublishStatus.VALIDATING,
    PublishStatus.SUCCESS,
]


class ProgressSyncMixin:
    """BaaS progress sync + status advancement (mixed into PublishFlowService).

    Persistence goes through the shared seams: publish-record status/ext writes
    via ``_update_publish_status`` (the ``PublishExtState`` plumbing),
    device-binding writes via ``DeviceBindingMixin``
    (``_activate_binding``), and the BaaS progress query via
    ``BaasPublishOpsMixin`` (``get_baas_publish_progress``).
    """

    def _handle_sync_success(
        self,
        publish_id: int,
        publish_record: BotPublishRecord,
        stage: _SyncStage,
        ext: dict,
        baas_publish_id: int,
        progress: dict,
    ) -> PublishFlowResult:
        """Handle a successful BaaS publish.

        Runs only for the two backend-driven stages (VERIFY/ONLINE). The source and
        target publish status are derived from ``stage`` (they are 1:1 with it), so
        the caller does not pass ``current_status``.

        Args:
            publish_id: Publish record ID
            publish_record: Publish record
            stage: Publish stage (VERIFY/ONLINE)
            ext: Extension fields
            baas_publish_id: BaaS publish record ID
            progress: BaaS publish progress information

        Returns:
            PublishFlowResult: Sync result
        """
        baas_status = progress.get("status", "")
        source_status = _SUCCESS_SOURCE_STATUS[stage]
        target_status = _SUCCESS_TARGET_STATUS[stage]

        # Clear the transient retry marker, then atomically advance the status
        # together with ext under the optimistic lock (a separate status-then-ext
        # write would be a TOCTOU race against a concurrent transition).
        ext.pop("retry", None)
        self._update_publish_status(
            publish_id=publish_id,
            target_status=target_status,
            source_status=source_status,
            ext=ext,
        )
        logger.info(
            f"[PublishFlowService._handle_sync_success] "
            f"Publish status updated: {source_status} -> {target_status}"
        )

        # If the online stage succeeded, update the previous publish record status to UPGRADED
        self._mark_previous_publish_superseded(publish_record, stage, target_status)

        # Update the device_binding status to ACTIVE
        self._activate_binding(
            ext=ext,
            stage=stage,
            progress=progress,
            baas_status=baas_status,
            baas_publish_id=baas_publish_id,
            bot_id=publish_record.source_bot_id,
        )

        # All-auto approval (#197): teclaw's post-upgrade MCP outbound-rule
        # refresh — formerly gated on the (now-removed) client approve return —
        # triggers here, on observed deploy success. No-op for ARCA/baas;
        # idempotent (a re-push) for teclaw, so running it for both first-release
        # and upgrade successes is safe.
        self._refresh_provider_mcp_after_success(publish_record, ext, stage)

        if stage == PublishStage.ONLINE:
            self._destroy_verify_bot_after_online_success(publish_id, publish_record)

        return PublishFlowResult(
            publish_id=publish_id,
            status=target_status,
            message=f"Publish progress synced successfully, status: {baas_status}",
            data=progress,
        )

    def _refresh_provider_mcp_after_success(
        self,
        publish_record: BotPublishRecord,
        ext: dict,
        stage: PublishStage,
    ) -> None:
        """Re-establish the provider's post-upgrade MCP outbound rule after a
        BaaS publish reaches SUCCESS (teclaw re-pushes; ARCA/baas no-op).

        Best-effort — a failure is logged and does not block the publish. This
        replaces the former approve-gated refresh in ``upgrade_release`` (#197
        all-auto): the refresh now keys off observed deploy success."""
        binding_id = (ext.get("binding") or {}).get(stage.value)
        if not binding_id:
            return
        binding = self._publish_service.get_device_binding_by_id(binding_id)
        if not binding or not binding.device_id:
            return
        owner_id = self._get_owner_id(publish_record)
        bot = self._bot_service.get_bot(
            bot_id=publish_record.source_bot_id, user_id=owner_id
        )
        if not bot:
            return
        try:
            self._provider_behavior(bot).refresh_after_upgrade(
                bot_uuid=binding.device_id, bot=bot
            )
        except Exception as e:
            logger.warning(
                "[PublishFlowService._refresh_provider_mcp_after_success] "
                "refresh failed: publish_id=%s stage=%s error=%s",
                publish_record.id, stage.value, e,
            )

    def _mark_previous_publish_superseded(
        self,
        publish_record: BotPublishRecord,
        stage: PublishStage,
        target_status: PublishStatus,
    ) -> None:
        """Update the previous publish record status to UPGRADED (only when the online stage succeeds).

        Part of the sync-success handling (its only caller); the actual write
        goes through the ``_update_publish_status`` plumbing.

        Args:
            publish_record: Current publish record
            stage: Publish stage (VERIFY/ONLINE)
            target_status: Target status
        """
        # Only update the previous publish record when the online stage succeeds
        if stage != PublishStage.ONLINE or target_status != PublishStatus.SUCCESS:
            return

        last_pub_id = publish_record.last_pub_id
        if not last_pub_id or last_pub_id <= 0:
            return

        # Query the previous publish record
        last_publish = self.get_publish_record(last_pub_id)
        if not last_publish:
            logger.warning(
                f"[PublishFlowService._mark_previous_publish_superseded] "
                f"Last publish record not found: last_pub_id={last_pub_id}"
            )
            return

        # Clear the rollback_restored_from marker (if present)
        last_ext = last_publish.ext or {}
        if last_ext.pop("rollback_restored_from", None):
            logger.info(
                f"[PublishFlowService._mark_previous_publish_superseded] "
                f"Clearing rollback_restored_from for publish {last_pub_id}"
            )

        # Update the previous publish record status to UPGRADED, and update ext at the same time
        try:
            self._update_publish_status(
                publish_id=last_pub_id,
                target_status=PublishStatus.UPGRADED,
                source_status=PublishStatus.SUCCESS,
                ext=last_ext,
            )
            logger.info(
                f"[PublishFlowService._mark_previous_publish_superseded] "
                f"Last publish status updated to UPGRADED: last_pub_id={last_pub_id}"
            )
        except Exception as e:
            logger.warning(
                f"[PublishFlowService._mark_previous_publish_superseded] "
                f"Failed to update last publish status: last_pub_id={last_pub_id}, error={e}"
            )

    def _destroy_verify_bot_after_online_success(
        self, publish_id: int, publish_record: BotPublishRecord
    ) -> None:
        """After the online stage succeeds, tear down the verify-stage BaaS bot
        (provider-permitting — teclaw keeps it). Best-effort: a destroy failure is
        logged and does not block the publish."""
        owner_id = self._get_owner_id(publish_record)
        bot = self._bot_service.get_bot(
            bot_id=publish_record.source_bot_id,
            user_id=owner_id,
        )
        if not bot:
            raise PublishFlowServiceError(
                f"Bot does not exist: {publish_record.source_bot_id}"
            )

        if not self._provider_behavior(bot).destroys_verify_bot_on_online:
            logger.info(
                "[PublishFlowService._destroy_verify_bot_after_online_success] "
                "Skip destroying verify BaaS bot for this provider: "
                f"publish_id={publish_id}, bot_id={publish_record.source_bot_id}"
            )
            return

        try:
            self._destroy_bot_by_stage(publish_record, PublishStage.VERIFY)
            logger.info(
                "[PublishFlowService._destroy_verify_bot_after_online_success] "
                f"Bot destroyed: publish_id={publish_id}, stage={PublishStage.VERIFY.value}"
            )
        except Exception as e:
            logger.warning(
                "[PublishFlowService._destroy_verify_bot_after_online_success] "
                f"Failed to destroy BaaS bots: publish_id={publish_id}, "
                f"stage={PublishStage.VERIFY.value}, error={e}"
            )
            # A destroy failure does not block the overall flow.

    def _handle_sync_failure(
        self,
        publish_id: int,
        current_status: _FailureSourceStatus,
        ext: dict,
        progress: dict,
        baas_publish_id: int,
        error_message: str | None = None,
    ) -> PublishFlowResult:
        """Handle a failed BaaS publish.

        Args:
            publish_id: Publish record ID
            current_status: Current status
            ext: Extension fields
            progress: BaaS publish progress information
            baas_publish_id: The failed BaaS workflow's id — its ledger op is
                outcome-corrected to FAILED so liveness readers stop treating
                the deploy as landed
            error_message: Custom error message; generated from the number of failed devices when not provided

        Returns:
            PublishFlowResult: Sync result
        """
        if error_message is None:
            failed_devices = progress.get("failed_devices", [])
            error_message = f"BaaS publish failed: {len(failed_devices)} device(s) failed"

        # Outcome correction, before the record's FAILED write: the op's steps
        # completed at bookkeeping time, but its workflow just terminally failed —
        # without this, the failed deploy still reads as the live deployment, so
        # the online-release gate would skip the re-issue on retry (a FAILED
        # retry loop) and the deploy would wrongly supersede a live release.
        self._publish_operation_repo.fail_by_workflow(
            publish_id, baas_publish_id, error_message
        )

        self._clear_retry_flag(ext)
        ext["error_message"] = error_message
        ext["source_status"] = current_status.value
        self._update_publish_status(
            publish_id=publish_id,
            target_status=PublishStatus.FAILED,
            source_status=current_status,
            ext=ext,
        )

        logger.error(f"[PublishFlowService._handle_sync_failure] {error_message}")

        return PublishFlowResult(
            publish_id=publish_id,
            status=PublishStatus.FAILED,
            message=error_message,
            data=progress,
        )

    def advance_publish_progress(
        self,
        publish_id: int,
    ) -> PublishFlowResult:
        """Query the BaaS-layer publish progress and advance the publish record.

        The engine behind the durable progress-poll task (its only caller — the
        user-facing ``/sync`` endpoint is a read-only status report on the
        facade). When the BaaS-layer status is SUCCESS, advance the record and
        update the device_binding; when FAILED, mark the record FAILED.

        The stage is determined automatically from the publish record status:
        - VALIDATE_PUB -> verify
        - ONLINE_PUB -> release

        Args:
            publish_id: Publish record ID

        Returns:
            PublishFlowResult: Sync result
        """
        logger.info(f"[PublishFlowService.advance_publish_progress] Syncing: publish_id={publish_id}")

        # Step 1: Query the publish record
        publish_record = self.get_publish_record(publish_id)
        if not publish_record:
            raise PublishNotFoundError(f"Publish order not found: {publish_id}")

        ext = publish_record.ext or {}

        # If this is a retry publish record (retry=True) and source_status is VALIDATE_PUB or ONLINE_PUB, return the restart progress directly

        if ext.get("retry"):
            source_status = ext.get("source_status")
            if source_status in (PublishStatus.VALIDATE_PUB.value, PublishStatus.ONLINE_PUB.value):
                logger.info(f"[PublishFlowService.advance_publish_progress] Detected retry flag with source_status={source_status}, redirecting to sync_restart_progress: publish_id={publish_id}")
                return self.sync_restart_progress(publish_id)

        current_status = PublishStatus(publish_record.status)

        # Step 2: Determine the stage based on the current status. The poll task
        # only fires in the *_PUB wait states, so a None stage is the TOCTOU
        # catch-all: the record moved (advanced/failed) between the poll's status
        # check and this re-read — nothing to drive, report and stop.
        stage = self._determine_sync_stage(current_status)
        if not stage:
            logger.warning(
                f"[PublishFlowService.advance_publish_progress] "
                f"Not in a BaaS wait state: {current_status}, publish_id={publish_id}"
            )
            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message=f"Current status {current_status} does not support progress sync",
            )

        # Step 3: Get the BaaS-layer publish record ID
        publish_info = ext.get("publish", {})
        baas_publish_id = publish_info.get(stage.value)
        if not baas_publish_id:
            logger.warning(
                f"[PublishFlowService.advance_publish_progress] "
                f"No baas_publish_id found for stage={stage.value}, publish_id={publish_id}"
            )
            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message=f"BaaS publish record ID not found for the {stage.value} stage",
            )

        # Step 4: Call the BaaS layer to get the publish progress
        try:
            progress = self.get_baas_publish_progress(
                baas_publish_id=baas_publish_id,
            )
        except Exception as e:
            logger.error(f"[PublishFlowService.advance_publish_progress] Failed to get progress: {e}")
            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message=f"Failed to get BaaS publish progress: {str(e)}",
            )

        baas_status = progress.get("status", "")
        logger.info(
            f"[PublishFlowService.advance_publish_progress] "
            f"BaaS status: {baas_status}, publish_id={publish_id}"
        )

        # Step 5: Dispatch handling based on the BaaS status
        if baas_status == "SUCCESS":
            return self._handle_sync_success(
                publish_id=publish_id,
                publish_record=publish_record,
                stage=stage,
                ext=ext,
                baas_publish_id=baas_publish_id,
                progress=progress,
            )

        elif baas_status == "FAILED":
            return self._handle_sync_failure(
                publish_id=publish_id,
                current_status=current_status,
                ext=ext,
                progress=progress,
                baas_publish_id=baas_publish_id,
            )

        else:
            # Other statuses (INIT, PENDING, APPROVING, etc.)
            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message=f"BaaS publish status: {baas_status}",
                data=progress,
            )

    def sync_scale_progress(
        self,
        publish_id: int,
    ) -> PublishFlowResult:
        """Query the scale publish record status.

        Get the BaaS-layer scale publish record ID from the publish record's
        ext.scale.publish_id, call the BaaS layer to get the publish progress, and return it.

        Args:
            publish_id: Publish record ID

        Returns:
            PublishFlowResult: Scale progress result
        """
        logger.info(f"[PublishFlowService.sync_scale_progress] Syncing scale progress: publish_id={publish_id}")

        publish_record = self.get_publish_record(publish_id)
        if not publish_record:
            raise PublishNotFoundError(f"Publish order not found: {publish_id}")

        current_status = PublishStatus(publish_record.status)
        ext = publish_record.ext or {}

        # (#197) Prefer the ledger's scale op workflow id (source of truth);
        # fall back to the dual-written ext.scale marker for pre-ledger records.
        scale_publish_id = None
        scale_op = self._publish_operation_repo.get_latest_by_kind(
            publish_id, PublishOperationKind.SCALE.value, PublishStage.ONLINE.value
        )
        if scale_op is not None and scale_op.baas_publish_id is not None:
            scale_publish_id = scale_op.baas_publish_id
        if not scale_publish_id:
            scale_publish_id = (ext.get("scale", {}) or {}).get("publish_id")

        if not scale_publish_id:
            logger.warning(
                f"[PublishFlowService.sync_scale_progress] "
                f"No scale_publish_id found, publish_id={publish_id}"
            )
            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message="Scale publish record ID not found",
            )

        try:
            progress = self.get_baas_publish_progress(
                baas_publish_id=scale_publish_id,
            )
        except Exception as e:
            logger.error(f"[PublishFlowService.sync_scale_progress] Failed to get scale progress: {e}")
            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message=f"Failed to get BaaS scale publish progress: {str(e)}",
            )

        baas_status = progress.get("status", "")
        logger.info(
            f"[PublishFlowService.sync_scale_progress] "
            f"BaaS scale status: {baas_status}, publish_id={publish_id}"
        )

        return PublishFlowResult(
            publish_id=publish_id,
            status=current_status,
            message=f"BaaS scale status: {baas_status}",
            data=progress,
        )

    def sync_restart_progress(
        self,
        publish_id: int,
    ) -> PublishFlowResult:
        """Query the restart publish record status.

        Determine the current stage from the publish record status, get the BaaS-layer
        restart publish record ID from ext, call the BaaS layer to get the publish
        progress, and return it.

        Args:
            publish_id: Publish record ID

        Returns:
            PublishFlowResult: Restart progress result
        """
        logger.info(f"[PublishFlowService.sync_restart_progress] Syncing restart progress: publish_id={publish_id}")

        # Step 1: Query the publish record
        publish_record = self.get_publish_record(publish_id)
        if not publish_record:
            raise PublishNotFoundError(f"Publish order not found: {publish_id}")

        current_status = PublishStatus(publish_record.status)

        # Step 2: Determine the restart stage based on the status
        stage = self._determine_restart_stage(current_status)
        if not stage:
            logger.warning(
                f"[PublishFlowService.sync_restart_progress] "
                f"Invalid status for restart sync: {current_status}, publish_id={publish_id}"
            )
            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message=f"Current status {current_status} does not support querying restart progress",
            )

        # Step 3: Get the BaaS-layer restart publish record ID.
        # (#197) Prefer the ledger's restart op workflow id (source of truth); fall
        # back to the dual-written ext.restart marker. execute_restart writes that
        # marker best-effort, so a failed ext write must not leave restart status
        # unqueryable when the ledger already holds the workflow id.
        ext = publish_record.ext or {}
        restart_publish_id = None
        restart_op = self._publish_operation_repo.get_latest_by_kind(
            publish_id, PublishOperationKind.RESTART.value, stage.value
        )
        if restart_op is not None and restart_op.baas_publish_id is not None:
            restart_publish_id = restart_op.baas_publish_id
        if not restart_publish_id:
            restart_publish_id = (ext.get("restart", {}) or {}).get(stage.value)

        if not restart_publish_id:
            logger.warning(
                f"[PublishFlowService.sync_restart_progress] "
                f"No restart_publish_id found for stage={stage.value}, publish_id={publish_id}"
            )
            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message=f"Restart publish record ID not found for the {stage.value} stage",
            )

        # Step 4: Call the BaaS layer to get the publish progress
        try:
            progress = self.get_baas_publish_progress(
                baas_publish_id=restart_publish_id,
            )
        except Exception as e:
            logger.error(f"[PublishFlowService.sync_restart_progress] Failed to get restart progress: {e}")
            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message=f"Failed to get BaaS restart publish progress: {str(e)}",
            )

        baas_status = progress.get("status", "")
        logger.info(
            f"[PublishFlowService.sync_restart_progress] "
            f"BaaS restart status: {baas_status}, publish_id={publish_id}, stage={stage.value}"
        )

        # Step 5: Advance the publish record status based on the BaaS status
        # VALIDATING and SUCCESS are completed, stable states that do not need advancing
        if current_status in (PublishStatus.VALIDATING, PublishStatus.SUCCESS):
            logger.info(
                f"[PublishFlowService.sync_restart_progress] "
                f"Current status is {current_status}, skip status update: publish_id={publish_id}"
            )

            # If it failed, update the current publish record status to failed
            if baas_status == "FAILED":
                return self._handle_sync_failure(
                    publish_id=publish_id,
                    current_status=current_status,
                    ext=ext,
                    progress=progress,
                    baas_publish_id=restart_publish_id,
                    error_message=f"Restart publish status: {baas_status}",
                )

            # A restart that RECREATED the target minted a NEW binding as PENDING
            # (the normal in-place upgrade reuses the already-ACTIVE one, and
            # ext.binding.<stage> now points at the new binding). The stable
            # SUCCESS/VALIDATING record skips the status advance, but the recreated
            # binding must still be activated on deploy success or it stays PENDING
            # forever and binding consumers reject it. _activate_binding is
            # idempotent — a no-op refresh for the in-place upgrade path.
            if baas_status == "SUCCESS":
                self._activate_binding(
                    ext=ext,
                    stage=stage,
                    progress=progress,
                    baas_status=baas_status,
                    baas_publish_id=restart_publish_id,
                    bot_id=publish_record.source_bot_id,
                )
                # _activate_binding writes device_props via reuse_binding, which
                # REPLACES (not merges) the dict — dropping the teclaw status-read
                # handle publish_id that execute_restart / _recreate_restart_target
                # stored. Re-merge it (refresh_publish_handle merges) so teclaw
                # consumers keep pointing at the current restart workflow.
                binding_id = (ext.get("binding") or {}).get(stage.value)
                if binding_id:
                    self.refresh_publish_handle(binding_id, restart_publish_id)

                # A restart that RECREATED a teclaw target minted a fresh container
                # that needs the post-deploy MCP outbound/auth rule normal
                # first-release success establishes (via _handle_sync_success). This
                # stable-record branch bypasses that path, so apply it here. No-op
                # for ARCA/baas; idempotent (a re-push) for teclaw, so it is safe on
                # the in-place upgrade path too.
                self._refresh_provider_mcp_after_success(publish_record, ext, stage)

            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message=f"Restart publish status: {baas_status}",
                action="sync_restart",
                data=progress,
            )

        if baas_status == "SUCCESS":
            return self._handle_sync_success(
                publish_id=publish_id,
                publish_record=publish_record,
                stage=stage,
                ext=ext,
                baas_publish_id=restart_publish_id,
                progress=progress,
            )

        elif baas_status == "FAILED":
            return self._handle_sync_failure(
                publish_id=publish_id,
                current_status=current_status,
                ext=ext,
                progress=progress,
                baas_publish_id=restart_publish_id,
            )

        else:
            # Other statuses (INIT, PENDING, APPROVING, etc.)
            return PublishFlowResult(
                publish_id=publish_id,
                status=current_status,
                message=f"Restart publish status: {baas_status}",
                action="sync_restart",
                data=progress,
            )
