"""Read-only v2 collaboration repair facts for one Bot.

Never writes, never bumps ``caller_config_revision``, and never calls a
business mutator. Missing/deleted Bots, scope mismatches, unresolvable
stable identities, unreadable saved values and read failures raise the
kernel's ``RepairFactsError`` vocabulary — a facts read failure never
becomes a default payload.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable

from agentclaw.community.kernel.bot_manager_sync import SyncKey
from agentclaw.community.kernel.collaboration_repair import RepairFacts


@runtime_checkable
class CollaborationRepairReadRepository(Protocol):
    """Read current committed profile and owner facts in one pass."""

    @abstractmethod
    def read_facts(self, key: SyncKey) -> RepairFacts:
        """Read one live Bot within the exact tenant/environment/PK scope."""
        ...
