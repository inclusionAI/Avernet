"""Durable history checkpoint and claim barrier, separate from CLI transport."""

from pathlib import Path
from uuid import uuid4

from engine.community.kernel.dws_connection import (
    HintWindow,
    ListenerNotice,
    RecoveryWindow,
)
from engine.community.kernel.dws_hosting import Binding
from engine.community.plugins.dws_hosting.database import HostingDatabase
from engine.community.plugins.dws_hosting.state import SqliteHostingStateStore
from engine.community.kernel.dws_runtime import RuntimePolicy
from engine.community.plugins.dws_hosting.capacity import check_intake


class SqliteHostingRecoveryStore:
    def __init__(
        self, state_dir: Path, policy: RuntimePolicy = RuntimePolicy()
    ) -> None:
        self._database = HostingDatabase(state_dir)
        self.policy = policy

    def block_intake(self, binding_id: str) -> None:
        with self._database.transaction() as db:
            binding = SqliteHostingStateStore._binding(db, binding_id)
            db.execute(
                """INSERT INTO intake_checkpoints(binding_id, epoch, complete_until_ms)
                VALUES (?, ?, ?) ON CONFLICT(binding_id) DO UPDATE SET ready = 0, attempt_id = ''""",
                (binding_id, binding["epoch"], binding["active_since"]),
            )

    def recovery_window(
        self,
        binding_id: str,
        *,
        now_ms: int,
        overlap_ms: int,
        window_ms: int,
    ) -> RecoveryWindow | None:
        SqliteHostingStateStore._check_time(now_ms)
        if overlap_ms <= 0 or window_ms <= overlap_ms:
            raise ValueError("invalid recovery window")
        with self._database.transaction() as db:
            binding = SqliteHostingStateStore._binding(db, binding_id)
            db.execute(
                """INSERT INTO intake_checkpoints(binding_id, epoch, complete_until_ms)
                VALUES (?, ?, ?) ON CONFLICT(binding_id) DO NOTHING""",
                (binding_id, binding["epoch"], binding["active_since"]),
            )
            if not binding["enabled"]:
                raise ValueError("hosting is disabled")
            checkpoint = db.execute(
                "SELECT * FROM intake_checkpoints WHERE binding_id = ?",
                (binding_id,),
            ).fetchone()
            previous = checkpoint["complete_until_ms"]
            if checkpoint["epoch"] != binding["epoch"]:
                previous = binding["active_since"]
                db.execute(
                    "UPDATE intake_checkpoints SET epoch = ?, complete_until_ms = ?, ready = 0 WHERE binding_id = ?",
                    (binding["epoch"], previous, binding_id),
                )
            hints = db.execute(
                "SELECT MIN(created_ms), COALESCE(MAX(id), 0) FROM intake_hints WHERE binding_id = ?",
                (binding_id,),
            ).fetchone()
            # Discovery accepts integral seconds only. Intake independently fences activation.
            start = max(binding["active_since"], previous - overlap_ms) // 1000 * 1000
            end = min(now_ms // 1000 * 1000, start + window_ms)
            if end <= start or end <= previous:
                return None
            attempt_id = uuid4().hex
            db.execute(
                "UPDATE intake_checkpoints SET attempt_id = ? WHERE binding_id = ?",
                (attempt_id, binding_id),
            )
            return RecoveryWindow(
                binding=Binding.model_validate_json(binding["identity_json"]),
                epoch=binding["epoch"],
                start_ms=start,
                end_ms=end,
                previous_until_ms=previous,
                target_ms=now_ms // 1000 * 1000,
                attempt_id=attempt_id,
                hint_limit=hints[1],
            )

    def record_hint(self, binding_id: str, notice: ListenerNotice) -> None:
        if (
            notice.kind != "conversation"
            or not notice.conversation_id
            or not notice.message_id
        ):
            raise ValueError("invalid message hint")
        with self._database.transaction() as db:
            binding = SqliteHostingStateStore._binding(db, binding_id)
            if (
                not binding["enabled"]
                or notice.created_at_ms <= binding["active_since"]
            ):
                return
            SqliteHostingStateStore._conversation(
                db, binding_id, notice.conversation_id
            )
            if db.execute(
                "SELECT 1 FROM intake_hints WHERE binding_id = ? AND conversation_id = ? AND message_id = ?",
                (binding_id, notice.conversation_id, notice.message_id),
            ).fetchone():
                return
            check_intake(db, self._database.path, self.policy, hint=True)
            db.execute(
                """INSERT INTO intake_hints(binding_id, conversation_id, message_id, created_ms)
                VALUES (?, ?, ?, ?) ON CONFLICT(binding_id, conversation_id, message_id) DO NOTHING""",
                (
                    binding_id,
                    notice.conversation_id,
                    notice.message_id,
                    notice.created_at_ms,
                ),
            )

    def pending_hints(self, binding_id: str, *, now_ms: int) -> tuple[HintWindow, ...]:
        SqliteHostingStateStore._check_time(now_ms)
        with self._database.transaction() as db:
            binding = SqliteHostingStateStore._binding(db, binding_id)
            if not binding["enabled"]:
                return ()
            rows = db.execute(
                """SELECT conversation_id, MIN(created_ms) AS first_ms, MAX(id) AS last_id
                FROM intake_hints WHERE binding_id = ? AND created_ms > ?
                AND created_ms < ? GROUP BY conversation_id ORDER BY MIN(id) LIMIT 32""",
                (binding_id, binding["active_since"], now_ms // 1000 * 1000),
            ).fetchall()
            return tuple(
                HintWindow(
                    binding=Binding.model_validate_json(binding["identity_json"]),
                    epoch=binding["epoch"],
                    conversation_id=row["conversation_id"],
                    start_ms=max(binding["active_since"], row["first_ms"] - 30_000)
                    // 1000
                    * 1000,
                    end_ms=min(
                        now_ms // 1000 * 1000, row["first_ms"] // 1000 * 1000 + 300_000
                    ),
                    hint_limit=row["last_id"],
                )
                for row in rows
            )

    def complete_hints(self, window: HintWindow, observed_ids: tuple[str, ...]) -> None:
        with self._database.transaction() as db:
            binding = SqliteHostingStateStore._binding(db, window.binding.binding_id)
            if not binding["enabled"] or binding["epoch"] != window.epoch:
                return
            db.executemany(
                """DELETE FROM intake_hints WHERE binding_id = ? AND conversation_id = ?
                AND message_id = ? AND id <= ? AND created_ms < ?""",
                [
                    (
                        window.binding.binding_id,
                        window.conversation_id,
                        mid,
                        window.hint_limit,
                        window.end_ms,
                    )
                    for mid in observed_ids
                ],
            )

    def remember_conversation(self, binding_id: str, conversation_id: str) -> None:
        if not conversation_id or len(conversation_id) > 512:
            raise ValueError("invalid conversation ID")
        with self._database.transaction() as db:
            SqliteHostingStateStore._binding(db, binding_id)
            SqliteHostingStateStore._conversation(db, binding_id, conversation_id)

    def known_conversations(self, binding_id: str) -> tuple[str, ...]:
        with self._database.transaction() as db:
            SqliteHostingStateStore._binding(db, binding_id)
            rows = db.execute(
                "SELECT conversation_id FROM conversations WHERE binding_id = ? ORDER BY conversation_id LIMIT 10001",
                (binding_id,),
            ).fetchall()
            if len(rows) > 10_000:
                raise ValueError("hosting conversation capacity exceeded")
            return tuple(row[0] for row in rows)

    def resolve_hints(
        self,
        window: RecoveryWindow,
        conversation_id: str,
        observed_ids: tuple[str, ...],
    ) -> None:
        with self._database.transaction() as db:
            db.executemany(
                """DELETE FROM intake_hints WHERE binding_id = ? AND conversation_id = ?
                AND message_id = ? AND id <= ? AND created_ms < ?
                AND EXISTS (SELECT 1 FROM intake_checkpoints WHERE binding_id = ? AND attempt_id = ?)""",
                [
                    (
                        window.binding.binding_id,
                        conversation_id,
                        mid,
                        window.hint_limit,
                        window.end_ms,
                        window.binding.binding_id,
                        window.attempt_id,
                    )
                    for mid in observed_ids
                ],
            )

    def complete_recovery(self, window: RecoveryWindow) -> bool:
        with self._database.transaction() as db:
            binding = SqliteHostingStateStore._binding(db, window.binding.binding_id)
            if not binding["enabled"] or binding["epoch"] != window.epoch:
                return False
            cursor = db.execute(
                """UPDATE intake_checkpoints SET complete_until_ms = MAX(complete_until_ms, ?), attempt_id = ''
                WHERE binding_id = ? AND epoch = ? AND complete_until_ms = ? AND attempt_id = ?""",
                (
                    window.end_ms,
                    window.binding.binding_id,
                    window.epoch,
                    window.previous_until_ms,
                    window.attempt_id,
                ),
            )
            if cursor.rowcount != 1:
                return False
            db.execute(
                "DELETE FROM intake_hints WHERE binding_id = ? AND created_ms <= ?",
                (window.binding.binding_id, binding["active_since"]),
            )
            ready = max(window.previous_until_ms, window.end_ms) >= window.target_ms
            db.execute(
                "UPDATE intake_checkpoints SET ready = ? WHERE binding_id = ?",
                (ready, window.binding.binding_id),
            )
            return ready
