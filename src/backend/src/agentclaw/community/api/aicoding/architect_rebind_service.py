"""Service API Protocol for the aicoding architect-bot rebind feature.

The rebind endpoint (``PUT /api/bots/{architect_bot_id}/architect-rebind``)
is aicoding-creation specific, so the service contract lives under the
aicoding namespace rather than the generic bot-management one. The concrete
implementation is
:class:`agentclaw.community.core.aicoding.services.architect_rebind_service.ArchitectRebindService`.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ArchitectRebindServiceProtocol(Protocol):
    """Service API for rebinding application-coding bots to a domain architect bot."""

    def rebind_architect_bot(self, *args: Any, **kwargs: Any) -> Any: ...

    def rebind_architect_bot_batch(self, *args: Any, **kwargs: Any) -> Any: ...
