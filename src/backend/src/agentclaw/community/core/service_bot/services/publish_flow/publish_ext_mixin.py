"""Publish-record ext ops, mixed into ``PublishFlowService``.

Owns the release-time writes to the ac_bot_publish record's ext + status that need
the provider seam (so they live here rather than on the ``PublishExtState``
collaborator, which owns the raw atomic write plumbing and stays
provider-agnostic). Kept distinct from the device-binding writes
(``DeviceBindingMixin``) and the BaaS approve (``BaasPublishOpsMixin``);
the release runner invokes create-binding, record-ext, then approve in sequence.
"""
from __future__ import annotations

from agentclaw.community.core.service_bot.repository.models import PublishStatus
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
