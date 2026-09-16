"""Service API for observing READY session resources without blocking TC."""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable

from agentclaw.community.core.session_resources.types import SessionResourceRecord


@runtime_checkable
class TcResourceReadyObserverProtocol(Protocol):
    """Observe a resource record and schedule notification when it is READY."""

    @abstractmethod
    def notify_in_background(self, resource: SessionResourceRecord) -> None: ...


__all__ = ["TcResourceReadyObserverProtocol"]
