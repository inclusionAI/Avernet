"""Detach revoked executions without claiming an uncertain engine run has ended."""

from uuid import uuid4

from engine.community.kernel.dws_hosting import Turn


def retire_revoked(db, binding_id: str) -> None:
    rows = db.execute(
        """SELECT b.batch_id, b.snapshot_json, c.session_id, e.state AS execution_state
        FROM batches b JOIN bindings s ON s.binding_id = b.binding_id
        JOIN conversations c ON c.binding_id = b.binding_id AND c.conversation_id = b.conversation_id
        LEFT JOIN executions e ON e.turn_id = b.batch_id
        WHERE b.binding_id = ? AND b.state IN ('running','unknown')
        AND (s.enabled = 0 OR b.binding_epoch != s.epoch OR c.taken_over = 1 OR b.conversation_epoch != c.epoch)
        AND NOT EXISTS (SELECT 1 FROM outbox o WHERE o.turn_id = b.batch_id
            AND o.state NOT IN ('queued','claimed','cancelled'))""",
        (binding_id,),
    ).fetchall()
    for row in rows:
        turn = Turn.model_validate_json(row["snapshot_json"])
        # COSEC: revocation is not proof of engine completion. Never reuse the old
        # session while that run may still write to it; retain the ledger for audit.
        if row["execution_state"] in ("dispatching", "accepted", "unknown"):
            db.execute(
                """UPDATE conversations SET session_id = ? WHERE binding_id = ?
                AND conversation_id = ? AND session_id = ?""",
                (uuid4().hex, binding_id, turn.conversation_id, turn.session_id),
            )
        db.execute(
            """UPDATE executions SET state = 'cancelled', reason = 'control_revoked_run_detached'
            WHERE turn_id = ? AND state NOT IN ('resolved','manual','cancelled')""",
            (turn.turn_id,),
        )
        db.execute(
            "UPDATE batches SET state = 'cancelled' WHERE batch_id = ?", (turn.turn_id,)
        )
        db.execute(
            """UPDATE outbox SET state = 'cancelled', reason = 'control_revoked', lease_until_ms = 0
            WHERE turn_id = ? AND state IN ('queued','claimed')""",
            (turn.turn_id,),
        )
