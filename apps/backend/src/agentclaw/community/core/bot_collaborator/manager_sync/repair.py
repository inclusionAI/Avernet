"""ManagerSyncRepairService — first backfill, targeted compensation, controlled recovery.

The Task 10 maintenance surface for Backend→BCS manager sync. This is
**not** a public HTTP endpoint and is never wired to one: it is resolved
from the DI container by the deployment's existing controlled Python /
maintenance environment only (see
``docs/bot-manager-sync-operations.md`` for the runbook).

What it exists for:

- **First backfill** — ``request_page`` walks every sync-eligible Bot in
  one explicit tenant/env scope in bounded pages of at most 100, and
  submits each key to the unified notifier with the REPAIR reason.
- **Targeted compensation** — the bounded dispatcher legitimately drops
  work (capacity, defer-window expiry, closed admission); re-submitting
  exactly those keys is the controlled remedy. A page never counts a
  refused submission as success: every key the notifier refused comes
  back precisely in ``RepairPage.rejected_keys`` so the operator cannot
  blind-advance the cursor and lose failed entries.
- **Controlled recovery** — ``clear_confirmed_attempt`` is the *only*
  code path besides the coordinator's own CAS that touches a marker: it
  demands a non-empty operator id and audit evidence *before* the
  coordinator is called, passes the exact ``attempt_id``,
  returns the coordinator's bool verbatim, and never hides a re-request
  behind a clear (the operator calls ``request_bot`` explicitly next).

Safety postures, each pinned by a test in ``test_repair.py``:

- **Submission is never confirmation.** Every count and every returned
  key speaks about admission only; whether a sync actually ran, and what
  it confirmed, is answered by the delivery round — never here.
- **Blocking is never bypassed.** All three entry points only delegate;
  the env gate, queue capacity and admission state of the notifier all
  apply to repair requests exactly as to business ones.
- **CAS verdicts are never fabricated.** A coordinator ``False`` means
  the marker stands — no notifier call, no side effects. A coordinator
  failure propagates: the operator must see the infrastructure error and
  can never mistake it for a confirmed clear.
- **Rejected keys are maintenance output only.** They never go into
  metric labels or logs; the page log line carries integer counts only.
- **Compensation reads the *current* database.** Repair projects nothing
  itself (``read_facts`` is never called here) and carries no roster:
  each page submits exactly what a fresh read returns, and the desired
  roster is rebuilt later from current facts by the sync round.

Thread discipline: repair runs in a maintenance context, so the
coordinator / reader calls are plain synchronous calls — there is no
event loop to protect, and their results and exceptions surface
verbatim to the operator.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from agentclaw.community.core.repository.protocols.bot.manager_sync import (
    ManagerSyncReadRepository,
)
from agentclaw.community.kernel.bot_manager_sync import (
    SubmissionResult,
    SyncKey,
    SyncReason,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.manager_sync_coordination import (
    ManagerSyncCoordinator,
)

logger = get_logger()

#: Hard page clamp for one repair sweep step. Callers may ask for less,
#: never for more — a maintenance sweep must not materialize the whole
#: tenant in one query. Mirrors the reader's own ``MAX_PAGE_LIMIT``.
REPAIR_PAGE_MAX_KEYS = 100


@dataclass(frozen=True)
class RepairPage:
    """One page of a repair sweep: submission bookkeeping, never sync
    confirmations.

    ``last_pk`` is the next sweep cursor (the last key's ``bot_pk``, or
    the untouched ``after_pk`` on an empty page — the reader's "no more
    keys" signal). ``rejected_keys`` carries exactly the keys the
    notifier refused (REJECTED or DISABLED alike): a caller may advance
    the cursor, but only after handling every refused key — nothing is
    ever silently dropped. The keys are maintenance output only; they
    must not end up in metric labels or high-cardinality logs.
    """

    last_pk: int
    queued: int
    coalesced: int
    rejected: int
    rejected_keys: tuple[SyncKey, ...]


@runtime_checkable
class _SyncNotifier(Protocol):
    """The structural slice of the manager-sync notifier repair needs.

    ``core`` must not import ``api`` (architecture rule — this package
    consumes the api Protocol's *implementations* via DI, like every
    other core consumer of manager sync), so the notifier contract is
    pinned structurally: anything that satisfies
    ``BotManagerSyncProtocol.request_sync`` satisfies this, both
    the real dispatcher and the disabled twin included.
    """

    def request_sync(
        self,
        *,
        bot_pk: int,
        env: str,
        avernet_tenant: str,
        reason: SyncReason,
    ) -> SubmissionResult:
        """Submit one Bot for manager sync (admission only)."""
        ...


class ManagerSyncRepairService:
    """Controlled maintenance for the manager sync projection."""

    def __init__(
        self,
        reader: ManagerSyncReadRepository,
        notifier: _SyncNotifier,
        coordinator: ManagerSyncCoordinator,
    ) -> None:
        self._reader = reader
        self._notifier = notifier
        self._coordinator = coordinator

    # -- targeted compensation ------------------------------------------

    def request_bot(self, key: SyncKey) -> SubmissionResult:
        """Delegate one Bot to the unified notifier with the REPAIR reason.

        The verdict is returned verbatim: QUEUED / COALESCED are
        *submissions* (never sync confirmations); REJECTED (capacity,
        closed admission, env gate) and DISABLED mean nothing was
        submitted and the key needs compensation — never counting as
        success. Repair never bypasses the notifier's blocking.
        """
        return self._notifier.request_sync(
            bot_pk=key.bot_pk,
            env=key.env,
            avernet_tenant=key.avernet_tenant,
            reason=SyncReason.REPAIR,
        )

    # -- first backfill ---------------------------------------------------

    def request_page(
        self,
        *,
        avernet_tenant: str,
        env: str,
        after_pk: int,
        limit: int,
    ) -> RepairPage:
        """Sweep one page of a tenant/env scope and submit every key.

        Reads the *current* sync-eligible keys through the reader with
        the same explicit tenant/env scoping (never the ambient request
        tenant), clamps ``limit`` to :data:`REPAIR_PAGE_MAX_KEYS`, and
        submits each key via :meth:`request_bot`. Refused keys are
        preserved in ``rejected_keys`` — advancing the cursor past them
        without handling them is the caller's error, and the page makes
        that impossible to do silently.
        """
        bounded = max(0, min(int(limit), REPAIR_PAGE_MAX_KEYS))
        keys = self._reader.page_keys(
            avernet_tenant=avernet_tenant,
            env=env,
            after_pk=after_pk,
            limit=bounded,
        )
        if not keys:
            return RepairPage(
                last_pk=after_pk, queued=0, coalesced=0, rejected=0,
                rejected_keys=(),
            )
        queued = 0
        coalesced = 0
        rejected_keys: list[SyncKey] = []
        for key in keys:
            result = self.request_bot(key)
            if result is SubmissionResult.QUEUED:
                queued += 1
            elif result is SubmissionResult.COALESCED:
                # Already queued elsewhere — nothing to redo, and not a
                # failure either.
                coalesced += 1
            else:
                # REJECTED and DISABLED alike: nothing was submitted for
                # this key, so it must survive precisely.
                rejected_keys.append(key)
        page = RepairPage(
            last_pk=keys[-1].bot_pk,
            queued=queued,
            coalesced=coalesced,
            rejected=len(rejected_keys),
            rejected_keys=tuple(rejected_keys),
        )
        # Integer counts only: keys are maintenance output and never log
        # payloads here.
        logger.info(
            "[MANAGER-SYNC-REPAIR] page tenant=%s env=%s after_pk=%s "
            "last_pk=%s queued=%d coalesced=%d rejected=%d",
            avernet_tenant,
            env,
            after_pk,
            page.last_pk,
            page.queued,
            page.coalesced,
            page.rejected,
        )
        return page

    # -- controlled recovery ----------------------------------------------

    def clear_confirmed_attempt(
        self,
        key: SyncKey,
        *,
        attempt_id: str,
        operator_id: str,
        evidence: str,
    ) -> bool:
        """CAS-clear exactly ``attempt_id``'s marker — controlled recovery.

        The maintenance caller must hold approval authority; this method
        cannot prove the evidence, only demand it: a non-empty
        ``operator_id`` and non-empty ``evidence`` are validated *before*
        the coordinator is touched (empty or whitespace-only raises
        ``ValueError`` — never a quiet pass-through).

        Returns the coordinator's bool VERBATIM. ``False`` — wrong or
        stale attempt id, or the marker is not this one: the marker
        stands and nothing else happens (no notifier call, no side
        effects). An infrastructure failure propagates: the operator
        must see it, and can never mistake it for a confirmed clear.
        The re-request afterwards is the operator's explicit
        ``request_bot`` (runbook §5) — never hidden behind a clear.
        """
        operator = (operator_id or "").strip()
        if not operator:
            raise ValueError(
                "clear_confirmed_attempt requires a non-empty operator_id "
                "(the traceable approver of this recovery)"
            )
        proof = (evidence or "").strip()
        if not proof:
            raise ValueError(
                "clear_confirmed_attempt requires non-empty audit evidence "
                "(what was verified, and from which server-side source)"
            )
        confirmed = self._coordinator.clear_confirmed(key, attempt_id=attempt_id)
        if not confirmed:
            logger.warning(
                "[MANAGER-SYNC-REPAIR] marker_clear_refused tenant=%s env=%s "
                "bot_pk=%s attempt_id=%s operator_id=%s — marker stands; "
                "verify the exact attempt id before retrying",
                key.avernet_tenant,
                key.env,
                key.bot_pk,
                attempt_id,
                operator,
            )
            return False
        # The audit record of the recovery: who cleared which exact
        # attempt, and on what evidence (operator-supplied free text —
        # never a credential and never a roster).
        logger.info(
            "[MANAGER-SYNC-REPAIR] marker_cleared tenant=%s env=%s bot_pk=%s "
            "attempt_id=%s operator_id=%s evidence=%s",
            key.avernet_tenant,
            key.env,
            key.bot_pk,
            attempt_id,
            operator,
            proof,
        )
        return True


class DisabledManagerSyncRepair(ManagerSyncRepairService):
    """Zero-resource twin for the off / unavailable posture.

    The composition root binds this when the feature is off or its
    stack is unavailable: there is no coordinator to CAS against and no
    notifier to submit through, so the sweep and the clear refuse
    loudly instead of fabricating a verdict — a sweep would page the
    whole tenant just to refuse every key, and a CAS answer without the
    CAS layer is a lie. ``request_bot`` answers DISABLED exactly like
    the disabled notifier does, so the default binding stays honest for
    single-key delegation too.
    """

    def __init__(self, reason: str = "disabled") -> None:
        # No super().__init__: this posture owns nothing at all. The
        # reason is a stable diagnostic word (never secret material).
        self._reason = reason

    def __repr__(self) -> str:  # pragma: no cover — debug aid only
        return f"<DisabledManagerSyncRepair reason={self._reason!r}>"

    def request_bot(self, key: SyncKey) -> SubmissionResult:
        return SubmissionResult.DISABLED

    def request_page(
        self,
        *,
        avernet_tenant: str,
        env: str,
        after_pk: int,
        limit: int,
    ) -> RepairPage:
        raise RuntimeError(
            f"manager sync repair is unavailable: {self._reason}"
        )

    def clear_confirmed_attempt(
        self,
        key: SyncKey,
        *,
        attempt_id: str,
        operator_id: str,
        evidence: str,
    ) -> bool:
        raise RuntimeError(
            f"manager sync repair is unavailable: {self._reason}"
        )


__all__ = [
    "DisabledManagerSyncRepair",
    "ManagerSyncRepairService",
    "REPAIR_PAGE_MAX_KEYS",
    "RepairPage",
]