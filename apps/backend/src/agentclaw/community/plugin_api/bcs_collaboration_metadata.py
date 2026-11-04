"""Collaboration metadata delivery Plugin API v1; no transport objects.

Core supplies current committed snapshots; the Plugin makes one
bounded attempt per method and classifies failures; retry policy and
component ordering belong to Core, not to the delivery implementation.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.kernel.collaboration_repair import (
    CallerSnapshot,
    MetadataDeliveryError,
    ProfileSnapshot,
)
from agentclaw.community.plugin_api.base import Plugin

__all__ = ["BcsCollaborationMetadataPlugin", "MetadataDeliveryError"]


@runtime_checkable
class BcsCollaborationMetadataPlugin(Plugin, Protocol):
    """Repair BCS-side collaboration metadata for one existing stable Bot.

    Every method addresses BCS with the established registration reference
    carried inside the snapshot, including retained conversion references.
    Callers must not target a Bot they have not confirmed with
    ``exists`` first; this plugin never registers a missing Bot and
    never initializes ownership.
    """

    async def exists(self, bcs_bot_id: str, *, timeout_seconds: float) -> bool:
        """Check remote presence once, bounded by ``timeout_seconds``.

        Returns whether BCS currently knows the stable Bot. It performs
        no write and raises ``MetadataDeliveryError`` when the answer
        could not be obtained.
        """
        ...

    async def update_profile(
        self,
        snapshot: ProfileSnapshot,
        *,
        timeout_seconds: float,
    ) -> None:
        """Deliver one profile update; unconfirmed delivery raises.

        A returned ``None`` means BCS confirmed the update. Any outcome
        BCS did not confirm raises ``MetadataDeliveryError``; the
        explicit ``avatar_url`` clear (``""``) is a valid payload and is
        transmitted as-is. The current onboard transport preserves empty/blank
        summaries and does not echo them: those inputs raise UNKNOWN with
        ``collaboration_summary_unconfirmed`` after acknowledgement, even if
        name/avatar were applied. Consumers must preserve the partial result.
        """
        ...

    async def sync_caller(
        self,
        snapshot: CallerSnapshot,
        *,
        timeout_seconds: float,
    ) -> None:
        """Deliver one caller-config update; unconfirmed delivery raises.

        A disabled established config (``enabled=False``) is a valid
        payload and is transmitted as-is, with the saved
        ``source_revision``; the plugin never bumps the revision and
        never treats ``enabled=False`` as "do not send".
        """
        ...

    async def aclose(self) -> None:
        """Release owned resources on the delivery loop, not injected clients."""
        ...
