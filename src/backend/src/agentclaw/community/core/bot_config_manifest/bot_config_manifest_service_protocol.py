"""Service API Protocol for the per-bot configuration manifest (issue #1469).

Impl: ``core/bot_config_manifest/services/config_manifest_service.py``
::``BotConfigManifestService``. Re-exported from
``api/bot_config_manifest_service.py``, which is where adapters import it.

Every member is ``@abstractmethod`` and the concrete service **inherits** this
Protocol — the same shape ``BotStartupScriptServiceProtocol`` and the repository
contracts use. Omitting a member then fails at construction naming it, rather
than surfacing as an ``AttributeError`` at some later call site. The structural
check runs alongside: the ``(Protocol, ConcreteService)`` pair is registered in
``tests/community/architecture/test_service_api_conformance.py``, which compares
full signatures — inheritance catches a *missing* member, that catches a drifted
one.

Signatures are keyed on ``(entity_id, bot_id)`` rather than on an owner. That
pair identifies one bot for good — ``ac_bots`` constrains
``uk_bot_id_entity_id_env`` and its deletion is a soft update, so a deleted bot
goes on holding the tuple and no later bot can be created onto it — so no row
here carries an owner stamp. ``entity_id`` is resolved server-side from the bot
record and never appears on the public API.

**Two capability entry points, deliberately.** ``resolve_capabilities`` takes an
engine type and a bot type; ``capabilities_for_bot`` takes a record. W13
validates a manifest during bot creation, before any record exists, and a
contract offering only the record-shaped call would force a second
implementation into that path — the one thing the "same function on the read and
write paths" criterion exists to prevent.
"""
from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import Any, Optional, Protocol, TYPE_CHECKING, runtime_checkable

from agentclaw.community.core.bot_config_manifest.capabilities import (
    ManifestCapabilities,
)
from agentclaw.community.core.bot_config_manifest.schema import (
    MAX_DOCUMENT_BYTES,
    ManifestNotEncodableError,
    ManifestTooLargeError,
    ManifestValidationError,
    ValidationResult,
    Violation,
)

if TYPE_CHECKING:
    from agentclaw.community.core.bot_config_manifest.repository.models import (
        BotConfigManifestRecord,
    )


@dataclass(frozen=True)
class ManifestWriteResult:
    """What a successful ``PUT`` produced.

    The warnings ride with the record rather than being logged: schema §2.3
    promises a caller is *told* about a declared-but-unreferenced source, and a
    note nobody sees is not a note. They are non-fatal by definition — anything
    that should refuse the write is a violation instead.
    """

    record: BotConfigManifestRecord
    warnings: tuple[str, ...]
    #: Whether the document declares a startup ``script`` (W8): the one
    #: construct a running bot does not pick up until its next start, which
    #: the ``PUT`` response tells the caller about.
    declares_script: bool = False


__all__ = [
    "BotConfigManifestServiceProtocol",
    "MAX_DOCUMENT_BYTES",
    "ManifestCapabilities",
    "ManifestNotEncodableError",
    "ManifestTooLargeError",
    "ManifestValidationError",
    "ManifestWriteResult",
    "ValidationResult",
    "Violation",
]


@runtime_checkable
class BotConfigManifestServiceProtocol(Protocol):
    """Read, replace, clear and describe a bot's configuration manifest."""

    @abstractmethod
    def get(
        self, *, entity_id: str, bot_id: str
    ) -> Optional[BotConfigManifestRecord]:
        """Return the stored manifest, or ``None`` when the bot has none.

        ``None``, never an error: a bot that has never carried a manifest reads
        as an empty document, the same rule ``bot_startup_script`` set.
        """
        ...

    @abstractmethod
    def put(
        self,
        *,
        entity_id: str,
        bot_id: str,
        document: str,
        modifier: str,
        active_engine: str | None,
        bot_type: str | None,
    ) -> ManifestWriteResult:
        """Validate and store the document. All-or-nothing.

        Nothing is written unless every rule passes: one unsupported category
        refuses the whole document.

        Raises:
            ManifestNotEncodableError: The document is not encodable UTF-8.
            ManifestTooLargeError: The document exceeds the size limit.
            ManifestValidationError: Carries the full list of violations.
        """
        ...

    @abstractmethod
    def delete(self, *, entity_id: str, bot_id: str) -> bool:
        """Remove the stored manifest. Idempotent — absent is success."""
        ...

    @abstractmethod
    def validate(
        self,
        *,
        document: str,
        active_engine: str | None,
        bot_type: str | None,
    ) -> ValidationResult:
        """Validate without storing — the preflight W13 runs before a bot exists."""
        ...

    @abstractmethod
    def resolve_capabilities(
        self, *, active_engine: str | None, bot_type: str | None
    ) -> ManifestCapabilities:
        """Which constructs this engine and bot type accept."""
        ...

    @abstractmethod
    def capabilities_for_bot(self, bot: dict[str, Any]) -> ManifestCapabilities:
        """The same answer for a bot that already has a record."""
        ...
