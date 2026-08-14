"""Publish-record ext ops, mixed into ``PublishFlowService``.

Owns the release-time writes to the ac_bot_publish record's ext + status that need
the provider seam (so they live here rather than on the ``PublishExtState``
collaborator, which owns the raw atomic write plumbing and stays
provider-agnostic). Kept distinct from the device-binding writes
(``DeviceBindingMixin``) and the BaaS approve (``BaasPublishOpsMixin``);
the release runner invokes create-binding, record-ext, then approve in sequence.
"""
from __future__ import annotations

import copy

from agentclaw.community.core.service_bot.repository.models import PublishStatus
from agentclaw.community.core.service_bot.services.publish_flow.errors import (
    PublishFlowServiceError,
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
        expected_ext = copy.deepcopy(ext)
        ext.setdefault("binding", {})[stage.value] = binding_id
        ext.setdefault("publish", {})[stage.value] = baas_publish_id
        publish_record = self.get_publish_record(publish_id)
        if publish_record is None:
            raise PublishFlowServiceError(
                f"Publish record not found: publish_id={publish_id}"
            )
        self._publish_provider_behavior(publish_record).persist_stage_promotion(
            ext=ext, stage=stage, engine_overrides=engine_overrides
        )
        self._update_publish_status(
            publish_id=publish_id,
            target_status=target_status,
            source_status=source_status,
            ext=ext,
            expected_ext=expected_ext,
        )
        return ext
