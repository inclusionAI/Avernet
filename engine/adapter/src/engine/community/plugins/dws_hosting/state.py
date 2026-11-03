"""SQLite implementation of the hosting state Plugin API.

Atomic transitions deliberately include epoch checks: a separate read/check/write
would allow a worker to claim work concurrently with disable or takeover.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import uuid4

from engine.community.kernel.dws_hosting import (
    BatchPolicy,
    Binding,
    DirectMessage,
    IntakeResult,
    Turn,
    TurnOutcome,
)
from engine.community.plugins.dws_hosting.database import HostingDatabase
from engine.community.kernel.dws_runtime import RuntimePolicy
from engine.community.plugins.dws_hosting.capacity import check_intake
from engine.community.plugins.dws_hosting.revocation import retire_revoked


class SqliteHostingStateStore:
    """Explicitly constructed, per-Bot store. Never launches DWS or an engine."""

    def __init__(
        self,
        state_dir: Path,
        policy: BatchPolicy = BatchPolicy(),
        *,
        runtime_policy: RuntimePolicy = RuntimePolicy(),
    ) -> None:
        self._database = HostingDatabase(state_dir)
        self._policy = policy
        self._runtime_policy = runtime_policy

    def bind(self, binding: Binding) -> None:
        with self._database.transaction() as db:
            row = db.execute(
                "SELECT identity_json FROM bindings WHERE binding_id = ?",
                (binding.binding_id,),
            ).fetchone()
            if row is not None:
                if Binding.model_validate_json(row[0]) != binding:
                    raise ValueError("binding identity is immutable")
                return
            # COSEC: bind all event/identity data as parameters; never interpolate SQL.
            db.execute(
                """INSERT INTO bindings(binding_id, identity_json, account_id,
                   environment, organization_id, bot_id) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    binding.binding_id,
                    binding.model_dump_json(),
                    binding.account_id,
                    binding.environment,
                    binding.organization_id,
                    binding.bot_id,
                ),
            )

    def confirm_rule(self, binding_id: str, text: str) -> int:
        if not text.strip() or len(text) > 16_000:
            raise ValueError("confirmed rule must contain 1..16000 characters")
        with self._database.transaction() as db:
            binding = self._binding(db, binding_id)
            version = binding["rule_version"] + 1
            db.execute(
                "UPDATE bindings SET rule_version = ?, rule_text = ? WHERE binding_id = ?",
                (version, text, binding_id),
            )
        return version

    def set_enabled(self, binding_id: str, enabled: bool, *, now_ms: int) -> None:
        self._check_time(now_ms)
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        with self._database.transaction() as db:
            binding = self._binding(db, binding_id)
            if enabled and not binding["rule_version"]:
                raise ValueError("confirm a rule before enabling hosting")
            if bool(binding["enabled"]) == enabled:
                retire_revoked(db, binding_id)
                return
            db.execute(
                """UPDATE bindings SET enabled = ?, epoch = epoch + 1,
                   active_since = ? WHERE binding_id = ?""",
                (enabled, now_ms, binding_id),
            )
            db.execute(
                """UPDATE batches SET state = 'cancelled' WHERE binding_id = ?
                   AND state IN ('collecting', 'ready')""",
                (binding_id,),
            )
            retire_revoked(db, binding_id)

    def set_takeover(
        self,
        binding_id: str,
        conversation_id: str,
        taken_over: bool,
        *,
        now_ms: int,
    ) -> None:
        self._check_time(now_ms)
        if type(taken_over) is not bool:
            raise ValueError("taken_over must be a boolean")
        if not conversation_id or len(conversation_id) > 512:
            raise ValueError("invalid conversation ID")
        with self._database.transaction() as db:
            self._binding(db, binding_id)
            conversation = self._conversation(db, binding_id, conversation_id)
            if bool(conversation["taken_over"]) == taken_over:
                return
            db.execute(
                """UPDATE conversations SET taken_over = ?, epoch = epoch + 1,
                   active_since = ? WHERE binding_id = ? AND conversation_id = ?""",
                (taken_over, now_ms, binding_id, conversation_id),
            )
            db.execute(
                """UPDATE batches SET state = 'cancelled'
                   WHERE binding_id = ? AND conversation_id = ?
                   AND state IN ('collecting', 'ready')""",
                (binding_id, conversation_id),
            )
            retire_revoked(db, binding_id)

    def ingest(
        self,
        binding_id: str,
        message: DirectMessage,
        *,
        now_ms: int,
    ) -> IntakeResult:
        self._check_time(now_ms)
        with self._database.transaction() as db:
            binding = self._binding(db, binding_id)
            key = (binding_id, message.conversation_id, message.message_id)
            if db.execute(
                """SELECT 1 FROM inbox WHERE binding_id = ?
                   AND conversation_id = ? AND message_id = ?""",
                key,
            ).fetchone():
                return "duplicate"
            check_intake(db, self._database.path, self._runtime_policy)
            conversation = self._conversation(db, binding_id, message.conversation_id)
            identity = Binding.model_validate_json(binding["identity_json"])
            eligible = (
                binding["enabled"]
                and not conversation["taken_over"]
                and message.sender_id != identity.account_id
                and message.created_at_ms
                > max(
                    binding["active_since"],
                    conversation["active_since"],
                )
                and message.created_at_ms <= now_ms
                and len(message.text) <= self._policy.max_characters
            )
            batch_id = None
            if eligible:
                batch_id = self._append_batch(
                    db, binding, conversation, message, now_ms
                )
            db.execute(
                """INSERT INTO inbox(binding_id, conversation_id, message_id,
                   message_json, batch_id, received_ms) VALUES (?, ?, ?, ?, ?, ?)""",
                (*key, message.model_dump_json(), batch_id, now_ms),
            )
            return "queued" if eligible else "ignored"

    def claim_ready(self, binding_id: str, *, now_ms: int) -> Turn | None:
        self._check_time(now_ms)
        with self._database.transaction() as db:
            binding = self._binding(db, binding_id)
            if not binding["enabled"]:
                return None
            if db.execute(
                "SELECT 1 FROM intake_checkpoints WHERE binding_id = ? AND (ready = 0 OR epoch != ?)",
                (binding_id, binding["epoch"]),
            ).fetchone():
                return None
            db.execute(
                """UPDATE batches SET state = 'ready' WHERE binding_id = ?
                   AND state = 'collecting' AND deadline_ms <= ?""",
                (binding_id, now_ms),
            )
            batch = db.execute(
                """SELECT b.*, c.session_id FROM batches b JOIN conversations c
                   ON b.binding_id = c.binding_id AND b.conversation_id = c.conversation_id
                   WHERE b.binding_id = ? AND b.state = 'ready'
                   AND b.binding_epoch = ? AND b.conversation_epoch = c.epoch
                   AND NOT EXISTS (SELECT 1 FROM intake_hints h WHERE h.binding_id = b.binding_id
                       AND h.conversation_id = b.conversation_id AND h.created_ms > ?)
                   AND c.taken_over = 0 AND NOT EXISTS (
                       SELECT 1 FROM batches active WHERE active.binding_id = b.binding_id
                       AND active.conversation_id = b.conversation_id
                       AND active.state IN ('running', 'unknown')
                   ) ORDER BY b.first_ms, b.rowid LIMIT 1""",
                (binding_id, binding["epoch"], binding["active_since"]),
            ).fetchone()
            if batch is None:
                return None
            messages = [
                DirectMessage.model_validate_json(row[0])
                for row in db.execute(
                    "SELECT message_json FROM inbox WHERE batch_id = ? ORDER BY rowid",
                    (batch["batch_id"],),
                )
            ]
            # History pages may arrive out of order; retain deterministic event ordering.
            messages.sort(
                key=lambda message: (message.created_at_ms, message.message_id)
            )
            turn = Turn(
                turn_id=batch["batch_id"],
                binding=Binding.model_validate_json(binding["identity_json"]),
                conversation_id=batch["conversation_id"],
                session_id=batch["session_id"],
                binding_epoch=batch["binding_epoch"],
                conversation_epoch=batch["conversation_epoch"],
                rule_version=binding["rule_version"],
                rule_text=binding["rule_text"],
                messages=tuple(messages),
            )
            db.execute(
                "UPDATE batches SET state = 'running', snapshot_json = ? WHERE batch_id = ?",
                (turn.model_dump_json(), turn.turn_id),
            )
            return turn

    def get_turn(self, binding_id: str, turn_id: str) -> Turn:
        with self._database.transaction() as db:
            return Turn.model_validate_json(
                self._turn_row(db, binding_id, turn_id)["snapshot_json"]
            )

    def is_current(self, binding_id: str, turn_id: str) -> bool:
        with self._database.transaction() as db:
            batch = self._turn_row(db, binding_id, turn_id)
            binding = self._binding(db, binding_id)
            conversation = self._conversation(db, binding_id, batch["conversation_id"])
            return bool(
                batch["state"] == "running"
                and binding["enabled"]
                and not conversation["taken_over"]
                and batch["binding_epoch"] == binding["epoch"]
                and batch["conversation_epoch"] == conversation["epoch"]
            )

    def finish_turn(self, binding_id: str, turn_id: str, outcome: TurnOutcome) -> None:
        if outcome not in ("completed", "unknown"):
            raise ValueError("invalid turn outcome")
        with self._database.transaction() as db:
            turn = self._turn_row(db, binding_id, turn_id)
            if turn["state"] == outcome:
                return
            if turn["state"] == "completed":
                raise ValueError("completed turn cannot be reopened")
            db.execute(
                "UPDATE batches SET state = ? WHERE batch_id = ?", (outcome, turn_id)
            )

    def _append_batch(
        self,
        db: sqlite3.Connection,
        binding: sqlite3.Row,
        conversation: sqlite3.Row,
        message: DirectMessage,
        now_ms: int,
    ) -> str:
        binding_id = binding["binding_id"]
        batch = db.execute(
            """SELECT * FROM batches WHERE binding_id = ? AND conversation_id = ?
               AND state = 'collecting'""",
            (binding_id, message.conversation_id),
        ).fetchone()
        if batch is not None and (
            batch["deadline_ms"] <= now_ms
            or batch["character_count"] + len(message.text)
            > self._policy.max_characters
        ):
            db.execute(
                "UPDATE batches SET state = 'ready' WHERE batch_id = ?",
                (batch["batch_id"],),
            )
            batch = None
        if batch is None:
            batch_id = uuid4().hex
            db.execute(
                """INSERT INTO batches(batch_id, binding_id, conversation_id,
                   binding_epoch, conversation_epoch, state, first_ms, deadline_ms,
                   message_count, character_count) VALUES (?, ?, ?, ?, ?, 'collecting', ?, ?, 0, 0)""",
                (
                    batch_id,
                    binding_id,
                    message.conversation_id,
                    binding["epoch"],
                    conversation["epoch"],
                    now_ms,
                    self._policy.deadline(now_ms, now_ms),
                ),
            )
            first_ms, count, characters = now_ms, 0, 0
        else:
            batch_id = batch["batch_id"]
            first_ms, count, characters = (
                batch["first_ms"],
                batch["message_count"],
                batch["character_count"],
            )
        count += 1
        characters += len(message.text)
        state = (
            "ready"
            if (
                count >= self._policy.max_messages
                or characters >= self._policy.max_characters
            )
            else "collecting"
        )
        db.execute(
            """UPDATE batches SET state = ?, deadline_ms = ?, message_count = ?,
               character_count = ? WHERE batch_id = ?""",
            (
                state,
                self._policy.deadline(first_ms, now_ms),
                count,
                characters,
                batch_id,
            ),
        )
        return batch_id

    @staticmethod
    def _binding(db: sqlite3.Connection, binding_id: str) -> sqlite3.Row:
        row = db.execute(
            "SELECT * FROM bindings WHERE binding_id = ?", (binding_id,)
        ).fetchone()
        if row is None:
            raise KeyError("hosting binding not found")
        return row

    @staticmethod
    def _conversation(
        db: sqlite3.Connection, binding_id: str, conversation_id: str
    ) -> sqlite3.Row:
        db.execute(
            """INSERT INTO conversations(binding_id, conversation_id, session_id)
               VALUES (?, ?, ?) ON CONFLICT(binding_id, conversation_id) DO NOTHING""",
            (binding_id, conversation_id, uuid4().hex),
        )
        return db.execute(
            "SELECT * FROM conversations WHERE binding_id = ? AND conversation_id = ?",
            (binding_id, conversation_id),
        ).fetchone()

    @staticmethod
    def _turn_row(db: sqlite3.Connection, binding_id: str, turn_id: str) -> sqlite3.Row:
        row = db.execute(
            "SELECT * FROM batches WHERE binding_id = ? AND batch_id = ? AND snapshot_json IS NOT NULL",
            (binding_id, turn_id),
        ).fetchone()
        if row is None:
            raise KeyError("hosting turn not found")
        return row

    @staticmethod
    def _check_time(now_ms: int) -> None:
        if type(now_ms) is not int or now_ms < 0:
            raise ValueError("now_ms must be non-negative UTC epoch milliseconds")
