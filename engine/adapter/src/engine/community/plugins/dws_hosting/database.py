"""Private SQLite state, explicit configuration and fail-closed transactions."""

from __future__ import annotations

import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from engine.community.plugins.dws_hosting.execution_schema import EXECUTION_SCHEMA
from engine.community.plugins.dws_hosting.outbox_schema import OUTBOX_SCHEMA
from engine.community.plugins.dws_hosting.runtime_schema import RUNTIME_SCHEMA

APPLICATION_ID = 0x44575348  # DWSH

SCHEMA = """
CREATE TABLE IF NOT EXISTS bindings (
    binding_id TEXT PRIMARY KEY, identity_json TEXT NOT NULL,
    account_id TEXT NOT NULL, environment TEXT NOT NULL, organization_id TEXT NOT NULL,
    bot_id TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0, epoch INTEGER NOT NULL DEFAULT 0,
    active_since INTEGER NOT NULL DEFAULT 0,
    rule_version INTEGER NOT NULL DEFAULT 0, rule_text TEXT NOT NULL DEFAULT '',
    UNIQUE(environment, organization_id, bot_id, account_id)
);
CREATE TABLE IF NOT EXISTS conversations (
    binding_id TEXT NOT NULL REFERENCES bindings(binding_id),
    conversation_id TEXT NOT NULL, session_id TEXT NOT NULL UNIQUE,
    taken_over INTEGER NOT NULL DEFAULT 0, epoch INTEGER NOT NULL DEFAULT 0,
    active_since INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (binding_id, conversation_id)
);
CREATE TABLE IF NOT EXISTS batches (
    batch_id TEXT PRIMARY KEY, binding_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL, binding_epoch INTEGER NOT NULL,
    conversation_epoch INTEGER NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('collecting','ready','running','unknown','completed','cancelled')),
    first_ms INTEGER NOT NULL, deadline_ms INTEGER NOT NULL,
    message_count INTEGER NOT NULL, character_count INTEGER NOT NULL,
    snapshot_json TEXT,
    FOREIGN KEY (binding_id, conversation_id)
        REFERENCES conversations(binding_id, conversation_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_collecting_batch
    ON batches(binding_id, conversation_id) WHERE state = 'collecting';
CREATE UNIQUE INDEX IF NOT EXISTS one_active_turn
    ON batches(binding_id, conversation_id) WHERE state IN ('running','unknown');
CREATE INDEX IF NOT EXISTS ready_batches ON batches(binding_id, state, deadline_ms);
CREATE TABLE IF NOT EXISTS inbox (
    binding_id TEXT NOT NULL, conversation_id TEXT NOT NULL, message_id TEXT NOT NULL,
    message_json TEXT NOT NULL, batch_id TEXT REFERENCES batches(batch_id),
    PRIMARY KEY (binding_id, conversation_id, message_id),
    FOREIGN KEY (binding_id, conversation_id)
        REFERENCES conversations(binding_id, conversation_id)
);
CREATE INDEX IF NOT EXISTS batch_messages ON inbox(batch_id);
"""

RECOVERY_SCHEMA = """CREATE TABLE intake_checkpoints (
    binding_id TEXT PRIMARY KEY REFERENCES bindings(binding_id),
    epoch INTEGER NOT NULL, complete_until_ms INTEGER NOT NULL,
    ready INTEGER NOT NULL DEFAULT 0, attempt_id TEXT NOT NULL DEFAULT ''
)"""

HINT_SCHEMA = """CREATE TABLE intake_hints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    binding_id TEXT NOT NULL REFERENCES bindings(binding_id),
    conversation_id TEXT NOT NULL, message_id TEXT NOT NULL, created_ms INTEGER NOT NULL,
    UNIQUE(binding_id, conversation_id, message_id)
)"""


class HostingDatabase:
    """One dedicated state directory, on storage verified to support SQLite locks.

    No WAL assumption for NAS. DELETE journal + FULL sync still require deployment
    validation. Existing unrelated databases and insecure paths are rejected.
    """

    def __init__(self, state_dir: Path) -> None:
        state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        # COSEC: message bodies are private; do not silently use a shared directory.
        self._check_private(state_dir, directory=True)
        self.path = state_dir / "dws_hosting.sqlite3"
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            self._check_private(self.path, directory=False)
        else:
            os.close(fd)
        with self.transaction() as db:
            if db.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
                raise ValueError("DWS state requires DELETE journal mode")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2, 3, 4, 5, 6, 7):
                raise ValueError("unsupported DWS state schema version")
            application_id = db.execute("PRAGMA application_id").fetchone()[0]
            if version == 0:
                if (
                    application_id != 0
                    or db.execute(
                        "SELECT 1 FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' LIMIT 1",
                    ).fetchone()
                ):
                    raise ValueError("refusing to initialize an unrelated database")
                for statement in SCHEMA.split(";"):
                    if statement.strip():
                        db.execute(statement)
                db.execute("PRAGMA application_id = 1146573640")
                db.execute("PRAGMA user_version = 1")
            elif application_id != APPLICATION_ID:
                raise ValueError("not a DWS hosting database")
            if version < 2:
                db.execute(RECOVERY_SCHEMA)
                db.execute(HINT_SCHEMA)
                db.execute("PRAGMA user_version = 2")
            if version < 3:
                db.execute(EXECUTION_SCHEMA)
                db.execute("PRAGMA user_version = 3")
            if version < 4:
                db.execute(OUTBOX_SCHEMA)
                db.execute(
                    "CREATE INDEX outbox_due ON outbox(binding_id, state, due_ms)"
                )
                db.execute("PRAGMA user_version = 4")
            if version == 4:
                # No tables reference outbox; rebuild its CHECK constraint atomically.
                db.execute("ALTER TABLE outbox RENAME TO outbox_v4")
                db.execute("DROP INDEX outbox_due")
                db.execute(OUTBOX_SCHEMA)
                db.execute("INSERT INTO outbox SELECT * FROM outbox_v4")
                db.execute("DROP TABLE outbox_v4")
                db.execute(
                    "CREATE INDEX outbox_due ON outbox(binding_id, state, due_ms)"
                )
            if version < 5:
                db.execute("PRAGMA user_version = 5")
            if version < 6:
                for statement in RUNTIME_SCHEMA:
                    db.execute(statement)
                db.execute("PRAGMA user_version = 6")
            if version < 7:
                db.execute("ALTER TABLE executions ADD COLUMN review_json TEXT")
                db.execute("PRAGMA user_version = 7")

    @staticmethod
    def _check_private(path: Path, *, directory: bool) -> None:
        info = path.lstat()
        expected_type = stat.S_ISDIR if directory else stat.S_ISREG
        if not expected_type(info.st_mode) or info.st_mode & 0o077:
            raise ValueError("DWS state requires a private directory and regular file")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("PRAGMA synchronous = FULL")
            # Serialize state transitions across threads/processes, not just Python locks.
            db.execute("BEGIN IMMEDIATE")
            with db:
                yield db
        finally:
            db.close()
