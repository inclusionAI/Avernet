"""Recording collaboration-metadata fake for existing-only repair fixtures.

Simulates the BCS side of the one-shot collaboration repair with no
network: presence is whatever ``existing`` was seeded with, delivered
profiles are stored keyed by the stable BCS bot address, and every
plugin call is recorded as ``(operation, payload)`` so consumer tests
can pin the bounded single-pass call sequence (``exists`` →
``profile``) and script delivery outcomes.

Declared test-only: like the ownership/manager recording fakes, this is
a fixture backend, not a production service. It never registers a
missing Bot, never creates presence, and exposes no register surface —
a write addressed at an id that is not already in ``existing`` raises
``MetadataDeliveryError(NOT_SENT, collaboration_bot_not_found)`` after
the call is recorded. Scripted ``failures`` raise after recording too,
so a scripted failure always characterizes the delivery of a known Bot.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace

from agentclaw.community.kernel.bot_manager_sync import DeliveryFailure
from agentclaw.community.kernel.collaboration_repair import (
    MetadataDeliveryError,
    MetadataDeliveryErrorCode,
    ProfileSnapshot,
)
from agentclaw.community.plugin_api.bcs_collaboration_metadata import (
    BcsCollaborationMetadataPlugin,
)
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl

# Delivery failures carry the metadata family's own code, not the
# precondition family's value-identical twin.
_NOT_FOUND = MetadataDeliveryErrorCode.COLLABORATION_BOT_NOT_FOUND

__all__ = ["RecordingBcsCollaborationMetadataPlugin"]


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.FAKE,
    rationale="isolated existing-only repair fixtures and scripted delivery outcomes",
)
class RecordingBcsCollaborationMetadataPlugin(BcsCollaborationMetadataPlugin):
    """Record repair sends; existing ids only; no registration, no network."""

    def __init__(
        self,
        existing: Iterable[str] = (),
        failures: Mapping[str, Exception] | None = None,
    ) -> None:
        #: stable BCS bot ids the fake is scripted to already know; writes
        #: target this set and never grow it.
        self.existing: set[str] = set(existing)
        #: operation name -> exception raised after the call is recorded.
        self.failures: dict[str, Exception] = (
            dict(failures) if failures is not None else {}
        )
        #: Every call: ("exists", bcs_bot_id) / ("profile", snapshot).
        self.calls: list[tuple[str, object]] = []
        #: delivered profiles keyed by the stable BCS bot address.
        self.profiles: dict[str, ProfileSnapshot] = {}
        self.closed = False

    async def exists(self, bcs_bot_id: str, *, timeout_seconds: float) -> bool:
        self.calls.append(("exists", bcs_bot_id))
        failure = self.failures.get("exists")
        if failure is not None:
            raise failure
        return bcs_bot_id in self.existing

    async def update_profile(
        self,
        snapshot: ProfileSnapshot,
        *,
        timeout_seconds: float,
    ) -> None:
        self.calls.append(("profile", snapshot))
        self._require_existing(snapshot.bcs_bot_id)
        failure = self.failures.get("profile")
        if failure is not None:
            raise failure
        if not snapshot.summary.strip():
            # Match the existing onboard transport: a blank summary preserves
            # the previous value while name/avatar can still be applied.
            previous = self.profiles.get(snapshot.bcs_bot_id)
            self.profiles[snapshot.bcs_bot_id] = replace(
                snapshot, summary=previous.summary if previous is not None else ""
            )
            return
        self.profiles[snapshot.bcs_bot_id] = snapshot

    async def aclose(self) -> None:
        """No owned resources — only mark, never close injected clients."""
        self.closed = True

    def _require_existing(self, bcs_bot_id: str) -> None:
        if bcs_bot_id not in self.existing:
            # Existing-only contract: absence is a NOT_SENT not-found, never
            # an implicit registration of the addressed bot.
            raise MetadataDeliveryError(DeliveryFailure.NOT_SENT, _NOT_FOUND)
