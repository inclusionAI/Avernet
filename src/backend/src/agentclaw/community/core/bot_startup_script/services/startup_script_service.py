"""Per-bot startup script service (issue #926).

Owns the rules the repository deliberately does not: the size cap, the
env scoping, and the "absent is not an error" contract that the payload-build
path depends on.

The service **inherits** ``BotStartupScriptServiceProtocol``, whose members are
``@abstractmethod``: dropping one fails at construction naming it rather than at
some later call site. ``PublishApprovalService`` takes the same core-implements-
api shape. ``test_service_api_conformance.py`` still checks full signatures on
top, which inheritance alone does not.

``StartupScriptReaderProtocol`` — the narrow read-only view ``BaasService`` holds
while composing a start command — is inherited for the same reason. It stays a
separate, smaller contract on purpose: the code that builds shell strings should
not be able to reach the write side.
"""
from __future__ import annotations

from typing import Callable, Optional

from injector import inject


from agentclaw.community.core.bot_startup_script.repository.models import (
    BotStartupScriptRecord,
)
from agentclaw.community.api.bot_startup_script_service import (
    MAX_SCRIPT_BYTES,
    BotStartupScriptServiceProtocol,
    StartupScriptTooLargeError,
)
from agentclaw.community.core.bot_startup_script.protocols import (
    StartupScriptReaderProtocol,
    TeclawEngineTestProtocol,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotStartupScriptRepositoryProtocol,
)
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env


logger = get_logger()

#: Re-exported so existing importers of this module keep working. The
#: definitions live on the contract in ``api/`` — see there for why.
__all__ = [
    "BotStartupScriptService",
    "MAX_SCRIPT_BYTES",
    "MAX_MODIFIER_CHARS",
    "StartupScriptTooLargeError",
]

#: Column width of ``ac_bot_startup_script.modifier``.
#:
#: The audit actor is composed by the caller and can legitimately exceed this:
#: an application's actor carries the delegating user's id, and ``owner_id`` is
#: itself a 1024-character column, so ``app:7:on-behalf-of:<1024 chars>`` is
#: reachable without anything being malformed. Persisting it unbounded fails an
#: otherwise valid write at the database.
#:
#: Bounded here rather than at the one caller that composes a prefix today, so
#: every caller is covered by construction.
MAX_MODIFIER_CHARS = 1024


class BotStartupScriptService(
    BotStartupScriptServiceProtocol, StartupScriptReaderProtocol
):
    """Read, replace and clear a bot's startup script."""

    @inject
    def __init__(
        self,
        repository: BotStartupScriptRepositoryProtocol,
        teclaw_engine_test_provider: Callable[[], TeclawEngineTestProtocol],
    ) -> None:
        self._repository = repository
        # Lazy *and* narrow. Lazy for the reason ``BotService`` holds it this
        # way — teclaw provisioning pulls in BaasService, whose graph reaches
        # back here. Narrow because importing the concrete class closes an
        # import cycle at module load; see ``TeclawEngineTestProtocol``.
        self._teclaw_provision_provider = teclaw_engine_test_provider

    def get(
        self, *, entity_id: str, bot_id: str
    ) -> Optional[BotStartupScriptRecord]:
        """Return the stored script, or ``None`` when the bot has none."""
        return self._repository.get(
            env=get_current_env(), entity_id=entity_id, bot_id=bot_id
        )

    def put(
        self, *, entity_id: str, bot_id: str, script: str, modifier: str
    ) -> BotStartupScriptRecord:
        """Store or replace the script.

        Args:
            modifier: The acting user, resolved from the request principal.
                Never supplied by the client.

        Raises:
            StartupScriptTooLargeError: When the body exceeds the size cap.
        """
        size_bytes = len(script.encode("utf-8"))
        if size_bytes > MAX_SCRIPT_BYTES:
            raise StartupScriptTooLargeError(size_bytes)
        return self._repository.upsert(
            env=get_current_env(),
            entity_id=entity_id,
            bot_id=bot_id,
            script=script,
            size_bytes=size_bytes,
            # Truncated, not rejected: an over-long actor is the platform's own
            # composition meeting a legitimately long user id, so failing the
            # caller's write for it would be blaming them for our formatting.
            # The front is kept because that is where the acting identity is —
            # a truncated tail costs some of the delegating id, never which
            # application acted.
            modifier=modifier[:MAX_MODIFIER_CHARS],
        )

    def delete(self, *, entity_id: str, bot_id: str) -> bool:
        """Clear the script. Idempotent — clearing an absent script succeeds."""
        return self._repository.delete(
            env=get_current_env(), entity_id=entity_id, bot_id=bot_id
        )

    def resolve_support(self, bot: dict) -> tuple[str, str]:
        """Return ``(state, reason)`` — SUPPORTED or UNSUPPORTED.

        Lives on the service so the HTTP adapter can reach it through the
        Service API Protocol — a router importing the rule directly would cross
        the adapter/core boundary the architecture gate enforces.

        The engine test is delegated to :class:`TeclawProvisionService`, the
        single definition of "runs in a teclaw container", rather than being
        re-derived here.
        """
        from agentclaw.community.core.bot_startup_script.services._support import (
            resolve_support,
        )

        return resolve_support(bot, self._teclaw_provision_provider().is_teclaw)

    def get_body(self, *, entity_id: str, bot_id: str) -> str:
        """Return the script body, or ``""`` when the bot has none.

        The payload-build path composes a shell string and must never branch on
        ``None``; an empty body is the "no script" signal there, and
        ``_get_start_cmd`` returns its unchanged chain for it.
        """
        record = self.get(entity_id=entity_id, bot_id=bot_id)
        return record.script if record is not None else ""
