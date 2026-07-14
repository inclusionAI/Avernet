"""Shared publish-record ext/state helpers.

Every stage runner reads and writes the publish record's ``ext`` blob and status
through :class:`PublishExtState`, so the persistence conventions — latest-ext
read-back, atomic status+ext writes, and the DingTalk per-stage
``engine_overrides`` composition — live in one place instead of being scattered
across the flow. Behavior is a verbatim lift from the former private helpers on
``PublishFlowService``; the ``ext`` JSON keys are unchanged.
"""
from __future__ import annotations

import copy
from collections.abc import Callable
from typing import TYPE_CHECKING

from agentclaw.community.core.service_bot.repository.models import BotPublishRecord, PublishStatus
from agentclaw.community.core.service_bot.services.bot_publish_service import (
    BotPublishService,
    PublishNotFoundError,
)
from agentclaw.community.core.service_bot.services.deploy.engine_ext_stage import (
    apply_engine_overrides,
    restamp_stage,
)
from agentclaw.community.core.service_bot.services.publish_flow.errors import (
    PublishFlowServiceError,
)
from agentclaw.community.core.service_bot.types import PublishStage

if TYPE_CHECKING:
    from agentclaw.community.core.channel.services.engine_overrides_reader import (
        ChannelEngineOverridesReader,
    )


class PublishExtState:
    """Read/write a publish record's ``ext`` + status and compose per-stage artifacts."""

    def __init__(
        self,
        publish_service: BotPublishService,
        channel_overrides_reader: "ChannelEngineOverridesReader",
    ) -> None:
        self._publish_service = publish_service
        self._channel_overrides_reader = channel_overrides_reader

    # ── owner / retry ────────────────────────────────────────────────────
    @staticmethod
    def owner_id(publish_record: BotPublishRecord) -> str:
        """The publish record's owner_id; a collaborator's action must run the
        execution chain under the owner's identity."""
        owner_id = publish_record.owner_id
        if not owner_id:
            raise PublishFlowServiceError(
                f"Publish record missing owner_id: publish_id={publish_record.id}"
            )
        return owner_id

    @staticmethod
    def clear_retry_flag(ext: dict) -> None:
        """Clear the publish record's transient retry marker."""
        ext.pop("retry", None)

    # ── ext read / write ─────────────────────────────────────────────────
    def get_latest_ext(self, publish_id: int) -> dict:
        """Fetch the publish record's latest ext, avoiding write-back from a stale snapshot."""
        latest_record = self._publish_service.get_publish_by_id(publish_id)
        if not latest_record:
            raise PublishNotFoundError(f"Publish order not found: {publish_id}")
        return copy.deepcopy(latest_record.ext or {})

    def mutate_and_update_ext(
        self,
        publish_id: int,
        mutator: Callable[[dict], None],
    ) -> dict:
        """Read-modify-write the ext: fetch the latest ext, apply ``mutator`` to it
        in place, then persist it. The mutator may make any change (not just a
        merge); reading the latest snapshot first reduces the risk of clobbering
        concurrent writes. Returns the persisted ext."""
        ext = self.get_latest_ext(publish_id)
        mutator(ext)
        self._publish_service.update_publish_ext(publish_id=publish_id, ext=ext)
        return ext

    def advance_status(
        self,
        publish_id: int,
        target_status: PublishStatus,
        source_status: PublishStatus,
    ) -> bool:
        """Atomically advance the status (no ext write) under the optimistic lock.

        Returns ``True`` if this call won the transition (the record was still at
        ``source_status``); ``False`` if a concurrent writer already moved it."""
        try:
            self._publish_service.update_publish_status(
                publish_id, target_status.value, source_status.value
            )
            return True
        except PublishNotFoundError:
            return False

    def update_status(
        self,
        publish_id: int,
        target_status: PublishStatus,
        source_status: PublishStatus,
        ext: dict,
    ) -> None:
        """Atomically update the publish record's status and ext fields.

        ``ext`` is required and written as-is: the downstream repository does a
        blind overwrite of the record's ext column, so the caller must pass the
        full ext it wants persisted (read-modify-write), never a partial or empty
        dict expecting a merge. Every call site already does this.
        """
        self._publish_service.update_publish_status_with_ext(
            publish_id=publish_id,
            target_status=target_status,
            ext=ext,
            source_status=source_status,
        )

    # ── per-stage engine_overrides / artifact stamping ───────────────────
    def stage_overrides(
        self, publish_record: BotPublishRecord, stage: PublishStage
    ) -> dict | None:
        """That stage's ``engine_overrides`` (DingTalk channels), or ``None``.

        ``None`` when the record carries no ``config_artifact`` (the ARCA mount
        path handles per-stage channels out of band), so we skip the channel
        fetch entirely.
        """
        if not (publish_record.ext or {}).get("config_artifact"):
            return None
        return self._channel_overrides_reader.overrides_for_stage(
            user_id=self.owner_id(publish_record),
            bot_id=publish_record.source_bot_id,
            accept_stages={stage.value},
        )

    @staticmethod
    def artifact_for_stage(
        config_artifact: dict | None,
        stage: PublishStage,
        overrides: dict | None,
    ) -> dict | None:
        """The artifact to deliver for ``stage``: stamp ``engine_ext.stage`` and
        overlay that stage's channel ``engine_overrides``. No-ops for the ARCA
        mount path (no ``config_artifact``)."""
        return apply_engine_overrides(restamp_stage(config_artifact, stage), overrides)

    @staticmethod
    def store_stage_overrides(
        ext: dict, stage: PublishStage, overrides: dict | None
    ) -> None:
        """Persist that stage's ``engine_overrides`` so restart/redeliver
        reproduces the promoted channels. No-op when ``overrides`` is ``None``."""
        if overrides is None:
            return
        ext.setdefault("engine_overrides_by_stage", {})[stage.value] = overrides

    @staticmethod
    def stamp_stage_on_stored_artifact(ext: dict, stage: PublishStage) -> None:
        """Persist the promoted ``stage`` into the stored ``config_artifact``
        snapshot's ``engine_ext.stage``. No-op for the ARCA mount path."""
        config_artifact = ext.get("config_artifact")
        if config_artifact is not None:
            ext["config_artifact"] = restamp_stage(config_artifact, stage)
