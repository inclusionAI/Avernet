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

That pair identifies one bot for good, so nothing here carries an owner stamp
alongside it: ``ac_bots`` constrains ``uk_bot_id_entity_id_env`` and its
deletion is a soft update, so a deleted bot goes on holding the tuple and no
later bot can be created onto it.
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

#: Maximum stored script size, in UTF-8 bytes.
#:
#: Declared here, in the contract, rather than in the service that enforces it.
#: Both sides need it and they sit on opposite sides of the boundary: the HTTP
#: layer documents the limit in the published 413 and maps the refusal, while
#: the core service is what actually refuses. Putting it in the service made a
#: shared adapter contract module import a concrete implementation; putting it
#: in the service and re-exporting it here closed an ``api`` ↔ ``core`` import
#: cycle. Defining it once, on the contract, is what avoids both.
#:
#: A safety bound, deliberately far below where the body has to fit. It is not
#: derived from the column width, and an earlier version of this comment that
#: claimed it was had the width wrong.
#:
#: The script does ride inside ``after_create_cmd_hook``, and that hook is
#: serialised into ``baas_bot.extra_config`` and ``baas_publish.extra_config``
#: (``_bot_management_service.py``, ``extra_config=stored_config.model_dump()``).
#: But production declares both columns ``mediumtext`` — 16,777,215 bytes, not
#: the 65,535 of ``Text``. base64 costs 4/3, so 24 KiB raw reaches ~34.8 KB of
#: hook, which is 0.2% of the column. The rest of that JSON (template config,
#: mounts, env) has room to spare in any realistic shape.
#:
#: So the limit is not doing the job of keeping the row under the column, and
#: should not be read as a promise that it does. Two reasons it stays anyway:
#: a provisioning script is a boot-time shell body and nothing legitimate comes
#: near 24 KiB, and an unbounded field that lands in a device boot chain wants
#: some ceiling regardless of what the storage happens to allow today.
#:
#: A point-in-time check could not make that promise even if it tried. The
#: script is one term in a sum whose other terms — template envs, mounts,
#: outbound config — are editable after the script is accepted. Validating the
#: fully serialised payload at PUT time would pass, and the next env added
#: would overflow it just the same, with no write to refuse. Keeping the row
#: inside the column is the column's job, which is why the ``Text``-vs-
#: ``mediumtext`` divergence in the BaaS ORM is filed separately rather than
#: papered over here.
MAX_SCRIPT_BYTES = 24 * 1024


class StartupScriptTooLargeError(ValueError):
    """Raised when a submitted script exceeds :data:`MAX_SCRIPT_BYTES`.

    The message names the limit and the actual size — a caller should not have
    to guess by bisecting their script. Lives beside the limit for the same
    reason: the adapter maps it to a 413 and must be able to name it without
    reaching into the service.
    """

    def __init__(self, size_bytes: int) -> None:
        super().__init__(
            f"startup script is {size_bytes} bytes, "
            f"which exceeds the {MAX_SCRIPT_BYTES}-byte limit"
        )
        self.size_bytes = size_bytes
        self.limit_bytes = MAX_SCRIPT_BYTES


class StartupScriptNotEncodableError(ValueError):
    """Raised when a submitted script is not encodable UTF-8.

    JSON permits an escaped lone surrogate (``"\\ud800"``) and Pydantic's ``str``
    passes it through, but encoding one raises — so without this the size check
    downstream turns client-controlled input into a 500. Refused as a bad
    request instead, beside the size cap and for the same reason: a body problem
    should be answered at write time, not become an unhandled crash.
    """

    def __init__(self) -> None:
        super().__init__("startup script is not encodable as UTF-8")


__all__ = [
    "BotStartupScriptServiceProtocol",
    "MAX_SCRIPT_BYTES",
    "StartupScriptNotEncodableError",
    "StartupScriptTooLargeError",
    "SUPPORTED",
    "UNSUPPORTED",
]


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
        self,
        *,
        entity_id: str,
        bot_id: str,
        script: str,
        modifier: str,
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
