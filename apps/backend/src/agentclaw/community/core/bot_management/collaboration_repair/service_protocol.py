"""Core-owned collaboration repair Service API v2.

The api package re-exports this Protocol; Core never imports api.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable

from agentclaw.community.kernel.collaboration_repair import (
    RepairCommand,
    RepairReport,
)


@runtime_checkable
class CollaborationRepairServiceProtocol(Protocol):
    """Align one Bot's BCS collaboration metadata with committed facts."""

    @abstractmethod
    async def repair(self, command: RepairCommand) -> RepairReport:
        """Repair ownership, profile and managers once from fresh facts.

        Preconditions fail with the kernel's ``RepairPreconditionError``
        codes; component outcomes are reported per item and never
        rewritten into pretend success. Classified managers facts failures
        preserve prior writes in the report and stop remaining delivery.
        Facts reads and sends share one deadline; expired read results are
        discarded even if the synchronous database thread is still running.
        """
        ...
