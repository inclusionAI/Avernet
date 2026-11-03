"""SQLite runtime recovery and operator actions; never calls external services."""

import fcntl
import os
from contextlib import asynccontextmanager
from pathlib import Path

from engine.community.kernel.dws_hosting import Binding
from engine.community.kernel.dws_runtime import (
    HostingIssue,
    PendingTurn,
    Resolution,
    RuntimePolicy,
    RuntimeStatus,
)
from engine.community.plugins.dws_hosting.capacity import check_disk
from engine.community.plugins.dws_hosting.database import HostingDatabase
from engine.community.plugins.dws_hosting.state import SqliteHostingStateStore as State
from engine.community.plugins.dws_hosting.revocation import retire_revoked


class SqliteHostingRuntimeStore:
    def __init__(self, state_dir: Path, policy: RuntimePolicy = RuntimePolicy()):
        self.database = HostingDatabase(state_dir)
        self.policy = policy

    @asynccontextmanager
    async def exclusive(self):
        # COSEC: do not follow replaced lock paths; never steal a live worker's lease.
        path = self.database.path.parent / "runtime.lock"
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            HostingDatabase._check_private(path, directory=False)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("hosting_runtime_busy") from None
            yield
        finally:
            os.close(fd)

    @staticmethod
    def _binding(db, binding):
        row = State._binding(db, binding.binding_id)
        if Binding.model_validate_json(row["identity_json"]) != binding:
            raise ValueError("hosting_identity_mismatch")
        return row

    def status(self, binding: Binding) -> RuntimeStatus:
        with self.database.transaction() as db:
            row = self._binding(db, binding)
            key = (binding.binding_id,)
            checkpoint = db.execute(
                "SELECT ready, epoch FROM intake_checkpoints WHERE binding_id = ?", key
            ).fetchone()
            return RuntimeStatus(
                enabled=bool(row["enabled"]),
                intake_ready=bool(
                    checkpoint
                    and checkpoint["ready"]
                    and checkpoint["epoch"] == row["epoch"]
                ),
                pending_batches=db.execute(
                    "SELECT COUNT(*) FROM batches WHERE binding_id = ? AND state IN ('collecting','ready','running','unknown')",
                    key,
                ).fetchone()[0],
                unknown_executions=db.execute(
                    "SELECT COUNT(*) FROM executions WHERE binding_id = ? AND state = 'unknown'",
                    key,
                ).fetchone()[0],
                unknown_deliveries=db.execute(
                    "SELECT COUNT(*) FROM outbox WHERE binding_id = ? AND state = 'unknown'",
                    key,
                ).fetchone()[0],
            )

    def prepare_restart(self, binding: Binding, *, now_ms: int) -> None:
        State._check_time(now_ms)
        with self.database.transaction() as db:
            self._binding(db, binding)
            key = (binding.binding_id,)
            db.execute(
                "UPDATE executions SET state = 'unknown', reason = 'runtime_interrupted' WHERE binding_id = ? AND state IN ('dispatching','accepted')",
                key,
            )
            db.execute(
                "UPDATE batches SET state = 'unknown' WHERE binding_id = ? AND state = 'running' AND batch_id IN (SELECT turn_id FROM executions WHERE state = 'unknown')",
                key,
            )
            db.execute(
                "UPDATE outbox SET state = 'unknown', claim_id = '', lease_until_ms = 0, updated_ms = ?, reason = 'runtime_interrupted' WHERE binding_id = ? AND state = 'sending'",
                (now_ms, binding.binding_id),
            )
            db.execute(
                "UPDATE outbox SET state = 'queued', claim_id = '', lease_until_ms = 0 WHERE binding_id = ? AND state = 'claimed'",
                key,
            )
            db.execute(
                "UPDATE outbox SET state = 'awaiting', claim_id = '', lease_until_ms = 0 WHERE binding_id = ? AND state = 'checking'",
                key,
            )
            self._cancel_unreserved(db, binding)

    def issues(
        self, binding: Binding, *, limit: int, offset: int
    ) -> tuple[HostingIssue, ...]:
        if (
            type(limit) is not int
            or not 1 <= limit <= 100
            or type(offset) is not int
            or offset < 0
        ):
            raise ValueError("invalid issue pagination")
        with self.database.transaction() as db:
            self._binding(db, binding)
            rows = db.execute(
                """SELECT 'execution' AS kind, e.turn_id AS target_id, e.turn_id,
                b.conversation_id, e.state FROM executions e JOIN batches b ON b.batch_id = e.turn_id
                WHERE e.binding_id = ? AND e.state IN ('unknown','manual')
                UNION ALL SELECT 'delivery', o.delivery_id, o.turn_id, b.conversation_id, o.state
                FROM outbox o JOIN batches b ON b.batch_id = o.turn_id
                WHERE o.binding_id = ? AND o.state IN ('unknown','manual') AND o.reason != 'owner_handled'
                ORDER BY kind, target_id LIMIT ? OFFSET ?""",
                (binding.binding_id, binding.binding_id, limit, offset),
            ).fetchall()
            return tuple(HostingIssue(**dict(row)) for row in rows)

    @staticmethod
    def _cancel_unreserved(db, binding):
        retire_revoked(db, binding.binding_id)
        # Safe both at restart and after a control change during a running cycle.
        db.execute(
            """UPDATE batches SET state = 'cancelled' WHERE binding_id = ?
                AND state = 'running' AND NOT EXISTS (SELECT 1 FROM executions e WHERE e.turn_id = batches.batch_id)
                AND (binding_epoch != (SELECT epoch FROM bindings WHERE binding_id = batches.binding_id)
                OR EXISTS (SELECT 1 FROM conversations c WHERE c.binding_id = batches.binding_id
                AND c.conversation_id = batches.conversation_id AND (c.taken_over = 1 OR c.epoch != batches.conversation_epoch)))""",
            (binding.binding_id,),
        )

    def pending(self, binding: Binding, *, limit: int) -> tuple[PendingTurn, ...]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid pending limit")
        with self.database.transaction() as db:
            self._binding(db, binding)
            self._cancel_unreserved(db, binding)
            rows = db.execute(
                """SELECT b.batch_id, e.turn_id FROM batches b
                LEFT JOIN executions e ON e.turn_id = b.batch_id
                LEFT JOIN outbox o ON o.turn_id = b.batch_id
                WHERE b.binding_id = ? AND o.turn_id IS NULL AND
                ((b.state = 'running' AND e.turn_id IS NULL) OR e.state = 'received'
                OR e.state = 'manual' OR (e.state = 'resolved' AND json_extract(e.decision_json, '$.action') != 'ignore'))
                ORDER BY b.first_ms, b.rowid LIMIT ?""",
                (binding.binding_id, limit),
            ).fetchall()
            return tuple(
                PendingTurn(turn_id=r[0], has_execution=r[1] is not None) for r in rows
            )

    def maintain(self, binding: Binding, *, now_ms: int) -> None:
        State._check_time(now_ms)
        with self.database.transaction() as db:
            self._binding(db, binding)
            retire_revoked(db, binding.binding_id)
            # Only confirmed terminal work; unknown/manual records remain until acknowledged.
            rows = db.execute(
                """SELECT b.batch_id FROM batches b LEFT JOIN executions e ON e.turn_id = b.batch_id
                LEFT JOIN outbox o ON o.turn_id = b.batch_id WHERE b.binding_id = ?
                AND b.state IN ('completed','cancelled') AND b.first_ms < ?
                AND (e.turn_id IS NULL OR e.state = 'cancelled' OR
                     (e.state = 'resolved' AND json_extract(e.decision_json, '$.action') = 'ignore')
                     OR o.state IN ('sent','accepted','cancelled'))
                AND (o.delivery_id IS NULL OR o.updated_ms < ?) LIMIT 100""",
                (
                    binding.binding_id,
                    now_ms - self.policy.retention_ms,
                    now_ms - self.policy.retention_ms,
                ),
            ).fetchall()
            for row in rows:
                key = (row[0],)
                db.execute(
                    "UPDATE inbox SET message_json = '', batch_id = NULL WHERE batch_id = ?",
                    key,
                )
                db.execute("DELETE FROM outbox WHERE turn_id = ?", key)
                db.execute("DELETE FROM executions WHERE turn_id = ?", key)
                db.execute("DELETE FROM batches WHERE batch_id = ?", key)
            db.execute(
                """UPDATE inbox SET message_json = '' WHERE rowid IN
                (SELECT rowid FROM inbox WHERE binding_id = ? AND batch_id IS NULL
                AND received_ms < ? AND message_json != '' LIMIT 100)""",
                (binding.binding_id, now_ms - self.policy.retention_ms),
            )
        # Keep compact deduplication IDs indefinitely; never revive old messages.
        check_disk(self.database.path, self.policy)

    def resolve_manual(
        self, binding: Binding, resolution: Resolution, *, actor_id: str, now_ms: int
    ) -> None:
        State._check_time(now_ms)
        # COSEC: operations consume the authenticated actor, never an LLM-provided owner.
        if actor_id != binding.owner_id:
            raise PermissionError("owner required")
        if not resolution.evidence.strip():
            raise ValueError("resolution evidence required")
        with self.database.transaction() as db:
            self._binding(db, binding)
            key = (binding.binding_id, resolution.kind, resolution.target_id)
            if db.execute(
                "SELECT 1 FROM hosting_resolutions WHERE binding_id = ? AND kind = ? AND target_id = ?",
                key,
            ).fetchone():
                return
            if resolution.kind == "execution":
                row = db.execute(
                    "SELECT turn_id, state FROM executions WHERE binding_id = ? AND turn_id = ?",
                    (binding.binding_id, resolution.target_id),
                ).fetchone()
            else:
                row = db.execute(
                    "SELECT turn_id, state FROM outbox WHERE binding_id = ? AND delivery_id = ?",
                    (binding.binding_id, resolution.target_id),
                ).fetchone()
            if row is None or row["state"] not in ("unknown", "manual"):
                raise ValueError("unresolved record required")
            turn = State._turn_row(db, binding.binding_id, row["turn_id"])
            # Taking responsibility is NOT evidence of successful delivery; no auto-resend.
            db.execute(
                "UPDATE conversations SET taken_over = 1, epoch = epoch + 1, active_since = ? WHERE binding_id = ? AND conversation_id = ?",
                (now_ms, binding.binding_id, turn["conversation_id"]),
            )
            db.execute(
                "UPDATE batches SET state = 'cancelled' WHERE binding_id = ? AND conversation_id = ? AND state IN ('collecting','ready')",
                (binding.binding_id, turn["conversation_id"]),
            )
            db.execute(
                "UPDATE batches SET state = 'completed' WHERE batch_id = ?",
                (row["turn_id"],),
            )
            if resolution.kind == "execution":
                db.execute(
                    "UPDATE executions SET state = 'cancelled', reason = 'owner_handled' WHERE turn_id = ?",
                    (row["turn_id"],),
                )
            else:
                db.execute(
                    "UPDATE outbox SET state = 'manual', reason = 'owner_handled', claim_id = '', lease_until_ms = 0, updated_ms = ? WHERE delivery_id = ?",
                    (now_ms, resolution.target_id),
                )
            db.execute(
                "INSERT INTO hosting_resolutions VALUES (?, ?, ?, ?, ?, ?)",
                (*key, actor_id, resolution.evidence, now_ms),
            )
