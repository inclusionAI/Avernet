"""Publish-record ext ops, mixed into ``PublishFlowService``.

Owns the domain-level writes to the ac_bot_publish record's ext + status —
recording a release, recording a sync success, superseding the previous publish.
Feature mixins (progress sync, restart, rollback) orchestrate; the publish-record
writes live here (or in the ``PublishExtState`` collaborator, which owns the raw
atomic write plumbing). Kept distinct from the device-binding writes
(``DeviceBindingMixin``) and the BaaS approve (``BaasPublishOpsMixin``).
"""
from __future__ import annotations

from agentclaw.community.core.service_bot.repository.models import (
    BotPublishRecord,
    PublishStatus,
)
from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.log import get_logger

logger = get_logger()


class PublishExtMixin:
    """Release-time publish-record ext + status writes."""

    def record_release_ext(
        self,
        *,
        publish_id: int,
        bot: dict,
        stage: PublishStage,
        binding_id: int,
        baas_publish_id: int,
        source_status: PublishStatus,
        target_status: PublishStatus,
        engine_overrides: dict | None = None,
    ) -> dict:
        """Persist a release into the publish record's ext + status.

        Records the stage's binding/publish refs, lets the resolved provider
        persist its per-stage promotion state, then atomically advances
        ``source_status → target_status``. Re-reads the latest ext first (the
        authoritative snapshot). Returns the persisted ext."""
        ext = self._get_latest_ext(publish_id)
        ext.setdefault("binding", {})[stage.value] = binding_id
        ext.setdefault("publish", {})[stage.value] = baas_publish_id
        self._provider_behavior(bot).persist_stage_promotion(
            ext=ext, stage=stage, engine_overrides=engine_overrides
        )
        self._update_publish_status(
            publish_id=publish_id,
            target_status=target_status,
            source_status=source_status,
            ext=ext,
        )
        return ext

    def _record_sync_success(
        self,
        publish_id: int,
        *,
        source_status: PublishStatus,
        target_status: PublishStatus,
        ext: dict,
    ) -> None:
        """Persist a BaaS sync success into the publish record.

        Clears the transient retry marker and atomically advances
        ``source_status → target_status`` together with ``ext`` under the
        optimistic lock (a separate status-then-ext write would be a TOCTOU
        race against a concurrent transition)."""
        ext.pop("retry", None)
        self._update_publish_status(
            publish_id=publish_id,
            target_status=target_status,
            source_status=source_status,
            ext=ext,
        )
        logger.info(
            f"[PublishFlowService._record_sync_success] "
            f"Publish status updated: {source_status} -> {target_status}"
        )

    def _mark_previous_publish_superseded(
        self,
        publish_record: BotPublishRecord,
        stage: PublishStage,
        target_status: PublishStatus,
    ) -> None:
        """Update the previous publish record status to UPGRADED (only when the online stage succeeds).

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
        last_publish = self._publish_service.get_publish_by_id(last_pub_id)
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
