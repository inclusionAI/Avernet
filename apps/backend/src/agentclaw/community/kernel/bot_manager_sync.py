"""Bot manager sync — the fixed, transport-free type dictionary.

Shared vocabulary for the Backend→BCS bot manager sync feature. Every
layer exchanges these values: the api Service Protocol
(``api/bot_manager_sync.py``), the core projector and later services,
the read repository contracts, and the plugin Protocols
(``BcsManagersPlugin``, ``ManagerSyncCoordinator``). Names and shapes
below are a type dictionary frozen by the task plan — later tasks fill
in the implementations and must not rename or re-parameterize members.

Kernel discipline holds: stdlib only, no HTTP, no Redis, no database —
pure data plus the two snapshot errors, so every layer can import this
module without dragging infrastructure along (the labels of the
manager ids are part of the payload, but nothing here logs or exposes
them).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True)
class SyncKey:
    """Address of one Bot's sync target: tenant + environment + Bot PK."""

    avernet_tenant: str
    env: str
    bot_pk: int


@dataclass(frozen=True)
class CollaboratorFact:
    """One collaborator row as read from persistence.

    ``role`` is restricted to the explicit roles ``admin`` / ``member``;
    the projector rejects anything else rather than guessing.
    ``space_eligible`` is False for rows the Space no longer vouches
    for (e.g. membership revoked since the row was written).
    """

    user_id: str
    role: str
    space_eligible: bool


@dataclass(frozen=True)
class SyncFacts:
    """Everything needed to project a Bot's manager set, in one read.

    ``entity_id`` is the stable identity that survives an owner
    handoff; ``owner_id`` is the *current* owner and must never appear
    in the projected manager list.
    """

    key: SyncKey
    bot_id: str
    entity_id: str
    owner_id: str
    collaborators: tuple[CollaboratorFact, ...]


@dataclass(frozen=True)
class ManagerSnapshot:
    """The projected desired state of one Bot's BCS manager set.

    ``digest`` is the SHA-256 over canonical JSON of key, target,
    owner, and the full id list — it moves with the desired state so
    senders can dedupe identical pushes.
    """

    key: SyncKey
    bcs_bot_id: str
    owner_id: str
    manager_user_ids: tuple[str, ...]
    digest: str


@dataclass(frozen=True)
class SpaceChange:
    """A Space-side membership change that may touch many Bots.

    ``user_ids`` empty means "every Bot under ``space_refs`` that has an
    explicit collaborator" — the fan-out query interprets it, the DTO
    only carries it.
    """

    avernet_tenant: str
    env: str
    space_refs: tuple[str, ...]
    user_ids: tuple[str, ...]  # 空表示该 Space 下全部显式协作者 Bot


@dataclass(frozen=True)
class ManagerSetChange:
    """BCS-side confirmation of one applied full replace."""

    added: int
    removed: int
    total: int
    request_id: str


class SyncReason(StrEnum):
    """Why a sync is being requested — observability, not control flow."""

    COLLABORATOR_CHANGED = "collaborator_changed"
    OWNER_HANDOFF = "owner_handoff"
    SPACE_MEMBERSHIP = "space_membership"
    BOT_SPACE_CHANGED = "bot_space_changed"
    REPAIR = "repair"


class SubmissionResult(StrEnum):
    """What the sync entry point did with a request."""

    QUEUED = "queued"
    COALESCED = "coalesced"
    DISABLED = "disabled"
    REJECTED = "rejected"


class SyncOutcome(StrEnum):
    """Terminal outcome of one sync attempt, reported by the dispatcher."""

    CONFIRMED = "confirmed"
    ACKNOWLEDGED_EMPTY = "acknowledged_empty"
    DRIFT = "drift"
    FAILED = "failed"
    DEFERRED = "deferred"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"
    SKIPPED = "skipped"


class DeliveryFailure(StrEnum):
    """Stable classification when BCS could not confirm the delivery."""

    NOT_SENT = "not_sent"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class SnapshotUnavailable(Exception):
    """The facts behind a snapshot cannot be read.

    ``code`` is a stable machine reason — not found, ownership
    mismatch, missing stable identity, read error. It is read-only like
    every member here; nothing depends on HTTP or Redis.
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self._code = code

    @property
    def code(self) -> str:
        return self._code


class SnapshotTooLarge(Exception):
    """The projected manager set exceeds the 1,000-id cap."""