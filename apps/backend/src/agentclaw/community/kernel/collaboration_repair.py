"""Collaboration repair — the fixed, transport-free type dictionary.

Shared vocabulary for repairing one Bot's BCS collaboration metadata
(profile, ownership, managers) from committed Backend
facts. Every layer exchanges these values: the api Service Protocol
(``api/collaboration_repair_service.py`` re-exports the Core-owned
contract), the read repository contracts, and the metadata Plugin
Protocol (``BcsCollaborationMetadataPlugin``). Names and shapes below
form the common type dictionary for these implementations.

Kernel discipline holds: stdlib plus ``kernel.bot_manager_sync`` only —
no HTTP, no Redis, no database. HTTP status mapping belongs to the
adapter, never here. Field names are frozen end to end; ``error_code``
is the only optional value; its ``None`` meaning is pinned in its docstring.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from agentclaw.community.kernel.bot_manager_sync import DeliveryFailure, SyncKey


Component = Literal["ownership", "profile", "managers"]
ItemStatus = Literal[
    "succeeded", "failed", "unknown", "skipped_dependency"
]
ReportStatus = Literal["succeeded", "partial_failed", "failed"]

#: The report's fixed component order, owned here so the service that
#: produces it and the report schema that serializes it cannot drift.
COMPONENT_ORDER: tuple[Component, ...] = (
    "ownership",
    "profile",
    "managers",
)


class RepairFactsErrorCode(StrEnum):
    """Finite machine reasons why repair facts could not be read.

    No code here means "produce a payload anyway": a missing Bot, an
    unresolvable stable identity, an unreadable saved avatar projection,
    or a failed facts read never silently becomes a default payload.
    """

    COLLABORATION_BOT_NOT_FOUND = "collaboration_bot_not_found"
    COLLABORATION_BOT_IDENTITY_UNRESOLVED = "collaboration_bot_identity_unresolved"
    COLLABORATION_AVATAR_UNREADABLE = "collaboration_avatar_unreadable"
    COLLABORATION_FACTS_READ_ERROR = "collaboration_facts_read_error"


class RepairPreconditionErrorCode(StrEnum):
    """Finite machine reasons why a repair cannot even start.

    ``COLLABORATION_BOT_NOT_FOUND`` names the missing remote target: the
    Bot the command addresses is absent (or not live) on the delivery
    side. It shares its string with ``RepairFactsErrorCode`` deliberately
    — the two are distinct families (a local facts read versus a start
    precondition) and the adapter maps both to the same not-found
    response.
    """

    COLLABORATION_BOT_NOT_FOUND = "collaboration_bot_not_found"
    COLLABORATION_SYNC_UNAVAILABLE = "collaboration_sync_unavailable"
    OWNER_CHANGED = "owner_changed"
    REPAIR_DEADLINE_EXCEEDED = "repair_deadline_exceeded"


class RepairItemErrorCode(StrEnum):
    """Finite per-item error codes carried on failed/unknown repair items."""

    OWNER_DRIFT = "owner_drift"
    DELIVERY_TIMEOUT = "delivery_timeout"


class MetadataDeliveryErrorCode(StrEnum):
    """Finite public vocabulary for ``MetadataDeliveryError.code``.

    The repair service copies a failed delivery's code onto
    ``RepairItem.error_code`` and the report adapter serializes it, so
    this set is the public contract: every member is a stable, sanitized
    machine reason. HTTP status details never leak as codes — a 4xx
    rejection and a 5xx/unconfirmed failure are bucketed into
    ``COLLABORATION_REJECTED`` / ``COLLABORATION_UPSTREAM_ERROR``, and
    upstream bodies and transport class names appear in no member.
    """

    COLLABORATION_BOT_NOT_FOUND = "collaboration_bot_not_found"
    COLLABORATION_SYNC_UNAVAILABLE = "collaboration_sync_unavailable"
    DELIVERY_TIMEOUT = "delivery_timeout"
    COLLABORATION_REJECTED = "collaboration_rejected"
    COLLABORATION_UPSTREAM_ERROR = "collaboration_upstream_error"
    COLLABORATION_RESPONSE_MALFORMED = "collaboration_response_malformed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RepairCommand:
    """One explicit repair request: which Bot, triggered by whom.

    ``actor_user_id`` is the operating identity that asked for the
    repair; it is never substituted for the Bot's Owner.
    """

    key: SyncKey
    actor_user_id: str


@dataclass(frozen=True)
class ProfileSnapshot:
    """Profile facts as projected from the saved Bot record.

    ``bcs_bot_id`` is the established registration reference: service Bots use
    their entity or retained conversion reference; personal/default Bots use
    the existing owner-qualified registration rule. ``name``/``summary`` follow the same
    projection the daily metadata path sends: a missing saved name
    projects to ``bot_id``, a missing saved description to ``""``.
    ``avatar_url`` is ``""`` only as an explicit clear, never as a
    fallback fabricated from unreadable data.
    """

    bot_id: str
    bcs_bot_id: str
    name: str
    summary: str
    avatar_url: str


@dataclass(frozen=True)
class RepairFacts:
    """Committed profile and owner facts read in one bounded pass."""

    key: SyncKey
    profile: ProfileSnapshot
    owner_user_id: str


@dataclass(frozen=True)
class RepairItem:
    """Per-component outcome after the repair settles.

    ``status`` is one of the fixed per-item states: ``succeeded``,
    ``failed`` (the attempt was definitively rejected), ``unknown``
    (the remote outcome is unconfirmed — never equal to "untouched"),
    ``skipped_dependency`` (a prerequisite item failed).

    ``error_code`` is ``None`` when the item has no error of its own —
    succeeded or a skipped/dependency case with nothing
    to attribute; failed/unknown items carry one of the finite kernel
    code families: a ``RepairItemErrorCode`` value for item-level
    conditions, a ``RepairFactsErrorCode`` for facts unavailable after
    execution began, or a delivery failure code. Managers missing-target
    delivery is normalized to ``collaboration_bot_not_found``.
    """

    component: Component
    status: ItemStatus
    error_code: str | None = None


@dataclass(frozen=True)
class RepairReport:
    """Terminal result of one repair command, exactly three items.

    ``status`` aggregates the items via :func:`report_status`; the
    report is always produced by the service as one item per component
    in the fixed order ownership, profile, managers.
    """

    bot_id: str
    status: ReportStatus
    items: tuple[RepairItem, ...]


class RepairFactsError(Exception):
    """The repair facts for one Bot could not be read as a complete set.

    ``code`` is a stable machine reason (``RepairFactsErrorCode``) —
    never an HTTP status, a raw driver error, or a fabricated default
    payload. Read-only like every member in this module.
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self._code = code

    @property
    def code(self) -> str:
        return self._code


