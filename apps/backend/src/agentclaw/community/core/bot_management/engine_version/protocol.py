"""Service API v1: inspect one authorized personal/service draft runtime."""

from abc import abstractmethod
from typing import Protocol, runtime_checkable
from .models import EngineVersionResult


@runtime_checkable
class EngineVersionServiceProtocol(Protocol):
    @abstractmethod
    async def inspect(
        self,
        *,
        bot_id: str,
        owner_id: str,
        caller_id: str,
        stage: str = "draft",
        device_uuid: str | None = None,
    ) -> EngineVersionResult:
        """Read version facts and advice; no lifecycle, Pin or publication writes.

        Missing metadata is legacy. Transport/command failures are read_failed.
        Non-draft stages and ambiguous/missing instance selections raise domain
        errors, never selecting another runtime. Admission precedes any probe.
        """
        ...
