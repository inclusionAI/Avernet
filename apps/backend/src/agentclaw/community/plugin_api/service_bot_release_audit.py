"""Outbound audit output contract, independent of database or SLS transport."""
from __future__ import annotations

from typing import Protocol

from agentclaw.community.kernel.service_bot_release_audit import ReleaseAuditEvent
from agentclaw.community.plugin_api.base import Plugin


class ServiceBotReleaseAuditPlugin(Plugin, Protocol):
    """Persist one complete immutable audit event.

    In-process trusted plugin: may use only its injected output capability.
    ``record`` returns only after the sink confirms persistence. It may receive
    duplicate or out-of-order events and must preserve the highest revision.
    Failure raises to the durable delivery consumer. No startup/cleanup is
    required; database lifecycle belongs to DatabasePlugin. Implementations
    must not drive publication, reconstruct snapshots, or log credentials.
    """

    def record(self, event: ReleaseAuditEvent) -> None: ...
