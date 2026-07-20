"""Publish operation runner — the step engine for crash-safe BaaS mutations.

Every BaaS mutation in the publish pipeline goes through this runner as
``open (intent) -> acquire (workflow) -> finalize``. The intent row is persisted
BEFORE the BaaS call; the returned workflow id is persisted after; a crash-resume
reads the ledger and picks up at the first incomplete step. Approval is delegated
to BaaS server-side auto-approval, so there is no approve step — the progress poll
drives the workflow to terminal.

Adopt-by-query (the in-doubt window on an existing bot): when a re-run finds its
op still ``PENDING`` with a ``bot_uuid``, it lists the bot's BaaS workflows and
adopts the one that is *ours* — matching the operation's publish type, not already
claimed by any ledger row, and created after this operation began. "After this
operation began" is fenced by a **workflow-id high-water mark** snapshotted at the
first acquire (BaaS ids are monotonic and global, so this is immune to clock skew
between the BaaS DB and ours — a strictly better realization of the plan's
"intent-timestamp fence"). A single match is adopted; none means our issue never
landed (issue now); more than one is anomalous (we are the sole issuer + SVC-PUB-15
serializes) and fails loudly rather than guessing.

See specs/2026-07-15-publish-service-idempotency/plan.md.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional

from agentclaw.community.core.service_bot.repository.models import (
    PublishOperationKind,
    PublishOperationRecord,
    PublishOperationState,
)
from agentclaw.community.core.service_bot.repository.publish_operation_repository import (
    PublishOperationRepository,
)
from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()

_BASELINE_KEY = "_baseline_workflow_id"


def to_baas_request_id(readable: str) -> str:
    """Fold a readable correlation string into BaaS's ``request_id`` contract.

    BaaS validates ``request_id`` against ``^[A-Za-z0-9_-]{32,64}$`` on its strict
    endpoints (scale / update-devices / restart), so anything we send it must
    comply. Invalid characters (e.g. ``.``) become ``_``; an id shorter than 32 is
    padded with a deterministic md5 tail; the result is capped at 64. Deterministic
    — the same input always yields the same id — so it stays a stable correlation
    token across re-runs, and the readable prefix keeps it greppable.
    """
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", readable)
    if len(safe) < 32:
        safe = f"{safe}_{hashlib.md5(readable.encode()).hexdigest()}"
    return safe[:64]


def operation_request_id(
    publish_id: int,
    operation_kind: PublishOperationKind,
    stage: str,
    attempt: int,
) -> str:
    """Deterministic, correlation-only request id for a logical operation.

    Readable base ``pub_{publish_id}_{kind}[_{stage}]_a{attempt}`` (an empty
    ``stage`` is omitted), folded through :func:`to_baas_request_id` so it satisfies
    BaaS's ``request_id`` contract. Stable across re-runs of the same operation and
    distinct across different operations/attempts, so a BaaS log line traces back
    to the exact ledger step that issued it. BaaS treats it as an opaque string —
    it is never a dedup/idempotency key (verified: request_id is correlation-only
    server-side).
    """
    parts = [f"pub_{publish_id}", str(operation_kind)]
    if stage:
        parts.append(stage)
    parts.append(f"a{attempt}")
    return to_baas_request_id("_".join(parts))


class PublishOperationError(Exception):
    """A publish operation step failed (surfaced to the caller / task)."""


class PublishOperationRunner:
    """Runs a BaaS mutation as resumable ledger steps.

    Crash-safety is exercised by the crash-window tests, which interrupt the real
    seams (the ``issue`` callable, ``record_workflow``, follow-up steps) rather
    than any production hook.
    """

    def __init__(
        self,
        *,
        ledger: PublishOperationRepository,
        baas_service: Any,
    ) -> None:
        self._ledger = ledger
        self._baas = baas_service

    # ── open (find-or-create the intent) ─────────────────────────────────
    def open_operation(
        self,
        *,
        publish_id: int,
        kind: PublishOperationKind,
        stage: PublishStage,
        params: Optional[Dict[str, Any]] = None,
        bot_uuid: Optional[str] = None,
        operator: str = "system",
    ) -> PublishOperationRecord:
        """Return the intent row for this logical operation, creating it if none
        is in flight.

        ``bot_uuid`` is ``None`` for a creation kind (first release / eval), whose
        target bot does not exist yet — it is persisted as ``NULL`` and filled in
        once the create returns.

        Two cases, keyed on the latest row for ``(publish_id, kind, stage)``:

        * the latest is **non-terminal** → a crash-resume of the *same* in-flight
          operation; that row is returned as-is so ``acquire_workflow`` picks up
          where it left off (adopt-by-query or issue-once).
        * the latest is **terminal** (or absent) → the previous same-kind
          operation already finished, so this call is a *genuinely new* invocation
          of that operation (e.g. the user scales again, or a rebuild reissues
          after ABANDONED). A fresh attempt (``latest.attempt + 1``) is opened.

        Redelivery caveat: opening a new attempt on a terminal COMPLETED latest is
        what lets a genuinely new invocation re-run — but the durable task queue is
        at-least-once, so a task that already drove its op to COMPLETED and then
        crashed *before the queue recorded that task as done* is redelivered. On
        that redelivery this method sees the COMPLETED latest, opens a fresh
        attempt, and re-issues the BaaS mutation. Operations that are not otherwise
        status-gated must guard that window themselves (e.g. the offline destroy
        short-circuits when the binding is already RELEASED).
        """
        latest = self._ledger.get_latest_by_kind(publish_id, kind, stage.value)
        if latest is not None and latest.state not in {
            s.value for s in PublishOperationState.terminal()
        }:
            return latest

        attempt = (latest.attempt + 1) if latest is not None else 1
        data: Dict[str, Any] = {
            "publish_id": publish_id,
            "operation_kind": str(kind),
            "stage": stage.value,
            "attempt": attempt,
            "request_id": operation_request_id(publish_id, kind, stage.value, attempt),
            "bot_uuid": bot_uuid,
            "operator": operator,
            "params": params,
            "env": get_current_env(),
        }
        return self._ledger.insert(data)

    # ── acquire (get the workflow id: memory / adopt / issue) ────────────
    async def acquire_workflow(
        self,
        op: PublishOperationRecord,
        issue: Callable[[], Awaitable[Dict[str, Any]]],
    ) -> PublishOperationRecord:
        """Ensure ``op`` has a recorded BaaS workflow id, then return the row.

        ``issue`` performs the actual BaaS mutation and returns its result dict
        (must carry ``publish_id``; a creation also carries ``bot_uuid``). It is
        called at most once per real issuance — never when the id is already
        recorded or successfully adopted.
        """
        if op.baas_publish_id is not None:
            return op

        if op.bot_uuid:
            op, adopted = self._resolve_existing_bot(op)
            if adopted is not None:
                return adopted

        result = await issue()

        baas_publish_id = result.get("publish_id")
        new_bot_uuid = result.get("bot_uuid")
        if not baas_publish_id:
            raise PublishOperationError(
                f"BaaS did not return publish_id for op={op.id} kind={op.operation_kind}"
            )
        recorded = self._ledger.record_workflow(
            op.id,
            baas_publish_id=int(baas_publish_id),
            bot_uuid=new_bot_uuid or None,
        )
        return recorded or self._ledger.get_by_id(op.id)

    def _resolve_existing_bot(
        self, op: PublishOperationRecord
    ) -> tuple[PublishOperationRecord, Optional[PublishOperationRecord]]:
        """Existing-bot in-doubt resolution. Returns ``(op, adopted_or_None)``.

        On the very first acquire (no baseline recorded) there is nothing to
        adopt — we snapshot the bot's current max workflow id as the fence and
        fall through to issue. On a resume (baseline present) we difference the
        bot's workflows and adopt the single one that is ours.
        """
        baseline = (op.result or {}).get(_BASELINE_KEY)
        if baseline is None:
            workflows = self._baas.list_bot_publishes(op.bot_uuid)
            baseline = max((int(w["id"]) for w in workflows), default=0)
            op = self._persist_baseline(op, baseline)
            return op, None

        adopted = self._try_adopt(op, int(baseline))
        return op, adopted

    def _try_adopt(
        self, op: PublishOperationRecord, baseline: int
    ) -> Optional[PublishOperationRecord]:
        workflows = self._baas.list_bot_publishes(op.bot_uuid)
        if not workflows:
            return None
        known_ids = {
            o.baas_publish_id
            for o in self._ledger.list_by_bot(op.bot_uuid, op.env)
            if o.baas_publish_id is not None
        }
        expected_types = PublishOperationKind(op.operation_kind).baas_publish_types
        candidates: List[Dict[str, Any]] = [
            w
            for w in workflows
            if int(w["id"]) > baseline
            and int(w["id"]) not in known_ids
            and w.get("publish_type") in expected_types
        ]
        if not candidates:
            return None
        if len(candidates) > 1:
            ids = sorted(int(w["id"]) for w in candidates)
            self._ledger.fail(
                op.id, f"adopt ambiguous: >1 unclaimed workflow {ids}"
            )
            raise PublishOperationError(
                f"adopt-by-query ambiguous for op={op.id} bot={op.bot_uuid}: {ids}"
            )
        workflow_id = int(candidates[0]["id"])
        logger.info(
            "[PublishOperationRunner] adopted in-doubt workflow %s for op=%s bot=%s",
            workflow_id, op.id, op.bot_uuid,
        )
        recorded = self._ledger.record_workflow(op.id, baas_publish_id=workflow_id)
        return recorded or self._ledger.get_by_id(op.id)

    def _persist_baseline(
        self, op: PublishOperationRecord, baseline: int
    ) -> PublishOperationRecord:
        merged = dict(op.result or {})
        merged[_BASELINE_KEY] = baseline
        updated = self._ledger.update_result(op.id, merged)
        return updated or op

    # ── finalize ─────────────────────────────────────────────────────────
    def record_step_result(
        self, op: PublishOperationRecord, values: Dict[str, Any]
    ) -> PublishOperationRecord:
        """Merge ``values`` into the op's ``result`` (read-modify-write). Used to
        record follow-up step outputs (binding id, draft id, puid) so a re-run
        skips them."""
        merged = dict(op.result or {})
        merged.update(values)
        updated = self._ledger.update_result(op.id, merged)
        return updated or op

    def complete_operation(
        self, op: PublishOperationRecord
    ) -> Optional[PublishOperationRecord]:
        return self._ledger.complete(op.id)

    def fail_operation(
        self, op: PublishOperationRecord, error: str
    ) -> Optional[PublishOperationRecord]:
        return self._ledger.fail(op.id, error)

    def abandon_operation(
        self, op: PublishOperationRecord, reason: str
    ) -> Optional[PublishOperationRecord]:
        return self._ledger.abandon(op.id, reason)
