"""Transactional delivery queue. Uncertain writes are never leased for resend."""

import unicodedata
from pathlib import Path
from uuid import uuid4

from engine.community.core.dws_hosting.owner_notice import owner_notice
from engine.community.kernel.dws_delivery import (
    Delivery,
    DeliveryPolicy,
    DeliveryReceipt,
    DeliveryRecord,
)
from engine.community.kernel.dws_hosting import Turn
from engine.community.plugins.dws_hosting.database import HostingDatabase
from engine.community.plugins.dws_hosting.executions import (
    SqliteHostingExecutionStore as Executions,
)
from engine.community.plugins.dws_hosting.state import SqliteHostingStateStore as State

FINAL = {"sent", "accepted", "manual", "cancelled"}


class SqliteHostingOutbox:
    def __init__(self, state_dir: Path, policy: DeliveryPolicy = DeliveryPolicy()):
        self._database, self.policy = HostingDatabase(state_dir), policy

    @staticmethod
    def _row(db, binding_id, delivery_id):
        row = db.execute(
            "SELECT * FROM outbox WHERE binding_id = ? AND delivery_id = ?",
            (binding_id, delivery_id),
        ).fetchone()
        if row is None:
            raise KeyError("delivery not found")
        return row

    @staticmethod
    def _record(row):
        return DeliveryRecord(
            delivery=Delivery.model_validate_json(row["delivery_json"]),
            **{
                name: row[name]
                for name in (
                    "state",
                    "claim_id",
                    "attempts",
                    "polls",
                    "due_ms",
                    "lease_until_ms",
                    "task_id",
                    "message_id",
                    "remote_conversation_id",
                    "reason",
                )
            },
        )

    @staticmethod
    def _allowed(db, binding_id, turn_id, now_ms):
        batch = State._turn_row(db, binding_id, turn_id)
        binding = State._binding(db, binding_id)
        conversation = State._conversation(db, binding_id, batch["conversation_id"])
        execution = Executions._record(Executions._row(db, binding_id, turn_id))
        context = execution.invocation.hosting_context
        turn = Turn.model_validate_json(batch["snapshot_json"])
        # COSEC: persisted, recipient-bound evidence is required even for old
        # queued replies or a model that ignores its supplied scope instructions.
        if context is None or not context.permits(turn, now_ms):
            return False
        return bool(
            binding["enabled"]
            and not conversation["taken_over"]
            and binding["epoch"] == batch["binding_epoch"]
            and conversation["epoch"] == batch["conversation_epoch"]
        )

    @staticmethod
    def _intake_ready(db, row):
        checkpoint = db.execute(
            "SELECT ready, epoch FROM intake_checkpoints WHERE binding_id = ?",
            (row["binding_id"],),
        ).fetchone()
        batch = State._turn_row(db, row["binding_id"], row["turn_id"])
        if db.execute(
            """SELECT 1 FROM intake_hints WHERE binding_id = ? AND conversation_id = ?
            AND created_ms > (SELECT active_since FROM bindings WHERE binding_id = ?)""",
            (row["binding_id"], batch["conversation_id"], row["binding_id"]),
        ).fetchone():
            return False
        return not checkpoint or (
            checkpoint["ready"] and checkpoint["epoch"] == batch["binding_epoch"]
        )

    @staticmethod
    def _finish(db, row, state, reason, now_ms):
        db.execute(
            "UPDATE outbox SET state = ?, reason = ?, updated_ms = ?, lease_until_ms = 0 WHERE delivery_id = ?",
            (state, reason, now_ms, row["delivery_id"]),
        )
        if state in FINAL:
            db.execute(
                "UPDATE batches SET state = 'completed' WHERE batch_id = ? AND state IN ('running','unknown')",
                (row["turn_id"],),
            )
        elif state == "unknown":
            db.execute(
                "UPDATE batches SET state = 'unknown' WHERE batch_id = ? AND state = 'running'",
                (row["turn_id"],),
            )

    def enqueue(
        self, binding_id: str, turn_id: str, *, now_ms: int, label_suffix: str = ""
    ) -> DeliveryRecord | None:
        State._check_time(now_ms)
        if (
            not isinstance(label_suffix, str)
            or len(label_suffix) > 80
            or any(
                unicodedata.category(c).startswith("C")
                or unicodedata.category(c) in {"Zl", "Zp"}
                for c in label_suffix
            )
        ):
            raise ValueError("invalid AI label suffix")
        with self._database.transaction() as db:
            existing = db.execute(
                "SELECT * FROM outbox WHERE binding_id = ? AND turn_id = ?",
                (binding_id, turn_id),
            ).fetchone()
            if existing is not None:
                return self._record(existing)
            execution = Executions._record(Executions._row(db, binding_id, turn_id))
            turn = Turn.model_validate_json(
                State._turn_row(db, binding_id, turn_id)["snapshot_json"]
            )
            if execution.state == "manual":
                purpose, body = "owner_notice", "托管处理未完成，请人工核对该会话。"
            elif execution.state == "resolved" and execution.decision is not None:
                if execution.decision.action == "ignore":
                    return None
                purpose = (
                    "reply" if execution.decision.action == "reply" else "owner_notice"
                )
                body = (
                    execution.decision.text
                    if purpose == "reply"
                    else execution.decision.summary
                )
            else:
                return None
            if purpose == "owner_notice":
                body = owner_notice(turn, execution, body)
            delivery = Delivery(
                delivery_id=uuid4().hex,
                turn_id=turn_id,
                binding=turn.binding,
                conversation_id=turn.conversation_id,
                purpose=purpose,
                # Peer replies use DWS's native AI badge, not a body prefix.
                text=body,
            )
            db.execute(
                """INSERT INTO outbox(delivery_id, turn_id, binding_id, delivery_json, state, due_ms, created_ms, updated_ms)
                       VALUES (?, ?, ?, ?, 'queued', ?, ?, ?)""",
                (
                    delivery.delivery_id,
                    turn_id,
                    binding_id,
                    delivery.model_dump_json(),
                    now_ms,
                    now_ms,
                    now_ms,
                ),
            )
            row = self._row(db, binding_id, delivery.delivery_id)
            if not self._allowed(db, binding_id, turn_id, now_ms):
                self._finish(db, row, "cancelled", "control_revoked", now_ms)
            return self._record(self._row(db, binding_id, delivery.delivery_id))

    def get(self, binding_id: str, delivery_id: str) -> DeliveryRecord:
        with self._database.transaction() as db:
            return self._record(self._row(db, binding_id, delivery_id))

    def sent_message_ids(self, binding_id: str, conversation_id: str) -> frozenset[str]:
        with self._database.transaction() as db:
            return frozenset(
                row[0]
                for row in db.execute(
                    """SELECT o.message_id FROM outbox o JOIN batches b ON b.batch_id = o.turn_id
                WHERE o.binding_id = ? AND b.conversation_id = ? AND o.state = 'sent'
                AND o.message_id != '' AND json_extract(o.delivery_json, '$.purpose') = 'reply'""",
                    (binding_id, conversation_id),
                )
            )

    def skip(self, claim: DeliveryRecord, *, now_ms: int) -> None:
        State._check_time(now_ms)
        with self._database.transaction() as db:
            row = self._row(
                db, claim.delivery.binding.binding_id, claim.delivery.delivery_id
            )
            if (
                row["state"] == "claimed"
                and row["claim_id"] == claim.claim_id
                and row["lease_until_ms"] > now_ms
            ):
                self._finish(db, row, "cancelled", "owner_replied", now_ms)

    def claim(self, binding_id: str, *, now_ms: int) -> DeliveryRecord | None:
        State._check_time(now_ms)
        with self._database.transaction() as db:
            State._binding(db, binding_id)
            # Expired preflight/read-only work is safe to reclaim; an expired write is not.
            db.execute(
                """UPDATE outbox SET state = 'queued', lease_until_ms = 0
                          WHERE binding_id = ? AND state = 'claimed' AND lease_until_ms <= ?""",
                (binding_id, now_ms),
            )
            db.execute(
                """UPDATE outbox SET state = 'awaiting', lease_until_ms = 0
                          WHERE binding_id = ? AND state = 'checking' AND lease_until_ms <= ?""",
                (binding_id, now_ms),
            )
            db.execute(
                """UPDATE outbox SET state = 'unknown', reason = 'write_interrupted', lease_until_ms = 0, updated_ms = ?
                          WHERE binding_id = ? AND state = 'sending' AND lease_until_ms <= ?""",
                (now_ms, binding_id, now_ms),
            )
            rows = db.execute(
                """SELECT * FROM outbox WHERE binding_id = ? AND state IN ('queued','awaiting')
                                 AND due_ms <= ? ORDER BY CASE state WHEN 'awaiting' THEN 0 ELSE 1 END,
                                 due_ms, created_ms, delivery_id LIMIT 32""",
                (binding_id, now_ms),
            ).fetchall()
            for row in rows:
                if row["state"] == "queued":
                    if not self._allowed(db, binding_id, row["turn_id"], now_ms):
                        self._finish(db, row, "cancelled", "control_revoked", now_ms)
                        continue
                    if row["attempts"] >= len(self.policy.retry_delays_ms) + 1:
                        self._finish(db, row, "manual", "preflight_exhausted", now_ms)
                        continue
                    if not self._intake_ready(db, row):
                        continue
                    db.execute(
                        "UPDATE outbox SET state = 'claimed', attempts = attempts + 1 WHERE delivery_id = ?",
                        (row["delivery_id"],),
                    )
                else:
                    if row["polls"] >= self.policy.max_polls:
                        self._finish(db, row, "unknown", "status_unconfirmed", now_ms)
                        continue
                    db.execute(
                        "UPDATE outbox SET state = 'checking', polls = polls + 1 WHERE delivery_id = ?",
                        (row["delivery_id"],),
                    )
                db.execute(
                    "UPDATE outbox SET claim_id = ?, lease_until_ms = ?, updated_ms = ? WHERE delivery_id = ?",
                    (
                        uuid4().hex,
                        now_ms + self.policy.lease_ms,
                        now_ms,
                        row["delivery_id"],
                    ),
                )
                return self._record(self._row(db, binding_id, row["delivery_id"]))
            return None

    def start_send(
        self, claim: DeliveryRecord, *, now_ms: int
    ) -> DeliveryRecord | None:
        State._check_time(now_ms)
        with self._database.transaction() as db:
            row = self._row(
                db, claim.delivery.binding.binding_id, claim.delivery.delivery_id
            )
            if (
                row["state"] != "claimed"
                or row["claim_id"] != claim.claim_id
                or row["lease_until_ms"] <= now_ms
            ):
                return None
            if not self._allowed(db, row["binding_id"], row["turn_id"], now_ms):
                self._finish(db, row, "cancelled", "control_revoked", now_ms)
                return None
            if not self._intake_ready(db, row):
                self._retry(db, row, now_ms)
                return None
            db.execute(
                "UPDATE outbox SET state = 'sending', lease_until_ms = ?, updated_ms = ? WHERE delivery_id = ?",
                (now_ms + self.policy.lease_ms, now_ms, row["delivery_id"]),
            )
            return self._record(self._row(db, row["binding_id"], row["delivery_id"]))

    def _retry(self, db, row, now_ms):
        if row["attempts"] > len(self.policy.retry_delays_ms):
            self._finish(db, row, "manual", "preflight_exhausted", now_ms)
            return
        delay = self.policy.retry_delays_ms[row["attempts"] - 1]
        # Stable per-attempt jitter; the absolute deadline is persisted, not recomputed at restart.
        delay += int(row["claim_id"][:8], 16) % (max(1, delay // 5))
        db.execute(
            """UPDATE outbox SET state = 'queued', due_ms = ?, lease_until_ms = 0,
                       reason = 'preflight_retry', updated_ms = ? WHERE delivery_id = ?""",
            (now_ms + delay, now_ms, row["delivery_id"]),
        )

    def preflight_failed(
        self, claim: DeliveryRecord, *, now_ms: int, retryable: bool
    ) -> None:
        State._check_time(now_ms)
        if type(retryable) is not bool:
            raise ValueError("retryable must be boolean")
        with self._database.transaction() as db:
            row = self._row(
                db, claim.delivery.binding.binding_id, claim.delivery.delivery_id
            )
            if row["state"] != "claimed" or row["claim_id"] != claim.claim_id:
                return
            if retryable:
                self._retry(db, row, now_ms)
            else:
                self._finish(db, row, "manual", "preflight_rejected", now_ms)

    def complete(
        self, claim: DeliveryRecord, receipt: DeliveryReceipt, *, now_ms: int
    ) -> None:
        State._check_time(now_ms)
        with self._database.transaction() as db:
            row = self._row(
                db, claim.delivery.binding.binding_id, claim.delivery.delivery_id
            )
            if (
                row["state"] not in ("sending", "checking", "unknown")
                or row["claim_id"] != claim.claim_id
            ):
                return
            delivery = Delivery.model_validate_json(row["delivery_json"])
            mismatch = (
                (
                    row["task_id"]
                    and receipt.task_id
                    and row["task_id"] != receipt.task_id
                )
                or (
                    receipt.conversation_id
                    and delivery.purpose == "reply"
                    and receipt.conversation_id != delivery.conversation_id
                )
                or (
                    row["remote_conversation_id"]
                    and receipt.conversation_id
                    and row["remote_conversation_id"] != receipt.conversation_id
                )
            )
            if mismatch:
                self._finish(db, row, "unknown", "receipt_scope_mismatch", now_ms)
                return
            task_id = receipt.task_id or row["task_id"]
            db.execute(
                "UPDATE outbox SET task_id = ?, message_id = ?, remote_conversation_id = ? WHERE delivery_id = ?",
                (
                    task_id,
                    receipt.message_id or row["message_id"],
                    receipt.conversation_id or row["remote_conversation_id"],
                    row["delivery_id"],
                ),
            )
            if receipt.status == "sent":
                self._finish(db, row, "sent", "", now_ms)
            elif receipt.status == "rejected":
                self._finish(db, row, "manual", "delivery_rejected", now_ms)
            elif receipt.status == "accepted" and not receipt.queryable:
                if delivery.purpose == "owner_notice":
                    self._finish(db, row, "accepted", "accepted_unverified", now_ms)
                else:
                    self._finish(db, row, "unknown", "receipt_not_queryable", now_ms)
            elif task_id and row["polls"] < self.policy.max_polls:
                db.execute(
                    "UPDATE outbox SET state = 'awaiting', due_ms = ?, lease_until_ms = 0, reason = 'awaiting_receipt', updated_ms = ? WHERE delivery_id = ?",
                    (now_ms + self.policy.poll_interval_ms, now_ms, row["delivery_id"]),
                )
            else:
                self._finish(db, row, "unknown", "delivery_unconfirmed", now_ms)
