"""Outbound notification contract for the Todo workbench."""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable

from agentclaw.community.core.task.todos.notification_models import (
    TodoNotificationEvent,
    TodoNotificationPublishResult,
)


@runtime_checkable
class TodoNotificationPublisherProtocol(Protocol):
    """Publish only after Todo product facts have been persisted."""

    @abstractmethod
    def publish(
        self, event: TodoNotificationEvent
    ) -> TodoNotificationPublishResult: ...


__all__ = ["TodoNotificationPublisherProtocol"]
