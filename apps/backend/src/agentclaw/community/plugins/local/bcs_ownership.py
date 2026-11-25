"""Recording ownership fake for already-initialized fixtures, with no network.

Unscripted first calls idempotently confirm the fixture's target Owner. This
fake does NOT register Bots or Humans or initialize ownership: missing Bots,
version-0 conflicts, and unknown Humans must be expressed as scripted outcomes.
State is isolated by the complete tenant/environment/Bot sync key.
"""

from __future__ import annotations

from collections import deque

from agentclaw.community.kernel.bot_manager_sync import SyncKey
from agentclaw.community.kernel.bot_ownership_sync import (
    OwnershipChange,
    OwnershipSnapshot,
)
from agentclaw.community.plugin_api.bcs_ownership import (
    BcsOwnershipError,
    BcsOwnershipPlugin,
)
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.FAKE,
    rationale="isolated initialized ownership fixtures and scripted delivery outcomes",
)
class RecordingBcsOwnershipPlugin(BcsOwnershipPlugin):
    """Record snapshots and consume scripted results before simulating swaps."""

    def __init__(self) -> None:
        self.calls: list[OwnershipSnapshot] = []
        self.outcomes: deque[OwnershipChange | BcsOwnershipError] = deque()
        self.owners: dict[SyncKey, str] = {}

    async def replace_ownership(
        self,
        snapshot: OwnershipSnapshot,
        *,
        timeout_seconds: float,
    ) -> OwnershipChange:
        self.calls.append(snapshot)
        if self.outcomes:
            outcome = self.outcomes.popleft()
            if isinstance(outcome, BcsOwnershipError):
                raise outcome
            return outcome
        previous = self.owners.get(snapshot.key, snapshot.owner_user_id)
        changed = previous != snapshot.owner_user_id
        self.owners[snapshot.key] = snapshot.owner_user_id
        return OwnershipChange(
            snapshot.bcs_bot_id,
            snapshot.owner_user_id,
            previous if changed else None,
            changed,
            f"recorded-{len(self.calls)}",
        )

    async def aclose(self) -> None:
        """No owned resources to release."""
        return None
