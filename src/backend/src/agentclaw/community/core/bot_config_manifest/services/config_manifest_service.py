"""Per-bot configuration manifest service (issue #1469).

Owns the rules the repository deliberately does not: what a document has to be
before it can be stored, which constructs this build can accept, and the
"absent is not an error" contract every read depends on.

The service **inherits** ``BotConfigManifestServiceProtocol``, whose members are
``@abstractmethod``: dropping one fails at construction naming it rather than at
some later call site. ``BotStartupScriptService`` takes the same
core-implements-api shape, and ``test_service_api_conformance.py`` still checks
full signatures on top, which inheritance alone does not.

**Validation happens here, once, for every entry point.** The HTTP ``PUT``, and
W13's pre-creation preflight, both land on :meth:`validate` — there is no second
implementation for the path that has no bot record, because the capability
resolver answers from an engine type and a bot type rather than from a bot.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from injector import inject

from agentclaw.community.core.bot_config_manifest.bot_config_manifest_service_protocol import (
    BotConfigManifestServiceProtocol,
    ManifestWriteResult,
)
from agentclaw.community.core.bot_config_manifest.capabilities import (
    ManifestCapabilities,
    capabilities_for_bot,
    resolve_capabilities,
)
from agentclaw.community.core.bot_config_manifest.repository.models import (
    BotConfigManifestRecord,
)
from agentclaw.community.core.bot_config_manifest.schema import (
    ValidationResult,
    validate_document,
)
from agentclaw.community.core.bot_startup_script.protocols import (
    TeclawEngineTestProtocol,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotConfigManifestRepositoryProtocol,
)
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()

#: Column width of ``ac_bot_config_manifest.modifier``.
#:
#: The audit actor is composed by the caller and can legitimately exceed this:
#: an application's actor carries the delegating user's id, and ``owner_id`` is
#: itself a 1024-character column, so ``app:7:on-behalf-of:<1024 chars>`` is
#: reachable without anything being malformed. Persisting it unbounded fails an
#: otherwise valid write at the database, so it is bounded here — where every
#: caller is covered — rather than at whichever caller composes a prefix today.
MAX_MODIFIER_CHARS = 1024

__all__ = ["BotConfigManifestService", "MAX_MODIFIER_CHARS"]


class BotConfigManifestService(BotConfigManifestServiceProtocol):
    """Read, replace, clear and describe a bot's configuration manifest."""

    @inject
    def __init__(
        self,
        repository: BotConfigManifestRepositoryProtocol,
        teclaw_engine_test_provider: Callable[[], TeclawEngineTestProtocol],
    ) -> None:
        self._repository = repository
        # Lazy *and* narrow, for the reason ``BotStartupScriptService`` holds it
        # the same way: teclaw provisioning pulls in BaasService, whose graph
        # reaches back here, and importing the concrete class closes an import
        # cycle at module load. The one-method contract is reused rather than
        # redeclared — there is one definition of "runs in a teclaw container"
        # and a second copy would be a second answer.
        self._teclaw_engine_test_provider = teclaw_engine_test_provider

    # ── capabilities ────────────────────────────────────────────────────────

    def resolve_capabilities(
        self, *, active_engine: str | None, bot_type: str | None
    ) -> ManifestCapabilities:
        """Which constructs this engine and bot type accept.

        The entry point W13 uses: no bot record is needed, because none exists
        during the first leg of bot creation.
        """
        return resolve_capabilities(
            active_engine=active_engine,
            bot_type=bot_type,
            is_teclaw=self._teclaw_engine_test_provider().is_teclaw,
        )

    def capabilities_for_bot(self, bot: dict[str, Any]) -> ManifestCapabilities:
        """The same answer for a bot that already has a record."""
        return capabilities_for_bot(bot, self._teclaw_engine_test_provider().is_teclaw)

    # ── validation ──────────────────────────────────────────────────────────

    def validate(
        self,
        *,
        document: str,
        active_engine: str | None,
        bot_type: str | None,
    ) -> ValidationResult:
        """Validate without storing.

        Raises:
            ManifestNotEncodableError: The document is not encodable UTF-8.
            ManifestTooLargeError: The document exceeds the size limit.
            ManifestValidationError: Carries the full list of violations.
        """
        return validate_document(
            document,
            self.resolve_capabilities(
                active_engine=active_engine, bot_type=bot_type
            ),
        )

    # ── storage ─────────────────────────────────────────────────────────────

    def get(
        self, *, entity_id: str, bot_id: str
    ) -> Optional[BotConfigManifestRecord]:
        """Return the bot's stored manifest, or ``None``.

        No ownership check on top of the key: ``(env, entity_id, bot_id)`` names
        one bot for the life of the data, because ``ac_bots`` constrains
        ``uk_bot_id_entity_id_env`` and its deletion is a soft update, so a
        deleted bot keeps the tuple and no later bot can be created onto it.
        """
        return self._repository.get(
            env=get_current_env(), entity_id=entity_id, bot_id=bot_id
        )

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
        """Validate and store the document.

        All-or-nothing: :meth:`validate` raises before the repository is
        reached, so a refused document leaves the previous one — or the absence
        of one — exactly as it was.

        Args:
            modifier: The acting user, resolved from the request principal.
                Never supplied by the client.
        """
        result = self.validate(
            document=document, active_engine=active_engine, bot_type=bot_type
        )
        record = self._repository.upsert(
            env=get_current_env(),
            entity_id=entity_id,
            bot_id=bot_id,
            # The caller's bytes. Never ``yaml.safe_dump(result.parsed)``: a
            # round trip preserves the document's *value*, and ``script.body``
            # is a shell body whose bytes are its meaning.
            document=document,
            size_bytes=len(document.encode("utf-8")),
            schema_version=result.schema_version,
            # Truncated, not rejected: an over-long actor is the platform's own
            # composition meeting a legitimately long user id, so failing the
            # caller's write for it would be blaming them for our formatting.
            # The front is kept because that is where the acting identity is.
            modifier=modifier[:MAX_MODIFIER_CHARS],
        )
        return ManifestWriteResult(
            record=record,
            warnings=result.warnings,
            declares_script=result.parsed.get("script") is not None,
        )

    def delete(self, *, entity_id: str, bot_id: str) -> bool:
        """Remove the stored manifest. Idempotent — absent is success.

        Entities a previous apply materialized are **not** touched. Nothing here
        has materialized anything yet; detaching them from the apply record
        lands with that record, in W4.

        **Deleting the bot does not yet delete its manifest.** Bot deletion is a
        soft update and no cascade reaches this table, so the row outlives its
        bot. It cannot be inherited — the key names one bot for the life of the
        data — so nothing will ever read it, but a caller's configuration does
        outlive the bot they deleted. The narrow purge seam belongs with the
        rest of this feature's per-bot state (the apply record, W4) rather than
        as a lone protocol wired into the deletion path ahead of it; recorded
        here and in the module README rather than left to be discovered.
        """
        return self._repository.delete(
            env=get_current_env(), entity_id=entity_id, bot_id=bot_id
        )

