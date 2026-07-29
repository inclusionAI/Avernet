"""副屏 panel carrier transport (Phase 4.5.3, plan §4.5.3 / FR-OBS-11).

Bridges the in-process :class:`EventBus` (:class:`TaskPanelEvent`, published by
:class:`EventBusPanelPublisher`) to the :class:`PanelDeliveryPort` chat-push
seam. The carrier subscribes to ``TaskPanelEvent`` and hands the formatted
``<AixUI panel>`` content to the delivery port; the impl pushes it into the chat
session stream (corp/transport-bridge) or Noops (community — frontend create-flow
calls ``openTaskPanel`` directly).

Wired in the community profile bootstrap via :meth:`TaskPanelCarrier.install`.
Holds NO state; never blocks the create path on a delivery failure (the delivery
port swallows its own errors).
"""
from __future__ import annotations

from typing import List, Optional

from agentclaw.community.core.events.bus import EventBus, get_event_bus
from agentclaw.community.core.task.protocols import (
    PanelDeliveryPort,
)
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.log import get_logger

logger = get_logger()


class NoopPanelDelivery(PanelDeliveryPort):
    """Community default — no chat push bus. Logs the content for dev
    observability; the frontend create-flow handles the popup."""

    def deliver(self, session_id: Optional[str], content: str) -> None:
        logger.info(
            "[NoopPanelDelivery] drop panel content (community has no chat push bus) "
            "session=%s len=%d",
            session_id,
            len(content),
        )


class RecordingPanelDelivery(PanelDeliveryPort):
    """Test double — records delivered (session_id, content) pairs in order."""

    def __init__(self) -> None:
        self.delivered: List[tuple[Optional[str], str]] = []

    def deliver(self, session_id: Optional[str], content: str) -> None:
        self.delivered.append((session_id, content))


class TaskPanelCarrier(LifecycleBase):
    """Subscribe to :class:`TaskPanelEvent` on the EventBus → deliver via the
    :class:`PanelDeliveryPort`. Install once at bootstrap; ``uninstall`` to
    detach (tests).

    Implements :class:`Lifecycle` so the composition-root lifespan
    auto-discovers + installs it at ``startup()`` (after the DB bootstrap
    phase). Bound as a singleton in :class:`CommunityTaskModule`.
    """

    def __init__(
        self,
        delivery: PanelDeliveryPort,
        bus: Optional[EventBus] = None,
    ) -> None:
        self._delivery = delivery
        self._bus = bus
        self._installed = False

    def install(self, bus: Optional[EventBus] = None) -> None:
        from agentclaw.community.plugins.community.task.panel_publisher import (
            TaskPanelEvent,
        )

        target = bus or self._bus or get_event_bus()
        target.subscribe(TaskPanelEvent, self._on_panel_event)
        self._installed = True

    async def startup(self) -> None:
        # Idempotent: discover_lifecycle_participants dedups by id, but a
        # defensive guard keeps explicit install() calls in tests safe.
        if not self._installed:
            self.install()

    async def shutdown(self) -> None:
        # The EventBus is a process-singleton torn down with the process;
        # nothing to unsubscribe here. Kept explicit so future wire-level
        # transports (corp chat-WS) have an extension point.
        self._installed = False

    def _on_panel_event(self, event: object) -> None:
        content = getattr(event, "content", "") or ""
        if not content:
            return
        session_id = getattr(event, "session_id", None)
        try:
            self._delivery.deliver(session_id, content)
        except Exception:
            logger.exception("[TaskPanelCarrier] delivery failed — dropping (create path never blocks)")


__all__ = [
    "NoopPanelDelivery",
    "RecordingPanelDelivery",
    "TaskPanelCarrier",
]