"""Transport-agnostic BBS notification contract."""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable

from agentclaw.community.core.work_orders.models import WorkOrderEventType


@runtime_checkable
class ForumNotificationPublisherProtocol(Protocol):
    @abstractmethod
    def notify(
        self,
        *,
        event_type: WorkOrderEventType,
        recipient_type: str,
        recipient_id: str,
        actor_type: str,
        actor_id: str,
        actor_display_name: str | None,
        target_type: str,
        target_id: str,
        text: str,
        topic_id: str | None,
        post_id: str | None,
        topic_title: str | None = None,
    ) -> None:
        """Create or merge one recipient notification.

        BBS reply notifications coalesce into the recipient's currently unread
        group. A BOT recipient is translated to its current owner. Persistence
        failures must propagate to the caller.
        """
