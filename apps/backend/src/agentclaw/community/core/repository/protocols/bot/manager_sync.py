"""Read-only repository contracts for bot manager sync.

The sync path reads; it never writes persistence. ``read_facts``
returns everything the projector needs for one ``SyncKey`` in a single
call, and the paging members exist so a Space fan-out or repair sweep
walks keys in bounded batches instead of materializing the full table
(the caller decides batch size; implementations must not silently
expand it).

Paging contract: return the next chunk of ``SyncKey``s strictly ordered
by ``bot_pk`` ascending with ``after_pk`` exclusive, and stop with an
empty tuple. Implementations raise their domain's stable not-found /
ownership / missing-identity / read errors — the kernel's
``SnapshotUnavailable`` is the vocabulary they map onto.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable

from agentclaw.community.kernel.bot_manager_sync import (
    SpaceChange,
    SyncFacts,
    SyncKey,
)


@runtime_checkable
class ManagerSyncReadRepository(Protocol):
    """Read-only facts source for the manager sync projector."""

    @abstractmethod
    def read_facts(self, key: SyncKey) -> SyncFacts:
        """Read one Bot's sync facts (identity, owner, collaborators)."""
        ...

    @abstractmethod
    def page_space_keys(
        self, change: SpaceChange, *, after_pk: int, limit: int
    ) -> tuple[SyncKey, ...]:
        """Page the SyncKeys a SpaceChange fans out to, by bot_pk."""
        ...

    @abstractmethod
    def page_keys(
        self, *, avernet_tenant: str, env: str, after_pk: int, limit: int
    ) -> tuple[SyncKey, ...]:
        """Page every sync-eligible SyncKey in a tenant/env scope."""
        ...