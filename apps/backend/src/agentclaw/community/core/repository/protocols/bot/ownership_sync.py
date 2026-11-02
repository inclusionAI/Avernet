"""Read-only v1 ownership facts, independent of collaborators and Space state.

Readers never write sync state. Missing/deleted Bots, scope mismatches, invalid
identities and read failures raise the kernel's SnapshotUnavailable vocabulary.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable

from agentclaw.community.kernel.bot_manager_sync import SyncKey
from agentclaw.community.kernel.bot_ownership_sync import OwnershipFacts


@runtime_checkable
class OwnershipSyncReadRepository(Protocol):
    """Read current committed Owner facts without consulting manager rosters."""

    @abstractmethod
    def read_facts(self, key: SyncKey) -> OwnershipFacts:
        """Read one live Bot within the exact tenant/environment/PK scope."""
        ...

    @abstractmethod
    def page_keys(
        self, *, avernet_tenant: str, env: str, after_pk: int, limit: int
    ) -> tuple[SyncKey, ...]:
        """Page scoped live keys, ascending PK, after_pk exclusive, up to limit.

        An empty tuple ends the walk; collaborator presence is not required.
        """
        ...
