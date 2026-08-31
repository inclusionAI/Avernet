"""Lifecycle registration for G4 Reference and Track Latest task handlers."""

from __future__ import annotations

from agentclaw.community.core.skill_center.services.skill_center_reference_processor import (
    SkillCenterReferenceTaskHandler,
)
from agentclaw.community.core.skill_center.services.track_latest import (
    BotTrackLatestReconcileTaskHandler,
    TrackLatestFanoutTaskHandler,
)
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.kernel.lifecycle import LifecycleBase


class SkillCenterGroup4TaskRegistrar(LifecycleBase):
    def __init__(
        self,
        *,
        registry: HandlerRegistry,
        reference: SkillCenterReferenceTaskHandler,
        fanout: TrackLatestFanoutTaskHandler,
        reconcile: BotTrackLatestReconcileTaskHandler,
    ) -> None:
        self._registry = registry
        self._reference = reference
        self._fanout = fanout
        self._reconcile = reconcile

    async def bootstrap(self) -> None:
        self._registry.register(self._reference, wake_on_enqueue=True)
        self._registry.register(self._fanout)
        self._registry.register(self._reconcile)


__all__ = ["SkillCenterGroup4TaskRegistrar"]
