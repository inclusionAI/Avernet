"""One-shot repair of committed Bot facts, with three bounded report items.

Execution order and failure/deadline semantics are defined in this module's
README. Facts reads run in worker threads; timed-out results are discarded
before any later send. Only classified facts/delivery failures become report
items; unclassified errors and cancellation propagate.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from agentclaw.community.core.bot_collaborator.manager_sync.projector import (
    build_snapshot as build_manager_snapshot,
)
from agentclaw.community.core.bot_management.collaboration_repair import (
    service_protocol,
)
from agentclaw.community.core.bot_management.ownership_sync.projector import (
    build_snapshot as build_ownership_snapshot,
)
from agentclaw.community.core.repository.protocols.bot.collaboration_repair import (
    CollaborationRepairReadRepository,
)
from agentclaw.community.core.repository.protocols.bot.manager_sync import (
    ManagerSyncReadRepository,
)
from agentclaw.community.core.repository.protocols.bot.ownership_sync import (
    OwnershipSyncReadRepository,
)
from agentclaw.community.kernel.bot_manager_sync import (
    BOT_NOT_FOUND_CODE,
    DeliveryFailure,
    SnapshotTooLarge,
    SnapshotUnavailable,
    SyncKey,
)
from agentclaw.community.kernel.collaboration_repair import (
    COMPONENT_ORDER,
    Component,
    ItemStatus,
    MetadataDeliveryErrorCode,
    ProfileSnapshot,
    RepairCommand,
    RepairFacts,
    RepairFactsError,
    RepairFactsErrorCode,
    RepairItem,
    RepairItemErrorCode,
    RepairReport,
    RepairPreconditionError,
    RepairPreconditionErrorCode,
    report_status,
)
from agentclaw.community.kernel.bot_ownership_sync import OwnershipSnapshot
from agentclaw.community.plugin_api.bcs_collaboration_metadata import (
    BcsCollaborationMetadataPlugin,
    MetadataDeliveryError,
)
from agentclaw.community.plugin_api.bcs_managers import (
    BcsManagersError,
    BcsManagersPlugin,
)
from agentclaw.community.plugin_api.bcs_ownership import (
    BcsOwnershipError,
    BcsOwnershipPlugin,
)

Clock = Callable[[], float]

T = TypeVar("T")


def remaining(deadline: float, clock: Clock) -> float:
    """The seconds left before ``deadline``; an exhausted budget raises.

    The single deadline-arithmetic helper during execution: every reader,
    probe and send asks for its budget here, so the injected ``clock`` has
    exactly one arithmetic consumer plus the entry computation in
    :meth:`CollaborationRepairService.repair`.
    """
    seconds = deadline - clock()
    if seconds <= 0:
        raise RepairPreconditionError(
            RepairPreconditionErrorCode.REPAIR_DEADLINE_EXCEEDED
        )
    return seconds


def _status_for_kind(kind: DeliveryFailure) -> ItemStatus:
    """NOT_SENT/REJECTED are definitive refusals; UNKNOWN is unconfirmed."""
    return "failed" if kind is not DeliveryFailure.UNKNOWN else "unknown"


def _probe_precondition(error: MetadataDeliveryError) -> RepairPreconditionError:
    """An exists-phase delivery failure cannot become a per-item outcome.

    The probe runs before any item, so its failures are start preconditions:
    the unavailable family (and anything else BCS could not answer) is
    ``collaboration_sync_unavailable``; ``delivery_timeout`` maps to the
    deadline family so the API layer can answer with the 504 family.

    ``exists`` must return ``False`` for absence and raise only for
    unavailability — the composition adapter and the local recording fake
    both honor that today — which is why no code here is read as
    "absent": an unanswered probe is never a fabricated absence.
    """
    if error.code == MetadataDeliveryErrorCode.DELIVERY_TIMEOUT:
        return RepairPreconditionError(
            RepairPreconditionErrorCode.REPAIR_DEADLINE_EXCEEDED
        )
    return RepairPreconditionError(
        RepairPreconditionErrorCode.COLLABORATION_SYNC_UNAVAILABLE
    )


def _facts_error(error: SnapshotUnavailable) -> RepairFactsError:
    """Map a sync reader's stable vocabulary onto the repair facts family.

    ``BOT_NOT_FOUND_CODE`` — the kernel-declared reason both sync readers
    raise for a vanished or out-of-scope Bot — keeps the not-found meaning
    every other layer maps to the same response; every other reader
    refusal (read error, unavailable Space, missing identity part) is a
    plain facts-read error. Nothing here string-parses provider payloads;
    the readers' codes are their frozen contract.
    """
    if error.code == BOT_NOT_FOUND_CODE:
        return RepairFactsError(RepairFactsErrorCode.COLLABORATION_BOT_NOT_FOUND)
    return RepairFactsError(RepairFactsErrorCode.COLLABORATION_FACTS_READ_ERROR)


async def _read_mapped(
    reader: Callable[[SyncKey], T], key: SyncKey, deadline: float, clock: Clock
) -> T:
    """Read one facts set on a worker thread, mapping typed refusals.

    The synchronous, tenant-scoped reader session opens and closes on the
    worker thread — no network I/O ever observes it. Its stable
    ``SnapshotUnavailable`` refusals are translated into the repair facts
    error family above, and an oversized roster (``SnapshotTooLarge``,
    raised by the manager reader instead of truncating) is the same
    "these facts cannot be read" outcome — never an empty roster.
    Anything else (raw driver errors included) propagates untouched: a
    failed read is never a default payload.

    The await uses the remaining request budget. Cancellation cannot kill
    the worker thread, but its eventual result is discarded and cannot
    trigger any delivery after the request has returned.
    """
    budget = remaining(deadline, clock)
    read_timeout = asyncio.timeout(budget)
    try:
        async with read_timeout:
            return await asyncio.to_thread(reader, key)
    except TimeoutError as exc:
        if not read_timeout.expired():
            # A reader's unclassified timeout is not our deadline expiring.
            raise
        raise RepairPreconditionError(
            RepairPreconditionErrorCode.REPAIR_DEADLINE_EXCEEDED
        ) from exc
    except SnapshotUnavailable as exc:
        raise _facts_error(exc) from exc
    except SnapshotTooLarge:
        raise RepairFactsError(
            RepairFactsErrorCode.COLLABORATION_FACTS_READ_ERROR
        ) from None


class CollaborationRepairService(
    service_protocol.CollaborationRepairServiceProtocol
):
    """Repair one Bot's collaboration metadata from fresh committed facts.

    All dependencies arrive through the constructor — three read-only
    repositories and three delivery plugins — so the service stays
    transport-agnostic and never reads the environment. ``timeout_seconds``
    bounds the WHOLE request; ``clock`` is the injectable monotonic source
    (``time.monotonic`` in the composition root) and drives no other
    arithmetic.
    """

    def __init__(
        self,
        reader: CollaborationRepairReadRepository,
        ownership_reader: OwnershipSyncReadRepository,
        managers_reader: ManagerSyncReadRepository,
        metadata: BcsCollaborationMetadataPlugin,
        ownership: BcsOwnershipPlugin,
        managers: BcsManagersPlugin,
        *,
        timeout_seconds: float,
        clock: Clock,
    ) -> None:
        self._reader = reader
        self._ownership_reader = ownership_reader
        self._managers_reader = managers_reader
        self._metadata = metadata
        self._ownership = ownership
        self._managers = managers
        self._timeout_seconds = timeout_seconds
        self._clock = clock

    async def repair(self, command: RepairCommand) -> RepairReport:
        """Run the three bounded items against current committed facts."""
        deadline = self._clock() + self._timeout_seconds
        remaining(deadline, self._clock)

        facts = await _read_mapped(
            self._reader.read_facts, command.key, deadline, self._clock
        )
        self._require_scope(facts.key, command.key)
        stable = facts.profile.bcs_bot_id
        # The unified-owner authorization result as a fact-drift check:
        # the actor must still BE the owner the facts just named. It is
        # one precondition, not a second permission rule, and it fires
        # before any outbound traffic (the exists probe included).
        if command.actor_user_id != facts.owner_user_id:
            raise RepairPreconditionError(RepairPreconditionErrorCode.OWNER_CHANGED)
        await self._require_target(stable, deadline)

        ownership_item, snapshot = await self._repair_ownership(
            command.key, stable, deadline
        )
        if ownership_item.status != "succeeded" or snapshot is None:
            return self._report(
                facts,
                ownership_item,
                self._skipped("profile"),
                self._skipped("managers"),
            )

        profile_item = await self._repair_profile(facts.profile, deadline)
        if self._target_gone(profile_item) or self._deadline_dead(profile_item):
            return self._report(
                facts,
                ownership_item,
                profile_item,
                self._skipped("managers"),
            )

        try:
            managers_item = await self._repair_managers(
                command.key, stable, snapshot.owner_user_id, deadline
            )
        except (RepairFactsError, RepairPreconditionError) as exc:
            # Ownership/profile already ran. Preserve their outcomes when
            # fresh facts are unreadable; do not return a precondition envelope
            # that hides completed writes or continue with an unverified owner.
            code = (
                RepairItemErrorCode.DELIVERY_TIMEOUT
                if exc.code == RepairPreconditionErrorCode.REPAIR_DEADLINE_EXCEEDED
                else exc.code
            )
            return self._report(
                facts, ownership_item, profile_item,
                RepairItem("managers", "failed", code),
            )
        return self._report(
            facts, ownership_item, profile_item, managers_item
        )

    # ------------------------------------------------------------------
    # Preconditions (before any item)
    # ------------------------------------------------------------------

    async def _require_target(self, stable: str, deadline: float) -> None:
        """One existence probe; absence or unanswered probes refuse to start.

        The probe carries its own remaining budget (shared with the outer
        backstop await); False, a classified delivery failure or an expired
        budget raise the matching precondition, never a fabricated absence
        and never a per-item unknown.
        """
        budget = remaining(deadline, self._clock)
        try:
            present = await asyncio.wait_for(
                self._metadata.exists(stable, timeout_seconds=budget),
                timeout=budget,
            )
        except TimeoutError as exc:
            raise RepairPreconditionError(
                RepairPreconditionErrorCode.REPAIR_DEADLINE_EXCEEDED
            ) from exc
        except MetadataDeliveryError as exc:
            raise _probe_precondition(exc) from exc
        if not present:
            raise RepairPreconditionError(
                RepairPreconditionErrorCode.COLLABORATION_BOT_NOT_FOUND
            )

    # ------------------------------------------------------------------
    # Item steps (each at most one bounded send)
    # ------------------------------------------------------------------

    async def _repair_ownership(
        self, key: SyncKey, stable: str, deadline: float
    ) -> tuple[RepairItem, OwnershipSnapshot | None]:
        """Fresh-read ownership facts, one write, the return interpreted.

        Returns the item plus the snapshot actually written (the owner the
        managers drift check compares against). The missing no-op, a
        foreign confirmation and an owner mismatch are never success, and
        each stops the rest of the request.

        Fresh committed facts are authoritative for the OWNER too: when the
        Bot was handed off between the initial facts read and this fresh
        read, the request writes the fresh owner and continues. The
        actor-vs-owner precondition already refused a command whose actor
        is no longer the owner, so adopting the fresh owner here is
        carrying out the authorized repair, not re-authorizing it.
        """
        try:
            facts = await _read_mapped(
                self._ownership_reader.read_facts, key, deadline, self._clock
            )
        except RepairPreconditionError:
            # No ownership send started: a facts-read timeout is definitive.
            return RepairItem(
                "ownership", "failed", RepairItemErrorCode.DELIVERY_TIMEOUT
            ), None
        self._require_scope(facts.key, key)
        try:
            # The background ownership projector's contract (identity parts
            # validated, no Owner fallback). It cannot raise
            # ``SnapshotTooLarge`` — that cap belongs to the manager
            # projection — hence the narrower arm here.
            snapshot = build_ownership_snapshot(facts)
        except (SnapshotUnavailable, ValueError):
            raise RepairFactsError(
                RepairFactsErrorCode.COLLABORATION_FACTS_READ_ERROR
            ) from None
        if snapshot.bcs_bot_id != stable:
            raise RepairFactsError(
                RepairFactsErrorCode.COLLABORATION_BOT_IDENTITY_UNRESOLVED
            )

        item, change = await self._attempt(
            "ownership",
            lambda budget: self._ownership.replace_ownership(
                snapshot, timeout_seconds=budget
            ),
            deadline,
        )
        if item.status != "succeeded":
            return item, None
        if change.bot_id != stable:
            item = RepairItem(
                "ownership", "unknown",
                MetadataDeliveryErrorCode.COLLABORATION_RESPONSE_MALFORMED,
            )
        elif change.owner_user_id is None:
            item = RepairItem(
                "ownership",
                "failed",
                MetadataDeliveryErrorCode.COLLABORATION_BOT_NOT_FOUND,
            )
        elif change.owner_user_id != snapshot.owner_user_id:
            item = RepairItem(
                "ownership", "failed", RepairItemErrorCode.OWNER_DRIFT
            )
        return item, snapshot if item.status == "succeeded" else None

    async def _repair_profile(
        self, profile: ProfileSnapshot, deadline: float
    ) -> RepairItem:
        """One bounded onboard send of the frozen profile snapshot."""
        item, _ = await self._attempt(
            "profile",
            lambda budget: self._metadata.update_profile(
                profile, timeout_seconds=budget
            ),
            deadline,
        )
        return item

    async def _repair_managers(
        self,
        key: SyncKey,
        stable: str,
        written_owner: str,
        deadline: float,
    ) -> RepairItem:
        """Fresh manager facts through the real projector, one write.

        The stable address and the owner are re-validated on every read; a
        roster read failure raises a facts error — an empty roster is a
        state BCS confirmed, never a fallback for facts we could not read.
        Owner drift found here freezes the managers item instead of
        writing a stale roster.
        """
        facts = await _read_mapped(
            self._managers_reader.read_facts, key, deadline, self._clock
        )
        self._require_scope(facts.key, key)
        try:
            snapshot = build_manager_snapshot(facts)
        except (SnapshotUnavailable, ValueError, SnapshotTooLarge):
            raise RepairFactsError(
                RepairFactsErrorCode.COLLABORATION_FACTS_READ_ERROR
            ) from None
        # Identity first, exactly as the ownership step orders it: facts
        # describing another Bot are an unresolvable identity, not a benign
        # owner change on our target.
        if snapshot.bcs_bot_id != stable:
            raise RepairFactsError(
                RepairFactsErrorCode.COLLABORATION_BOT_IDENTITY_UNRESOLVED
            )
        # The drift comparison. ``ManagerSnapshot.owner_id`` and the
        # written ``OwnershipSnapshot.owner_user_id`` are both the same
        # stored ``ac_bots.owner_id`` column; the two facts families spell
        # it differently (``SyncFacts.owner_id`` / ``OwnershipFacts.owner_id``
        # in the DTOs, ``owner_user_id`` at the repair level), so compare
        # the projected snapshots instead of re-deriving the field.
        if snapshot.owner_id != written_owner:
            return RepairItem(
                "managers", "skipped_dependency", RepairItemErrorCode.OWNER_DRIFT
            )
        item, _ = await self._attempt(
            "managers",
            lambda budget: self._managers.replace_managers(
                snapshot, timeout_seconds=budget
            ),
            deadline,
        )
        return item


    # ------------------------------------------------------------------
    # One bounded send: classification only, no retry, no queue
    # ------------------------------------------------------------------

    async def _attempt(
        self,
        component: Component,
        send: Callable[[float], Awaitable[T]],
        deadline: float,
    ) -> tuple[RepairItem, T | None]:
        """Run one item send under the remaining budget and classify it.

        The pre-send budget check and the plugin call share one
        ``remaining`` value, and the outer ``asyncio.wait_for`` uses the
        same budget as a backstop against plugin misbehavior. Only the
        classified families and the deadline precondition are caught here;
        anything else — unclassified exceptions, Backend database errors,
        cancellation — propagates unreported.
        """
        try:
            budget = remaining(deadline, self._clock)
            call = send(budget)
            result = await asyncio.wait_for(call, timeout=budget)
        except RepairPreconditionError as exc:
            # Only ``remaining`` (deadline exhausted before this send —
            # the attempt definitively never started) or a metadata
            # adapter's gate refusal reach here; both are a failure of
            # THIS item, never a fabricated success.
            if exc.code == RepairPreconditionErrorCode.REPAIR_DEADLINE_EXCEEDED:
                return (
                    RepairItem(
                        component, "failed", RepairItemErrorCode.DELIVERY_TIMEOUT
                    ),
                    None,
                )
            return RepairItem(component, "failed", exc.code), None
        except TimeoutError:
            # The send was in flight when the budget expired; the remote
            # outcome is unconfirmed — never "failed", never "untouched".
            return (
                RepairItem(
                    component, "unknown", RepairItemErrorCode.DELIVERY_TIMEOUT
                ),
                None,
            )
        except (BcsOwnershipError, BcsManagersError, MetadataDeliveryError) as exc:
            kind: DeliveryFailure = exc.kind
            if isinstance(exc, BcsManagersError) and exc.code == BOT_NOT_FOUND_CODE:
                return RepairItem(
                    component, _status_for_kind(kind),
                    MetadataDeliveryErrorCode.COLLABORATION_BOT_NOT_FOUND,
                ), None
            return RepairItem(component, _status_for_kind(kind), exc.code), None
        return RepairItem(component, "succeeded"), result

    # ------------------------------------------------------------------
    # Facts helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _require_scope(read_key: SyncKey, command_key: SyncKey) -> None:
        """A reader answering outside the requested scope is unusable."""
        if read_key != command_key:
            raise RepairFactsError(
                RepairFactsErrorCode.COLLABORATION_FACTS_READ_ERROR
            )

    # ------------------------------------------------------------------
    # Report assembly
    # ------------------------------------------------------------------

    @staticmethod
    def _skipped(component: Component) -> RepairItem:
        """A tail item stopped by an earlier failure: nothing of its own."""
        return RepairItem(component, "skipped_dependency")

    @staticmethod
    def _target_gone(item: RepairItem) -> bool:
        """A definitive vanished-target failure freezes the tail."""
        return item.status == "failed" and item.error_code == (
            MetadataDeliveryErrorCode.COLLABORATION_BOT_NOT_FOUND
        )

    @staticmethod
    def _deadline_dead(item: RepairItem) -> bool:
        """Does this item carry the request's exhausted deadline?

        Both ``delivery_timeout`` carries — the failed item that never
        sent, and the unknown item whose send lost the race with the
        budget — mean the whole request's deadline is spent, so later
        applicable items become skipped dependencies instead of fresh
        sends against a dead budget. The delivered ``delivery_timeout``
        of a metadata send shares that meaning: the single-pass metadata
        transport runs on the caller's remaining budget, so its timeout
        is the request's budget exhaustion.
        """
        return item.error_code == RepairItemErrorCode.DELIVERY_TIMEOUT

    @staticmethod
    def _report(
        facts: RepairFacts,
        ownership: RepairItem,
        profile: RepairItem,
        managers: RepairItem,
    ) -> RepairReport:
        """Exactly one item per component, in the kernel's fixed order."""
        items = (ownership, profile, managers)
        for item, expected in zip(items, COMPONENT_ORDER, strict=True):
            if item.component != expected:
                raise ValueError(
                    f"repair item {item.component!r} does not occupy the "
                    f"{expected!r} slot of the fixed report order"
                )
        return RepairReport(
            bot_id=facts.profile.bot_id,
            status=report_status(items),
            items=items,
        )
