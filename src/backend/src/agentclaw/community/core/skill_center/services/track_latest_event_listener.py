"""Lifecycle bridge from the unified Published event to Track Latest tasks."""

from __future__ import annotations

from agentclaw.community.core.events.bus import get_event_bus
from agentclaw.community.core.skill_center.materialization_contract import (
    PublishedMaterializedSkillVersion,
)
from agentclaw.community.core.skill_center.track_latest_service_protocol import (
    TrackLatestServiceProtocol,
)
from agentclaw.community.kernel.lifecycle import LifecycleBase


class TrackLatestPublishedVersionListener(LifecycleBase):
    """Required at-least-once hand-off for every materialization producer."""

    def __init__(self, track_latest: TrackLatestServiceProtocol) -> None:
        self._track_latest = track_latest

    async def bootstrap(self) -> None:
        bus = get_event_bus()
        if bus.is_subscribed(PublishedMaterializedSkillVersion, self.handle):
            return
        # Required delivery makes the G3 Publication task retry if durable
        # Track Latest enqueue fails after its PUBLISHED commit.
        bus.subscribe(
            PublishedMaterializedSkillVersion,
            self.handle,
            required=True,
        )

    def handle(self, event: PublishedMaterializedSkillVersion) -> None:
        self._track_latest.version_published(event)


__all__ = ["TrackLatestPublishedVersionListener"]
