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
from typing import Optional, Protocol, runtime_checkable

from agentclaw.community.core.bot_config_manifest.apply.delivery import DeliveryStrategy
from agentclaw.community.core.bot_config_manifest.apply.order import (
    ALL_PHASES,
    ApplyPhase,
)
from agentclaw.community.core.bot_config_manifest.apply.outcomes import (
    ApplyConstruct,
    ApplyReport,
    ApplyStatus,
)


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
    "ALL_PHASES",
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
        bot: Optional[dict] = None,
        owner_id: str,
        actor_id: str,
        audit_actor: Optional[str] = None,
        trigger: str = "explicit",
        phases: frozenset[ApplyPhase],
        engine_type: Optional[str] = None,
        bot_type: Optional[str] = None,
        carry_from_apply_id: Optional[str] = None,
    ) -> ApplyAccepted:
        """Take the lock, validate, record ``RUNNING``, start the work, return.

        ``bot`` is optional for one caller: W13 applies the pre-container phase
        **before** the bot record exists, and passes ``engine_type`` /
        ``bot_type`` from the creation request instead. Every other caller has a
        record and passes it.

        ``carry_from_apply_id`` folds an earlier apply's categories into this
        one's report, so a creation's two phases read as one story. A missing or
        foreign id is ignored rather than fatal.

        **Does not wait for the apply.** Applying is device I/O today and
        network fetching from W5; a caller must never hold an HTTP connection
        open across it.

        Everything that can be answered immediately is answered immediately, and
        **before an id is minted**: a held lock raises, and a stored document
        that no longer validates raises. A caller therefore never receives an
        ``apply_id`` for an apply that did not start, and never has to poll to
        discover their document was bad.

        ``phases`` is **required, with no default**. An omitted-means-both
        default read fine at the one call site that wanted both and badly
        everywhere else: what an apply covers is the single most consequential
        thing about it — W13's pre-container phase writing the startup-script
        row before the container exists is the whole ordering guarantee — and a
        caller that leaves it out is not stating a choice, it is inheriting one.
        The explicit ``POST .../apply`` passes ``ALL_PHASES``, which says the
        same thing the default said and says it where the reader is.

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
        """Compute the plan and return it. Writes no bot state; mints no id.

        Synchronous, unlike :meth:`start_apply`, because a preview whose answer
        arrives later by polling is not a preview. W5 revisited it, as the
        old tripwire demanded, and the honest statement is narrower than
        "writes nothing": fetch belongs to ``resolve``, so a declared source
        **may really be fetched** (bounded by the same per-apply ledger a
        real apply uses) — and the bytes the platform acquires on the bot's
        behalf are filed as the platform's own copy, because §2.8's audit
        trail is a fact about acquisition, not delivery. What a dry run
        never touches: any write path — nothing is materialised, activated,
        removed, or answered async.
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
    def materialised_constructs(self) -> frozenset["ApplyConstruct"]:
        """Which constructs some shipped code can actually act on, right now.

        **Derived from the registry, never a list written by hand.** The
        implementation returns the keys of the same registry the orchestrator
        builds, so the two cannot disagree — W5 widened this from two constructs
        to four by registering materialisers and editing nothing else, and W6
        widens it the same way.

        The alternative was a hand-written constant. It would drift, and the
        drift is invisible until the worst moment: a creation endpoint that
        gates on it would accept a category nothing can apply, spend a Passport
        application, take a user through authorization, create the bot, and only
        then fail the apply.
        """
        ...

    @abstractmethod
    def delivery_for_engine(self, engine_type: Optional[str]) -> DeliveryStrategy:
        """The delivery strategy bots of this engine apply through (W8).

        What a caller asks it: the creation sequence (the W13 job and its poll),
        and whether any construct needs a live container (the ``PUT`` route's
        not-ACTIVE warning). The strategy is selected by the engine authority
        and the platform-managed switch, read once per call.
        """
        ...

    @abstractmethod
    def delivery_for_bot(self, bot: dict) -> DeliveryStrategy:
        """``delivery_for_engine`` for a bot record."""
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
