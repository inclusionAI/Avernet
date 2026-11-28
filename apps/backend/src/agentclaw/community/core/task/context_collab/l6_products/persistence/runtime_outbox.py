"""Scoped durable outbox claims/fences and tenant-wide candidate admission."""

from time import time
from uuid import uuid4
from sqlalchemy import or_, and_
from sqlalchemy.exc import IntegrityError

from ...errors import (
    ContextCollabVersionConflict as Conflict,
    ContextCollabValidationError as Invalid,
)
from ...l5_methods.multi_rollout_state import digest
from ..product_profile_service.snapshot_codec import canonical, load
from .rollout_models import ProductIntentRow as Intent, ProductCandidateSlotRow as Slot


class RuntimeOutbox:
    def _enqueue(self, session, snapshot, intents):
        seen = set()
        for intent in intents:
            intent = dict(intent)
            logical_key = None
            if snapshot.multi:
                c = snapshot.multi
                intent.setdefault("generation", c.generation)
                intent.setdefault("plan", c.plans[-1].digest)
                intent.setdefault("run_id", c.run_id)
                logical_key = digest(
                    {
                        "collet": snapshot.collet.id,
                        "revision": snapshot.revision,
                        **intent,
                    }
                )
                if logical_key in seen:
                    continue
                seen.add(logical_key)
                if (
                    self._query(session, Intent)
                    .filter(Intent.logical_key == logical_key)
                    .one_or_none()
                ):
                    continue
            session.add(
                Intent(
                    **self._axes(),
                    collet_id=snapshot.collet.id,
                    revision=snapshot.revision,
                    kind=intent["kind"],
                    payload_json=canonical(intent),
                    state="pending",
                    logical_key=logical_key
                )
            )

    def _slots(self, session, snapshot, acquire, release):
        if release:
            self._query(session, Slot).filter(
                Slot.collet_id == snapshot.collet.id, Slot.rollout_id.in_(release)
            ).delete(synchronize_session=False)
        if acquire:
            rid, quota = acquire
            existing = (
                self._query(session, Slot).filter(Slot.rollout_id == rid).one_or_none()
            )
            if existing:
                if existing.collet_id != snapshot.collet.id:
                    raise Invalid("slot belongs to another Collet")
                return
            occupied = {s.ordinal for s in self._query(session, Slot).all()}
            free = (
                next((n for n in range(quota) if n not in occupied), None)
                if len(occupied) < quota
                else None
            )
            if free is None:
                raise Conflict(
                    "tenant candidate quota is occupied (unknown runs retain slots)"
                )
            session.add(
                Slot(
                    **self._axes(),
                    ordinal=free,
                    collet_id=snapshot.collet.id,
                    rollout_id=rid
                )
            )
            try:
                session.flush()  # Unique ordinal is the cross-Collet arbitration point.
            except IntegrityError as exc:
                raise Conflict(
                    "concurrent tenant admission; retry without dispatch"
                ) from exc

    def pending_intents(self, limit=20):
        now = int(time())
        with self.db.orm_session() as session:
            rows = (
                self._query(session, Intent)
                .filter(
                    or_(
                        Intent.state == "pending",
                        and_(
                            Intent.state.in_(("claimed", "requested")),
                            Intent.lease_until <= now,
                        ),
                    )
                )
                .filter(
                    Intent.kind.in_(
                        (
                            "advance",
                            "advance-cand",
                            "control",
                            "select",
                            "adapt",
                            "notify",
                            "cancel",
                            "reconcile",
                        )
                    )
                )
                .order_by(Intent.id)
                .limit(limit)
                .all()
            )
            return [
                {
                    "id": r.id,
                    "collet_id": r.collet_id,
                    "revision": r.revision,
                    **load(r.payload_json),
                }
                for r in rows
            ]

    def claim_intent(self, identity):
        fence, now = uuid4().hex, int(time())
        with self.db.transactional_orm_session() as session:
            updated = (
                self._query(session, Intent)
                .filter(
                    Intent.id == identity,
                    or_(
                        Intent.state == "pending",
                        and_(
                            Intent.state.in_(("claimed", "requested")),
                            Intent.lease_until <= now,
                        ),
                    ),
                )
                .update(
                    {
                        Intent.state: "claimed",
                        Intent.claim_owner: fence,
                        Intent.lease_until: now + 300,
                    },
                    synchronize_session=False,
                )
            )
            return fence if updated == 1 else ""

    def finish_intent(self, identity, state, fence=""):
        if state not in {
            "pending",
            "done",
            "requested",
            "confirmed",
            "unknown",
            "superseded",
        }:
            raise Invalid("unknown intent result")
        if not fence:
            raise Conflict("intent completion requires the claim fence")
        with self.db.transactional_orm_session() as session:
            count = (
                self._query(session, Intent)
                .filter(
                    Intent.id == identity,
                    Intent.state == "claimed",
                    Intent.claim_owner == fence,
                )
                .update(
                    {
                        Intent.state: state,
                        Intent.lease_until: int(time())
                        + (5 if state == "requested" else 0),
                    },
                    synchronize_session=False,
                )
            )
            if count != 1:
                raise Conflict("intent lease was reclaimed")

    def due_collets(self, limit=20):
        # Explicit host sweep; bounded scan, no background thread or hidden timer.
        from .rollout_models import ProductRuntimeRow as Runtime
        from ..product_profile_service.snapshot_codec import decode_snapshot

        now = int(time())
        with self.db.orm_session() as session:
            rows = self._query(session, Runtime).order_by(Runtime.id).yield_per(100)
            result = []
            for row in rows:
                snapshot = decode_snapshot(row.snapshot_json)
                c = snapshot.multi
                if (
                    c
                    and c.phase not in {"SUCCEEDED", "FAILED", "STOPPED"}
                    and (
                        now >= c.deadline
                        or (c.waiting_until and now >= c.waiting_until)
                    )
                ):
                    result.append(row.id)
                    if len(result) >= limit:
                        break
            return result
