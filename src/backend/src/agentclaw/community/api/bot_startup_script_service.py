"""Service API Protocol for the per-bot startup script (issue #926).

Impl: ``core/bot_startup_script/services/startup_script_service.py``
::``BotStartupScriptService``.

No ``@abstractmethod`` here, deliberately. Unlike the repository contracts under
``core/repository/protocols/``, a Service API Protocol is never inherited — that
would force a ``core -> api`` import the layering rule forbids (see
``api/README.md``) — so ``@abstractmethod`` would bind nothing. Conformance is
structural instead, and the ``(Protocol, ConcreteService)`` pair is registered in
``tests/community/architecture/test_service_api_conformance.py``, which checks
member names *and* full signatures.

Signatures are keyed on ``(entity_id, bot_id)`` rather than an owner: the public
surface addresses a bot by ``bot_id`` and the caller's own identity, and the
adapter resolves ``entity_id`` from the bot record before calling in. That keeps
``entity_id`` a storage key and out of the public contract.
"""
from __future__ import annotations

from typing import Optional, Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.bot_startup_script.repository.models import (
        BotStartupScriptRecord,
    )


#: Support states returned by ``resolve_support``. Re-exported here so the
#: HTTP adapter can branch on them without importing a core service module.
SUPPORTED = "supported"
UNSUPPORTED = "unsupported"
UNKNOWN = "unknown"


@runtime_checkable
class BotStartupScriptServiceProtocol(Protocol):
    """Read, replace and clear a bot's startup script."""

    def get(
        self, *, entity_id: str, bot_id: str
    ) -> Optional[BotStartupScriptRecord]:
        """Return the stored script, or ``None`` when the bot has none."""
        ...

    def put(
        self, *, entity_id: str, bot_id: str, script: str, modifier: str
    ) -> BotStartupScriptRecord:
        """Store or replace the script; raises when it exceeds the size cap."""
        ...

    def delete(self, *, entity_id: str, bot_id: str) -> bool:
        """Clear the script. Idempotent."""
        ...

    def resolve_support(self, bot: dict) -> tuple[str, str]:
        """Return ``(state, reason)`` — "supported" / "unsupported" / "unknown"."""
        ...

    def get_body(self, *, entity_id: str, bot_id: str) -> str:
        """Return the script body, or ``""`` when the bot has none."""
        ...


@runtime_checkable
class BotStartupScriptRunReaderProtocol(Protocol):
    """Read the outcome of a bot's last container start.

    Separate from :class:`BotStartupScriptServiceProtocol` because ``BaasService``
    depends on that one; a single service holding both would close a cycle.
    """

    def last_start(self, *, publish_id: int | None) -> list:
        """One result per instance; empty when nothing is known."""
        ...