class RepairPreconditionError(Exception):
    """A repair command cannot start with the given state.

    ``code`` is one of the finite ``RepairPreconditionErrorCode``
    values: the addressed Bot is missing on the delivery side
    (``collaboration_bot_not_found``), the sync runtime is unavailable,
    the Bot's owner changed so the command no longer names a stable
    target, or the repair deadline is already exhausted.
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self._code = code

    @property
    def code(self) -> str:
        return self._code


class MetadataDeliveryError(Exception):
    """BCS could not confirm one metadata delivery attempt.

    ``kind`` is the stable ``DeliveryFailure`` classification
    (``NOT_SENT`` / ``REJECTED`` / ``UNKNOWN``); ``code`` is one of the
    finite ``MetadataDeliveryErrorCode`` values, the sanitized public
    reason that may later appear on ``RepairItem.error_code``. Carries
    no response bodies. HTTP status mapping belongs to the adapter, not
    to the raise site.
    """

    def __init__(self, kind: DeliveryFailure, code: str) -> None:
        super().__init__(f"{kind.value} {code}")
        self._kind = kind
        self._code = code

    @property
    def kind(self) -> DeliveryFailure:
        return self._kind

    @property
    def code(self) -> str:
        return self._code


_BAD_ITEM_STATUSES: frozenset[str] = frozenset(
    {"failed", "unknown", "skipped_dependency"}
)


def report_status(items: tuple[RepairItem, ...]) -> ReportStatus:
    """Derive the whole-report status from per-item statuses.

    Bad items are ``failed``, ``unknown`` and ``skipped_dependency``.
    Any bad item plus at least one succeeded item is
    ``partial_failed``; bad items with no succeeded item is ``failed``;
    everything else is ``succeeded``. An empty report is a programming
    error (the service always produces the fixed three items), not a
    domain outcome.
    """
    if not items:
        raise ValueError(
            "report_status requires the service's fixed repair items; "
            "an empty report is not a valid domain outcome"
        )
    bad = any(item.status in _BAD_ITEM_STATUSES for item in items)
    if not bad:
        return "succeeded"
    if any(item.status == "succeeded" for item in items):
        return "partial_failed"
    return "failed"
