"""Lifecycle registration for Reference, Track Latest, and Skill recovery."""

from __future__ import annotations

from agentclaw.community.core.skill_center.services.skill_center_reference_processor import (
    SkillCenterReferenceTaskHandler,
)
from agentclaw.community.core.skill_center.services.track_latest import (
    BotTrackLatestReconcileTaskHandler,
    TrackLatestFanoutTaskHandler,
)
from agentclaw.community.core.skill_center.services.desktop_skill_recovery import (
    DesktopSkillRecoveryTaskHandler,
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
        desktop_skill_recovery: DesktopSkillRecoveryTaskHandler,
    ) -> None:
        self._registry = registry
        self._reference = reference
        self._fanout = fanout
        self._reconcile = reconcile
        self._desktop_skill_recovery = desktop_skill_recovery

    async def bootstrap(self) -> None:
        self._registry.register(self._reference, wake_on_enqueue=True)
        self._registry.register(self._fanout)
        self._registry.register(self._reconcile)
        self._registry.register(
            self._desktop_skill_recovery,
            wake_on_enqueue=True,
        )


__all__ = ["SkillCenterGroup4TaskRegistrar"]
