"""AgentAttemptRepository — ac_agent_swarm_agent_attempt persistence
(D19 no-await-in-session).

The per-attempt executor ledger (D-K): one row per provisioning success,
keyed by (env, tenant, run, agent, claim epoch, slice retry index). Written by the
pool through the D28 claim fence (a stale slice's journal insert is
inert), read by the terminal destroy sweep, and marked DESTROYED per
ledger row as personas are cleaned.
"""
from __future__ import annotations

import time

from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError

from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.agentswarm.repository.agent_attempt import model as om
from agentclaw.community.core.agentswarm.repository.run_dispatch import model as om_dispatch
from agentclaw.community.log import get_logger

_logger = get_logger(__name__)
from agentclaw.community.core.agentswarm.repository.agent_attempt.protocol import (
    AgentAttemptRepositoryProtocol,
)
from agentclaw.community.core.agentswarm.repository.types import _now_ms
from agentclaw.community.plugin_api.database import DatabasePlugin

BINDING_STATE_SPAWNED = "SPAWNED"
BINDING_STATE_BOUND = "BOUND"
BINDING_STATE_DESTROYED = "DESTROYED"

# A transient MariaDB lock fault on the settle UPDATE is a retry, not a
# lost claim (live-proven: a 1213 deadlock was misread as "claim lost"
# and stamped the pod's own in-flight attempt FAILED/slice_lost):
# deadlock 1213 / lock-wait timeout 1205, plus the 1020 "Record has
# changed" lost-update signal — the retried UPDATE's DESTROYED predicate
# still turns a genuine destroy-sweep race into a rowcount-0 False.
_TRANSIENT_LOCK_CODES = frozenset({1213, 1205, 1020})
_TRANSIENT_LOCK_MARKERS = (
    "deadlock", "lock wait timeout", "record has changed",
)
# Bounded settle retry: the UPDATE is retried on a fresh session with a
# tiny backoff; only after these are exhausted does the settle report
# False (never an unbounded loop against a wedged lock chain).
_SETTLE_MAX_ATTEMPTS = 3


def _driver_error(exc: Exception) -> Exception:
    """The wrapped DBAPI error SQLAlchemy stashes on ``orig``, if any."""
    return getattr(exc, "orig", None) or exc


def _driver_error_code(exc: Exception) -> object | None:
    """Best-effort structured driver code for diagnostics — pymysql /
    mysqldb carry the MariaDB error code as ``orig.args[0]``."""
    args = getattr(_driver_error(exc), "args", None) or ()
    return args[0] if args else None


def _is_transient_lock_error(exc: Exception) -> bool:
    """True when the driver error is a bounded-retry lock fault rather
    than a real fault: a known MariaDB lock code (``orig.args[0]``) or,
    for drivers without a structured code, one of the message markers."""
    for arg in getattr(_driver_error(exc), "args", None) or ():
        if isinstance(arg, int) and arg in _TRANSIENT_LOCK_CODES:
            return True
    text = str(_driver_error(exc)).lower()
    return any(marker in text for marker in _TRANSIENT_LOCK_MARKERS)


