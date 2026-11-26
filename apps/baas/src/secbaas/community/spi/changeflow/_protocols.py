"""ChangeFlow observer contracts for the BaaS publish lifecycle.

The observer is deliberately side-channel only: every method is best-effort and
MUST NOT raise into the publish state machine.  ChangeFlow ``enforcement`` is
``observe`` in the v0.1.3 contract, so no verdict can block a BaaS publish.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ChangeFlowBatchTarget:
    """One device target inside a publish batch."""

    target: str
    state: str = "pending"
    actual_version: str | None = None


@dataclass(frozen=True, slots=True)
class ChangeFlowStage:
    """A stage and its batches as frozen in the external publish plan."""

    name: str
    batches: tuple[ChangeFlowBatchContext, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ChangeFlowRegisterContext:
    """Immutable plan snapshot registered with ChangeFlow."""

    tenant: str
    publish_id: int
    publish_type: str
    bot_id: int
    bot_name: str
    engine: str
    instance_count: int
    environment: str
    candidate_version: str
    initiator: str
    stages: tuple[ChangeFlowStage, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ChangeFlowBatchContext:
    """Batch-level facts used by checks and batch_result reports."""

    tenant: str
    publish_id: int
    change_id: str | None
    stage: str
    batch_index: int
    candidate_version: str
    targets: tuple[ChangeFlowBatchTarget, ...] = field(default_factory=tuple)
    outcome: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ChangeFlowTerminalContext:
    """Publish terminal facts used to close the ChangeFlow change."""

    tenant: str
    publish_id: int
    change_id: str | None
    outcome: str
    reason: str | None = None


@runtime_checkable
class ChangeFlowObserver(Protocol):
    """BaaS publish lifecycle observer (observe-only, never blocking)."""

    async def on_register(self, ctx: ChangeFlowRegisterContext) -> str | None:
        """Register an online publish plan; return ``change_id`` or ``None``."""

    async def on_pre_check(self, ctx: ChangeFlowBatchContext) -> None:
        """Send the non-blocking pre-check for one batch."""

    async def on_batch_result(self, ctx: ChangeFlowBatchContext) -> None:
        """Report a terminal batch outcome."""

    async def on_publish_terminal(self, ctx: ChangeFlowTerminalContext) -> None:
        """Close the ChangeFlow change at publish terminal state."""
