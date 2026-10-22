"""Durable dispatch and result ledger, separate from the future delivery outbox."""

from pathlib import Path
import json

from engine.community.kernel.dws_execution import (
    AgentInvocation,
    EngineUpdate,
    ExecutionRecord,
    HostingDecision,
    HostingReview,
)
from engine.community.kernel.dws_hosting import Turn
from engine.community.plugins.dws_hosting.database import HostingDatabase
from engine.community.plugins.dws_hosting.state import SqliteHostingStateStore as State
from engine.community.plugins.dws_hosting.dialogue import with_dialogue

TERMINAL = {"resolved", "manual", "cancelled"}


class SqliteHostingExecutionStore:
    def __init__(self, state_dir: Path):
        self._database = HostingDatabase(state_dir)

    def review_context(
        self, binding_id: str, turn_id: str
    ) -> tuple[dict[str, str], ...]:
        with self._database.transaction() as db:
            current = State._turn_row(db, binding_id, turn_id)
            # COSEC: only confirmed peer deliveries in the same owner/control scope.
            rows = db.execute(
                """SELECT b.snapshot_json, o.delivery_json FROM batches b
                JOIN outbox o ON o.turn_id = b.batch_id AND o.binding_id = b.binding_id
                WHERE b.binding_id = ? AND b.conversation_id = ?
                AND b.binding_epoch = ? AND b.conversation_epoch = ?
                AND b.first_ms < ? AND o.state = 'sent'
                AND json_extract(o.delivery_json, '$.purpose') = 'reply'
                ORDER BY b.first_ms DESC, b.rowid DESC LIMIT 4""",
                (
                    binding_id,
                    current["conversation_id"],
                    current["binding_epoch"],
                    current["conversation_epoch"],
                    current["first_ms"],
                ),
            ).fetchall()
            context = []
            for row in reversed(rows):
                turn = Turn.model_validate_json(row["snapshot_json"])
                context.append(
                    {
                        "role": "peer",
                        "text": "\n".join(message.text for message in turn.messages)[
                            :2000
                        ],
                    }
                )
                context.append(
                    {
                        "role": "assistant_sent",
                        "text": json.loads(row["delivery_json"])["text"][:2000],
                    }
                )
            return tuple(context)

    @staticmethod
    def _row(db, binding_id, turn_id):
        row = db.execute(
            "SELECT * FROM executions WHERE binding_id = ? AND turn_id = ?",
            (binding_id, turn_id),
        ).fetchone()
        if row is None:
            raise KeyError("execution not found")
        return row

    @staticmethod
    def _record(row) -> ExecutionRecord:
        return ExecutionRecord(
            turn_id=row["turn_id"],
            invocation=AgentInvocation.model_validate_json(row["invocation_json"]),
            state=row["state"],
            reason=row["reason"],
            result=(
                EngineUpdate.model_validate_json(row["result_json"])
                if row["result_json"]
                else None
            ),
            review=(
                HostingReview.model_validate_json(row["review_json"])
                if row["review_json"]
                else None
            ),
            decision=(
                HostingDecision.model_validate_json(row["decision_json"])
                if row["decision_json"]
                else None
            ),
        )

    @staticmethod
    def _current(db, binding_id, turn_id):
        batch = State._turn_row(db, binding_id, turn_id)
        binding = State._binding(db, binding_id)
        conversation = State._conversation(db, binding_id, batch["conversation_id"])
        return bool(
            batch["state"] in ("running", "unknown")
            and binding["enabled"]
            and not conversation["taken_over"]
            and batch["binding_epoch"] == binding["epoch"]
            and batch["conversation_epoch"] == conversation["epoch"]
        )

    def reserve(
        self, turn: Turn, invocation: AgentInvocation
    ) -> ExecutionRecord | None:
        binding_id, turn_id = turn.binding.binding_id, turn.turn_id
        with self._database.transaction() as db:
            batch = State._turn_row(db, binding_id, turn_id)
            if Turn.model_validate_json(batch["snapshot_json"]) != turn:
                raise ValueError("execution snapshot mismatch")
            if batch["state"] != "running" or not self._current(
                db, binding_id, turn_id
            ):
                return None
            checkpoint = db.execute(
                "SELECT ready, epoch FROM intake_checkpoints WHERE binding_id = ?",
                (binding_id,),
            ).fetchone()
            if checkpoint and (
                not checkpoint["ready"] or checkpoint["epoch"] != turn.binding_epoch
            ):
                return None
            if db.execute(
                """SELECT 1 FROM intake_hints WHERE binding_id = ? AND conversation_id = ?
                AND created_ms > (SELECT active_since FROM bindings WHERE binding_id = ?)""",
                (binding_id, turn.conversation_id, binding_id),
            ).fetchone():
                return None
            if db.execute(
                "SELECT 1 FROM executions WHERE turn_id=?", (turn_id,)
            ).fetchone():
                return None
            invocation = with_dialogue(db, turn, invocation)
            cursor = db.execute(
                """INSERT INTO executions(turn_id, binding_id, invocation_json, state)
                VALUES (?, ?, ?, 'dispatching') ON CONFLICT(turn_id) DO NOTHING""",
                (turn_id, binding_id, invocation.model_dump_json()),
            )
            return (
                self._record(self._row(db, binding_id, turn_id))
                if cursor.rowcount
                else None
            )

    def get(self, binding_id: str, turn_id: str) -> ExecutionRecord:
        with self._database.transaction() as db:
            return self._record(self._row(db, binding_id, turn_id))

    def record_update(
        self, binding_id: str, turn_id: str, update: EngineUpdate
    ) -> None:
        with self._database.transaction() as db:
            row = self._row(db, binding_id, turn_id)
            request = AgentInvocation.model_validate_json(row["invocation_json"])
            if update.run_id != request.run_id:
                raise ValueError("execution run mismatch")
            if row["state"] in TERMINAL or row["state"] == "received":
                return
            if update.status in ("prepared", "accepted"):
                if update.gateway_witness is not None:
                    if (
                        request.gateway_witness is not None
                        and request.gateway_witness != update.gateway_witness
                    ):
                        raise ValueError("gateway witness changed")
                    request = request.model_copy(
                        update={"gateway_witness": update.gateway_witness}
                    )
                    db.execute(
                        "UPDATE executions SET invocation_json = ? WHERE turn_id = ?",
                        (request.model_dump_json(), turn_id),
                    )
                if update.status == "prepared":
                    return
                # A late ACK cannot undo an uncertain execution state.
                db.execute(
                    "UPDATE executions SET state = 'accepted' WHERE turn_id = ? AND state = 'dispatching'",
                    (turn_id,),
                )
                return
            state = "unknown" if update.status == "unknown" else "received"
            db.execute(
                "UPDATE executions SET state = ?, result_json = ?, reason = ? WHERE turn_id = ?",
                (state, update.model_dump_json(), update.reason, turn_id),
            )
            if state == "unknown":
                db.execute(
                    "UPDATE batches SET state = 'unknown' WHERE batch_id = ? AND state = 'running'",
                    (turn_id,),
                )

    def mark_unknown(self, binding_id: str, turn_id: str, reason: str) -> None:
        with self._database.transaction() as db:
            row = self._row(db, binding_id, turn_id)
            if row["state"] not in ("dispatching", "accepted", "unknown"):
                return
            db.execute(
                "UPDATE executions SET state = 'unknown', reason = ? WHERE turn_id = ?",
                (reason, turn_id),
            )
            db.execute(
                "UPDATE batches SET state = 'unknown' WHERE batch_id = ? AND state = 'running'",
                (turn_id,),
            )

    def resolve(
        self,
        binding_id: str,
        turn_id: str,
        decision: HostingDecision,
        *,
        review: HostingReview | None = None,
    ) -> ExecutionRecord:
        with self._database.transaction() as db:
            row = self._row(db, binding_id, turn_id)
            if row["state"] in TERMINAL:
                return self._record(row)
            turn = Turn.model_validate_json(
                State._turn_row(db, binding_id, turn_id)["snapshot_json"]
            )
            if (
                row["state"] != "received"
                or self._record(row).result.status != "completed"
            ):
                raise ValueError("successful result required")
            if decision.rule_version != turn.rule_version:
                raise ValueError("execution rule mismatch")
            current = self._current(db, binding_id, turn_id)
            state = "resolved" if current else "cancelled"
            db.execute(
                "UPDATE executions SET state = ?, decision_json = ?, reason = ?, review_json = ? WHERE turn_id = ?",
                (
                    state,
                    decision.model_dump_json(exclude_unset=True),
                    "" if current else "control_revoked",
                    review.model_dump_json() if review is not None else None,
                    turn_id,
                ),
            )
            if not current or decision.action == "ignore":
                db.execute(
                    "UPDATE batches SET state = 'completed' WHERE batch_id = ?",
                    (turn_id,),
                )
            return self._record(self._row(db, binding_id, turn_id))

    def require_manual(
        self,
        binding_id: str,
        turn_id: str,
        reason: str,
        *,
        ended: bool,
        review: HostingReview | None = None,
    ) -> ExecutionRecord:
        with self._database.transaction() as db:
            row = self._row(db, binding_id, turn_id)
            if row["state"] in TERMINAL or (
                row["state"] == "received" and reason == "ended_result_unavailable"
            ):
                return self._record(row)
            state = "manual" if ended else "unknown"
            db.execute(
                "UPDATE executions SET state = ?, reason = ?, review_json = ? WHERE turn_id = ?",
                (
                    state,
                    reason,
                    review.model_dump_json() if review is not None else None,
                    turn_id,
                ),
            )
            db.execute(
                "UPDATE batches SET state = ? WHERE batch_id = ?",
                ("completed" if ended else "unknown", turn_id),
            )
            return self._record(self._row(db, binding_id, turn_id))
