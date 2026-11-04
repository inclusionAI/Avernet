"""Collaboration repair Service API v1 — the Core-owned contract.

The Protocol lives in its owning Core module so the concrete repair
service (a later task of this plan) can inherit it without a
Core→api import edge; ``api/collaboration_repair_service.py`` only
re-exports it for adapters. Like the repair itself, ``repair``
performs bounded work and returns an explicit report; it never mutates
Backend business state and never confirms anything about BCS collaboration
metadata beyond what the returned items state.
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
        """Repair the fixed component set (ownership, profile, managers,
        caller_config) once, from a fresh facts read.

        Preconditions fail with the kernel's ``RepairPreconditionError``
        codes; component outcomes are reported per item and never
        rewritten into pretend success.
        """
        ...
