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

        OpenClaw/Hermes older or legacy releases recommend restart independently
        of provider/image policy; same/newer needs none. Other engines return
        not_applicable with no device access after visibility and stage checks.
        Visible users without explicit restart standing receive restricted
        guidance; absence and lack of visibility remain masked not-found.
        Missing metadata is legacy. Transport/command failures are read_failed.
        Non-draft stages and ambiguous/missing instance selections raise domain
        errors, never selecting another runtime. Admission precedes any probe.
        """
        ...
