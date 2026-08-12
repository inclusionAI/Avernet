"""Service API Protocol for the per-bot startup script (issue #926).

Impl: ``core/bot_startup_script/services/startup_script_service.py``
::``BotStartupScriptService``.

Every member is ``@abstractmethod`` and the concrete service **inherits** this
Protocol, the same shape the repository contracts under
``core/repository/protocols/`` use: omitting a member then fails at construction
naming it, instead of surfacing as an ``AttributeError`` at some later call site.

That the implementation lives in ``core`` and inherits from ``api`` is an
established direction here, not an exception carved for this feature —
``PublishApprovalService(PublishApprovalServiceProtocol)`` does the same, as do
the ``ChannelServiceProtocol`` and ``PolicyServiceProtocol`` consumers.

The structural check still runs alongside it: the ``(Protocol, ConcreteService)``
pair is registered in
``tests/community/architecture/test_service_api_conformance.py``, which checks
member names *and* full signatures — inheritance catches a *missing* member,
that catches a member whose signature drifted.

Signatures are keyed on ``(entity_id, bot_id)`` rather than an owner: the public
surface addresses a bot by ``bot_id`` and the caller's own identity, and the
adapter resolves ``entity_id`` from the bot record before calling in. That keeps
``entity_id`` a storage key and out of the public contract.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Optional, Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.bot_startup_script.repository.models import (
        BotStartupScriptRecord,
    )


#: Support states returned by ``resolve_support``. Re-exported here so the
#: HTTP adapter can branch on them without importing a core service module.
SUPPORTED = "supported"
UNSUPPORTED = "unsupported"


@runtime_checkable
class BotStartupScriptServiceProtocol(Protocol):
    """Read, replace and clear a bot's startup script."""

    @abstractmethod
    def get(
        self, *, entity_id: str, bot_id: str
    ) -> Optional[BotStartupScriptRecord]:
        """Return the stored script, or ``None`` when the bot has none."""
        ...

    @abstractmethod
    def put(
        self, *, entity_id: str, bot_id: str, script: str, modifier: str
    ) -> BotStartupScriptRecord:
        """Store or replace the script; raises when it exceeds the size cap."""
        ...

    @abstractmethod
    def delete(self, *, entity_id: str, bot_id: str) -> bool:
        """Clear the script. Idempotent."""
        ...

    @abstractmethod
    def resolve_support(self, bot: dict) -> tuple[str, str]:
        """Return ``(state, reason)`` — "supported" or "unsupported"."""
        ...

    @abstractmethod
    def get_body(self, *, entity_id: str, bot_id: str) -> str:
        """Return the script body, or ``""`` when the bot has none."""
        ...
