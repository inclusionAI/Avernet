"""RecordingBcsManagersPlugin — test-only in-memory BCS managers fake.

Simulates BCS's ``replace_managers`` seam with no network: every
snapshot is recorded into ``calls`` instead of being delivered. Tests
script outcomes by pre-filling ``outcomes`` (a ``ManagerSetChange`` to
be returned, or a ``BcsManagersError`` to be raised, in order); with no
preset the fake itself applies the full replace, tracking the current
manager set so ``added`` / ``removed`` / ``total`` report the real
diff between consecutive snapshots.

Declared test-only: it is a recording fake for the manager projection,
not a production service. Two sync services may share one instance to
simulate contention, matching how the later coordinator fake is used.
"""
from __future__ import annotations

from collections import deque

from agentclaw.community.plugin_api.bcs_managers import (
    BcsManagersError,
    BcsManagersPlugin,
)
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.kernel.bot_manager_sync import (
    ManagerSetChange,
    ManagerSnapshot,
)


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.FAKE,
    rationale="isolated manager projection/coordination, no network",
)
class RecordingBcsManagersPlugin(BcsManagersPlugin):
    """Fake BCS that records snapshots and plays scripted outcomes."""

    def __init__(self) -> None:
        #: Every snapshot handed to replace_managers, in call order.
        self.calls: list[ManagerSnapshot] = []
        #: Scripted replies consumed in order: a ManagerSetChange is
        #: returned, a BcsManagersError is raised. Presets take
        #: precedence over the simulated full replace below.
        self.outcomes: deque[ManagerSetChange | BcsManagersError] = deque()
        self._manager_user_ids: tuple[str, ...] = ()

    async def replace_managers(
        self,
        snapshot: ManagerSnapshot,
        *,
        timeout_seconds: float,
    ) -> ManagerSetChange:
        self.calls.append(snapshot)
        if self.outcomes:
            outcome = self.outcomes.popleft()
            if isinstance(outcome, BcsManagersError):
                raise outcome
            return outcome
        previous = set(self._manager_user_ids)
        current = set(snapshot.manager_user_ids)
        self._manager_user_ids = tuple(sorted(current))
        return ManagerSetChange(
            added=len(current - previous),
            removed=len(previous - current),
            total=len(current),
            request_id=f"bcs-replace-{len(self.calls)}",
        )

    async def aclose(self) -> None:
        """No pooled connections in the fake — nothing to release."""
        return None