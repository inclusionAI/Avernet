"""Service API Protocol for applying a bot's configuration manifest (#1472).

Impl: ``core/bot_config_manifest/services/config_manifest_apply_service.py``
::``BotConfigManifestApplyService``. Re-exported from
``api/bot_config_manifest_apply_service.py``, which is where adapters import it.

**A second contract, not more methods on ``BotConfigManifestServiceProtocol``.**
Rule 9: that contract's reason to change is *what a document may be*; this one's
is *what applying does*. They also carry different authorization bars, have
different callers — W13 calls this one, and calls it one phase at a time — and
reach different graphs: the document service touches a repository and the
capability resolver, this one touches the bot-configuration services.

Every member is ``@abstractmethod`` and the concrete service **inherits** this
Protocol, the shape the sibling manifest contract and the repository contracts
use. The ``(Protocol, ConcreteService)`` pair is registered in
``tests/community/architecture/test_service_api_conformance.py``, which compares
full signatures — inheritance catches a *missing* member, that catches a drifted
one.

Signatures are keyed on ``(entity_id, bot_id)`` for the reason the sibling
records: that pair names one bot for the life of the data, so no row carries an
owner stamp, and ``entity_id`` stays a storage key resolved server-side.
"""
from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import Optional, Protocol, TYPE_CHECKING, runtime_checkable

from agentclaw.community.core.bot_config_manifest.apply.order import ApplyPhase
from agentclaw.community.core.bot_config_manifest.apply.outcomes import (
    ApplyReport,
    ApplyStatus,
)

if TYPE_CHECKING:
    pass


class ManifestApplyInProgressError(RuntimeError):
    """Another apply already holds this bot's lock.

    Retryable, and the message says so. Raised **before** an ``apply_id`` is
    minted, so a caller never receives an id for an apply that did not start.
    """

    def __init__(self, bot_id: str) -> None:
        super().__init__(
            f"an apply is already running for bot {bot_id}; wait for it to "
            "finish and retry"
        )
        self.bot_id = bot_id


@dataclass(frozen=True)
class ApplyAccepted:
    """What starting an apply returns: the handle, and the state it starts in.

    Deliberately not the report. The work has not happened yet — that is the
    whole point of the shape — so returning anything report-shaped here would
    invite a caller to read outcomes that do not exist.
    """

    apply_id: str
    status: ApplyStatus


__all__ = [
    "ApplyAccepted",
    "ApplyPhase",
    "ApplyReport",
    "BotConfigManifestApplyServiceProtocol",
    "ManifestApplyInProgressError",
]


@runtime_checkable
class BotConfigManifestApplyServiceProtocol(Protocol):
    """Apply a bot's stored manifest, and read what an apply did."""

    @abstractmethod
    def start_apply(
        self,
        *,
        entity_id: str,
        bot_id: str,
        bot: dict,
        owner_id: str,
        actor_id: str,
        audit_actor: Optional[str] = None,
        trigger: str = "explicit",
        phases: Optional[frozenset[ApplyPhase]] = None,
    ) -> ApplyAccepted:
        """Take the lock, validate, record ``RUNNING``, start the work, return.

        **Does not wait for the apply.** Applying is device I/O today and
        network fetching from W5; a caller must never hold an HTTP connection
        open across it.

        Everything that can be answered immediately is answered immediately, and
        **before an id is minted**: a held lock raises, and a stored document
        that no longer validates raises. A caller therefore never receives an
        ``apply_id`` for an apply that did not start, and never has to poll to
        discover their document was bad.

        ``phases`` defaults to both, which is what an apply on an existing bot
        wants. W13 passes one at a time — ``PRE_CONTAINER`` before the start
        command is composed, ``ON_CONTAINER`` once the container is up.

        Raises:
            ManifestApplyInProgressError: Another apply holds this bot's lock.
            ManifestValidationError: The stored document no longer validates for
                this bot. Re-validated rather than trusted from the ``PUT`` that
                accepted it, because a bot's engine can change afterwards and
                the construct that was appliable then may not be now.
        """
        ...

    @abstractmethod
    async def dry_run(
        self,
        *,
        entity_id: str,
        bot_id: str,
        bot: dict,
        owner_id: str,
        actor_id: str,
    ) -> ApplyReport:
        """Compute the plan and return it. Writes nothing; mints no id.

        Synchronous, unlike :meth:`start_apply`, because a preview whose answer
        arrives later by polling is not a preview. That is honest only while
        nothing is fetched — W5 must revisit it the moment ``resolve`` makes a
        network call.
        """
        ...

    @abstractmethod
    def get_apply(
        self, *, entity_id: str, bot_id: str, apply_id: str
    ) -> Optional[ApplyReport]:
        """One apply's report by id, in progress or finished.

        ``None`` when this bot has no such apply — including when the id belongs
        to a *different* bot, because the lookup is scoped to the bot key. The
        id is the caller's handle, never what authorizes the read.
        """
        ...

    @abstractmethod
    def last_apply(
        self, *, entity_id: str, bot_id: str
    ) -> Optional[ApplyReport]:
        """The newest report for this bot, or ``None`` if it never applied.

        ``None``, never an error — the same "absent is not an error" rule the
        manifest's own ``get`` follows.
        """
        ...