class AgentAttemptRepository(AgentAttemptRepositoryProtocol):

    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def record_agent_attempt_intent(self, run_id: dm.RunId, tenant: dm.TenantId,
                                          agent_id: dm.AgentId, claim_epoch: int,
                                          slice_retry_index: int, executor_kind: str,
                                          executor_id: str,
                                          executor_name: str) -> bool:
        """Write-ahead intent insert: fenced and idempotent like
        every other ledger write.

        Landed BEFORE the attempt's ``setup()`` so a provisioning
        failure leaves a reconcilable row: bot-kind intents journal a
        blank executor_id (the persona id does not exist yet) and the
        terminal destroy sweep derives the with-attempt identity from
        the row's own correlation columns. The fence is the same
        dispatch-row CURRENT claim_epoch predicate as ``record_agent_binding``;
        a UK replay (crash-resume re-running the same attempt leg)
        converges to True instead of surfacing the race.
        """
        try:
            with self._db.orm_session() as session:
                claim_alive = session.execute(
                    select(om_dispatch.AcAgentSwarmRunDispatchModel.dispatch_id).where(
                        om_dispatch.AcAgentSwarmRunDispatchModel.run_id == run_id,
                        om_dispatch.AcAgentSwarmRunDispatchModel.tenant == tenant,
                        om_dispatch.AcAgentSwarmRunDispatchModel.claim_epoch == claim_epoch,
                        om_dispatch.AcAgentSwarmRunDispatchModel.is_deleted.is_(False),
                    )
                ).first()
                if claim_alive is None:
                    return False
                session.add(om.AcAgentSwarmAgentAttemptModel(
                    agent_id=agent_id,
                    run_id=run_id,
                    tenant=tenant,
                    claim_epoch=claim_epoch,
                    slice_retry_index=slice_retry_index,
                    executor_kind=executor_kind,
                    executor_id=executor_id,
                    executor_name=executor_name,
                    binding_state=BINDING_STATE_SPAWNED,
                    started_at=_now_ms(),
                ))
        except IntegrityError:
            return True
        return True

    def record_agent_binding(self, run_id: dm.RunId, tenant: dm.TenantId,
                               agent_id: dm.AgentId, claim_epoch: int,
                               slice_retry_index: int, executor_kind: str,
                               executor_id: str, executor_name: str) -> bool:
        """Fence-gated insert-or-settle (D28).

        Fresh UK: the insert is allowed only while the dispatch row's CURRENT
        claim_epoch matches the caller's — the same EXISTS predicate
        AgentRepository.update_status applies to its UPDATE, evaluated
        in the same session/transaction as the insert. A stale slice's
        write is inert (rowcount-gate returns False, nothing lands). A
        UK replay — the same (env, tenant, run, agent, epoch, slice_retry_index)
        row already landed — converges instead of surfacing the race
        (idempotent journal, requeue_orphan's convergence idiom): the
        replays are the write-ahead INTENT row (or a settle replay), so
        the convergence leg is the fenced settlement below — same row,
        provisioned persona, ``binding_state`` BOUND. A DESTROYED row
        is never resurrected; a fenced settlement returns False and the
        caller unwinds as a lost slice.
        """
        try:
            with self._db.orm_session() as session:
                claim_alive = session.execute(
                    select(om_dispatch.AcAgentSwarmRunDispatchModel.dispatch_id).where(
                        om_dispatch.AcAgentSwarmRunDispatchModel.run_id == run_id,
                        om_dispatch.AcAgentSwarmRunDispatchModel.tenant == tenant,
                        om_dispatch.AcAgentSwarmRunDispatchModel.claim_epoch == claim_epoch,
                        om_dispatch.AcAgentSwarmRunDispatchModel.is_deleted.is_(False),
                    )
                ).first()
                if claim_alive is None:
                    return False
                session.add(om.AcAgentSwarmAgentAttemptModel(
                    agent_id=agent_id,
                    run_id=run_id,
                    tenant=tenant,
                    claim_epoch=claim_epoch,
                    slice_retry_index=slice_retry_index,
                    executor_kind=executor_kind,
                    executor_id=executor_id,
                    executor_name=executor_name,
                    binding_state=BINDING_STATE_SPAWNED,
                    started_at=_now_ms(),
                ))
        except IntegrityError:
            # uk_agent_attempt replay: the write-ahead intent row or a
            # same-identity settle replay already landed — settle THAT row
            # under the same claim fence instead of surfacing the collision.
            return self._settle_binding_locked(
                run_id, tenant, agent_id, claim_epoch, slice_retry_index,
                executor_id, executor_name,
            )
        return True

    def _settle_binding_locked(self, run_id: dm.RunId, tenant: dm.TenantId,
                               agent_id: dm.AgentId, claim_epoch: int,
                               slice_retry_index: int, executor_id: str,
                               executor_name: str) -> bool:
        """Fenced UK settlement: SPAWNED intent -> BOUND with the real
        persona. False means the claim is gone or the row is DESTROYED —
        both leave the ledger untouched.

        A transient MariaDB lock fault on the settlement UPDATE (1213 /
        1205 / 1020) is retried on a fresh session up to
        ``_SETTLE_MAX_ATTEMPTS``: a deadlock is a contention artifact,
        not a lost claim, and returning False here stamps the caller's
        in-flight attempt FAILED as a poison pill. Rowcount 0 keeps the
        immediate False of the unfenced contract — including the
        destroy-sweep race, whose signal is semantic (the retried
        UPDATE's ``binding_state != DESTROYED`` predicate matches no
        row), not an exception to retry.
        """
        dispatch_epoch_current = (
            select(om_dispatch.AcAgentSwarmRunDispatchModel.dispatch_id).where(
                om_dispatch.AcAgentSwarmRunDispatchModel.run_id == run_id,
                om_dispatch.AcAgentSwarmRunDispatchModel.tenant == tenant,
                om_dispatch.AcAgentSwarmRunDispatchModel.claim_epoch == claim_epoch,
                om_dispatch.AcAgentSwarmRunDispatchModel.is_deleted.is_(False),
            ).exists()
        )
        for settle_attempt in range(1, _SETTLE_MAX_ATTEMPTS + 1):
            try:
                with self._db.orm_session() as session:
                    result = session.execute(
                        update(om.AcAgentSwarmAgentAttemptModel).where(
                            om.AcAgentSwarmAgentAttemptModel.run_id == run_id,
                            om.AcAgentSwarmAgentAttemptModel.tenant == tenant,
                            om.AcAgentSwarmAgentAttemptModel.agent_id == agent_id,
                            om.AcAgentSwarmAgentAttemptModel.claim_epoch == claim_epoch,
                            om.AcAgentSwarmAgentAttemptModel.slice_retry_index == slice_retry_index,
                            om.AcAgentSwarmAgentAttemptModel.binding_state
                            != BINDING_STATE_DESTROYED,
                            om.AcAgentSwarmAgentAttemptModel.is_deleted.is_(False),
                            dispatch_epoch_current,
                        ).values(
                            executor_id=executor_id,
                            executor_name=executor_name,
                            binding_state=BINDING_STATE_BOUND,
                        )
                    )
                    if result.rowcount == 0:
                        return False
                    return True
            except (OperationalError, DBAPIError) as exc:
                # The destroy sweep can mark this row DESTROYED between this
                # transaction's read and write — that race converges to False
                # through the rowcount gate above (its 1020 retry re-runs the
                # DESTROYED-rejecting UPDATE), never through this leg.
                if (settle_attempt < _SETTLE_MAX_ATTEMPTS
                        and _is_transient_lock_error(exc)):
                    _logger.warning(
                        "[agentswarm][attempt] settle hit a transient lock "
                        "fault, retrying run=%s caller_epoch=%s attempt=%s "
                        "try=%d/%d code=%r", run_id, claim_epoch, slice_retry_index,
                        settle_attempt, _SETTLE_MAX_ATTEMPTS,
                        _driver_error_code(exc),
                    )
                    time.sleep(0.05 * settle_attempt)
                    continue
                _logger.warning(
                    "[agentswarm][attempt] settle failed run=%s "
                    "caller_epoch=%s attempt=%s code=%r exc=%r",
                    run_id, claim_epoch, slice_retry_index, _driver_error_code(exc), exc,
                )
                return False
        return False

    def count_agent_provisionings(self, run_id: dm.RunId,
                                   tenant: dm.TenantId,
                                   agent_id: dm.AgentId) -> int:
        """Lifetime provisioning attempts this agent has already burned:
        one ledger row per (claim epoch, attempt) provisioning start, across
        every slice of the run. The revive path caps respawns on this count
        so a poison agent stops minting personas after the configured
        budget (the rows themselves are the DB-recorded retry history)."""
        with self._db.orm_session() as session:
            return session.execute(
                select(func.count()).select_from(
                    om.AcAgentSwarmAgentAttemptModel
                ).where(
                    om.AcAgentSwarmAgentAttemptModel.run_id == run_id,
                    om.AcAgentSwarmAgentAttemptModel.tenant == tenant,
                    om.AcAgentSwarmAgentAttemptModel.agent_id == agent_id,
                    om.AcAgentSwarmAgentAttemptModel.is_deleted.is_(False),
                )
            ).scalar_one()

    def list_agent_bindings(self, run_id: dm.RunId,
                              tenant: dm.TenantId) -> list[dict]:
        """Every live ledger row of the run, ascending insertion order.

        Rows are NOT pre-deduped by executor_id: a cross-slice replay of
        one attempt number (crash-resume adopt) journals a NEW row per
        claim epoch, so the same executor id legitimately appears twice —
        the destroy sweep dedupes delete calls itself while this list
        keeps both rows addressable (crash-orphan traceability, D-K).
        """
        with self._db.orm_session() as session:
            rows = session.execute(
                select(om.AcAgentSwarmAgentAttemptModel).where(
                    om.AcAgentSwarmAgentAttemptModel.run_id == run_id,
                    om.AcAgentSwarmAgentAttemptModel.tenant == tenant,
                    om.AcAgentSwarmAgentAttemptModel.is_deleted.is_(False),
                ).order_by(om.AcAgentSwarmAgentAttemptModel.id)
            ).scalars().all()
            return [
                {
                    "agent_id": r.agent_id,
                    "claim_epoch": r.claim_epoch,
                    "slice_retry_index": r.slice_retry_index,
                    "executor_kind": r.executor_kind,
                    "executor_id": r.executor_id,
                    "executor_name": r.executor_name,
                    "binding_state": r.binding_state,
                    "result_status": r.result_status,
                    "result_message": r.result_message,
                    "started_at": r.started_at,
                }
                for r in rows
            ]

    def record_attempt_result(self, run_id: dm.RunId, tenant: dm.TenantId,
                              agent_id: dm.AgentId, claim_epoch: int,
                              slice_retry_index: int, result_status: str,
                              result_message: str | None) -> int:
        """Record the attempt's own outcome, fenced by its claim epoch.

        The ledger's ``binding_state`` only tracks the provisioning
        lifecycle (SPAWNED/BOUND/DESTROYED), so a FAILED attempt looked
        like a SPAWNED one. This diagnostic UPDATE keys the attempt row on
        (run_id, tenant, agent_id, claim_epoch, slice_retry_index) under the same
        dispatch-row CURRENT claim_epoch fence as the settle, writes
        ``result_status``/``result_message``, and stamps ``ended_at``.
        Best-effort by contract: a fenced-out or DESTROYED row (rowcount 0)
        is returned to the caller, never raised — a lost diagnostic write
        must not fail a completion.
        """
        dispatch_epoch_current = (
            select(om_dispatch.AcAgentSwarmRunDispatchModel.dispatch_id).where(
                om_dispatch.AcAgentSwarmRunDispatchModel.run_id == run_id,
                om_dispatch.AcAgentSwarmRunDispatchModel.tenant == tenant,
                om_dispatch.AcAgentSwarmRunDispatchModel.claim_epoch == claim_epoch,
                om_dispatch.AcAgentSwarmRunDispatchModel.is_deleted.is_(False),
            ).exists()
        )
        with self._db.orm_session() as session:
            try:
                result = session.execute(
                    update(om.AcAgentSwarmAgentAttemptModel).where(
                        om.AcAgentSwarmAgentAttemptModel.run_id == run_id,
                        om.AcAgentSwarmAgentAttemptModel.tenant == tenant,
                        om.AcAgentSwarmAgentAttemptModel.agent_id == agent_id,
                        om.AcAgentSwarmAgentAttemptModel.claim_epoch == claim_epoch,
                        om.AcAgentSwarmAgentAttemptModel.slice_retry_index == slice_retry_index,
                        om.AcAgentSwarmAgentAttemptModel.binding_state
                        != BINDING_STATE_DESTROYED,
                        om.AcAgentSwarmAgentAttemptModel.is_deleted.is_(False),
                        dispatch_epoch_current,
                    ).values(
                        result_status=result_status,
                        result_message=result_message,
                        ended_at=_now_ms(),
                    )
                )
                return result.rowcount
            except (OperationalError, DBAPIError):
                return 0

    def mark_binding_destroyed(self, run_id: dm.RunId, tenant: dm.TenantId,
                               executor_id: str) -> int:
        """Mark every live row of one executor DESTROYED (per ledger row).

        executor_id-level addressing across the whole run: one persona id
        can sit on more than one row (the cross-slice replay case above),
        and the sweep marks each of them as its own record. Post-terminal
        by contract — no claim fence applies (the dispatch row is already
        complete); rows already marked stay untouched, so the count is
        the rows this call transitioned.
        """
        with self._db.orm_session() as session:
            result = session.execute(
                update(om.AcAgentSwarmAgentAttemptModel).where(
                    om.AcAgentSwarmAgentAttemptModel.run_id == run_id,
                    om.AcAgentSwarmAgentAttemptModel.tenant == tenant,
                    om.AcAgentSwarmAgentAttemptModel.executor_id == executor_id,
                    om.AcAgentSwarmAgentAttemptModel.binding_state
                    != BINDING_STATE_DESTROYED,
                    om.AcAgentSwarmAgentAttemptModel.is_deleted.is_(False),
                ).values(binding_state=BINDING_STATE_DESTROYED)
            )
            return result.rowcount

    def mark_intent_binding_destroyed(self, run_id: dm.RunId,
                                      tenant: dm.TenantId,
                                      agent_id: dm.AgentId, slice_retry_index: int) -> int:
        """Mark blank-id INTENT rows DESTROYED, by attempt correlation.

        Post-terminal like ``mark_binding_destroyed`` (no claim fence
        applies). Addresses exactly the rows the executor_id-level
        method cannot see: write-ahead intent rows whose ``setup()``
        never settled, i.e. ``executor_id`` still blank. The
        (run_id, agent_id, slice_retry_index) correlation is epoch-independent on
        purpose — a crash-resume replays the same attempt number under
        a new epoch, and every blank row of that attempt shares the
        one derived persona the sweep just destroyed.
        """
        with self._db.orm_session() as session:
            result = session.execute(
                update(om.AcAgentSwarmAgentAttemptModel).where(
                    om.AcAgentSwarmAgentAttemptModel.run_id == run_id,
                    om.AcAgentSwarmAgentAttemptModel.tenant == tenant,
                    om.AcAgentSwarmAgentAttemptModel.agent_id == agent_id,
                    om.AcAgentSwarmAgentAttemptModel.slice_retry_index == slice_retry_index,
                    om.AcAgentSwarmAgentAttemptModel.executor_id == "",
                    om.AcAgentSwarmAgentAttemptModel.binding_state
                    != BINDING_STATE_DESTROYED,
                    om.AcAgentSwarmAgentAttemptModel.is_deleted.is_(False),
                ).values(binding_state=BINDING_STATE_DESTROYED)
            )
            return result.rowcount