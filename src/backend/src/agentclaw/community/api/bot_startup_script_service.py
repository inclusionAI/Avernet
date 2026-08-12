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
#: Derived from where the body actually has to fit, not chosen for roundness.
#:
#: The script rides inside ``after_create_cmd_hook``, and the device service
#: serialises that hook into ``baas_bot.extra_config`` and
#: ``baas_publish.extra_config`` — both ``Text`` columns, which is 65,535 bytes
#: on MySQL/OceanBase. base64 costs 4/3, so the raw cap has to leave room for
#: the expansion *and* for the rest of that JSON (template config, mounts, env).
#:
#:     24 KiB raw -> 32,768 base64 -> ~34.8 KB of hook, ~30 KB left for the rest.
#:
#: 64 KiB was the first value here and does not survive that arithmetic: it
#: expands to 87,384 bytes and overflows the column on its own, so a script
#: written near the advertised limit would be accepted and then fail to persist
#: on the next restart or republish — after the write had already succeeded.
#: 24 KiB is still far above any real provisioning script.
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
        self, *, entity_id: str, bot_id: str, bot_incarnation: int
    ) -> Optional[BotStartupScriptRecord]:
        """Return the stored script, or ``None`` when the bot has none.

        ``bot_incarnation`` is the ``ac_bots.id`` of the bot asking. A row
        stored by an earlier bot that held the same ``bot_id`` is not this
        bot's script and reads as ``None``.
        """
        ...

    @abstractmethod
    def put(
        self,
        *,
        entity_id: str,
        bot_id: str,
        script: str,
        modifier: str,
        bot_incarnation: int,
    ) -> BotStartupScriptRecord:
        """Store or replace the script; raises when it exceeds the size cap.

        The stored row is stamped with ``bot_incarnation``, which is what every
        later read checks it against.
        """
        ...

    @abstractmethod
    def delete(self, *, entity_id: str, bot_id: str) -> bool:
        """Clear the script. Idempotent.

        Unconditional — clears whichever incarnation's row is at the key.
        """
        ...

    @abstractmethod
    def delete_written_by(
        self, *, entity_id: str, bot_id: str, bot_incarnation: int
    ) -> bool:
        """Withdraw a row only if that incarnation still owns it.

        For a write taking back its own row after finding its bot deleted; the
        condition keeps it from removing a script a recreated bot has since
        stored at the same key.
        """
        ...

    @abstractmethod
    def resolve_support(self, bot: dict) -> tuple[str, str]:
        """Return ``(state, reason)`` — "supported" or "unsupported"."""
        ...

    @abstractmethod
    def get_body(self, *, entity_id: str, bot_id: str, bot_incarnation: int) -> str:
        """Return the script body, or ``""`` when the bot has none.

        ``""`` also covers a row belonging to an earlier incarnation — this is
        the read whose result is executed, so it must never return one.
        """
        ...
