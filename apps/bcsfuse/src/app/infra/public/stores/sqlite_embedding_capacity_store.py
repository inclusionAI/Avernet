"""SQLite shared embedding-capacity state for co-located development pods."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Protocol

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS bcsfuse_embedding_capacity_states (
    model TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    expires_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS bcsfuse_embedding_call_windows (
    model TEXT PRIMARY KEY,
    window_start REAL NOT NULL,
    window_calls INTEGER NOT NULL
);
"""


class EmbeddingCapacityStore(Protocol):
    """Durable model-capacity state shared by all BCSFuse replicas."""

    def observe(self, model: str) -> tuple[str, float]:
        """Return the current reason and its expiry epoch (0 when open)."""

    def reserve(
        self,
        model: str,
        *,
        now_epoch_seconds: float,
        max_calls: int,
        period_seconds: float,
    ) -> tuple[bool, float, str]:
        """Atomically reserve one upstream call in a model-level window."""

    def mark_exhausted(
        self,
        model: str,
        *,
        blocked_until_epoch_seconds: float,
        reason_code: str = "quota_exhausted",
    ) -> tuple[str, float]:
        """Publish or extend an exhaustion window for the model."""


class SQLiteEmbeddingCapacityStore:
    """Coordinate embedding quota blocks in one SQLite file.

    This store is intentionally tiny. It stores only non-secret model-level
    capacity state and metadata-free timestamps; it never records provider
    URLs, credentials, request bodies, or responses.
    """

    def __init__(self, path: str):
        if not path:
            raise ValueError("path is required")
        self.db_path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._execute(lambda cursor: cursor.executescript(_CREATE_TABLE))

    def observe(self, model: str) -> tuple[str, float]:
        self._validate_model(model)
        row = self._execute(
            lambda cursor: cursor.execute(
                "SELECT state, expires_at FROM "
                "bcsfuse_embedding_capacity_states WHERE model = ?",
                (model,),
            ).fetchone()
        )
        if row is None:
            return ("available", 0.0)
        return (str(row[0]), float(row[1]))

    def reserve(
        self,
        model: str,
        *,
        now_epoch_seconds: float,
        max_calls: int,
        period_seconds: float,
    ) -> tuple[bool, float, str]:
        if not model:
            raise ValueError("model is required")
        if max_calls <= 0 or period_seconds <= 0:
            return (True, 0.0, "available")
        reason = "embedding_rate_limit_admission_rejected"
        with sqlite3.connect(
            self.db_path, timeout=30.0, isolation_level=None
        ) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=30000")
            cursor = connection.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")
                cursor.execute(
                    "SELECT window_start, window_calls FROM "
                    "bcsfuse_embedding_call_windows WHERE model = ?",
                    (model,),
                )
                row = cursor.fetchone()
                if row is None:
                    cursor.execute(
                        "INSERT INTO bcsfuse_embedding_call_windows "
                        "(model, window_start, window_calls) VALUES (?, ?, 1)",
                        (model, now_epoch_seconds),
                    )
                    connection.commit()
                    return (True, 0.0, "available")

                window_start = float(row[0])
                calls = int(row[1])
                if (
                    now_epoch_seconds < window_start
                    or now_epoch_seconds >= window_start + period_seconds
                ):
                    window_start = now_epoch_seconds
                    calls = 0

                if calls >= max_calls:
                    connection.commit()
                    retry_after = window_start + period_seconds - now_epoch_seconds
                    return (
                        False,
                        max(0.001, retry_after),
                        reason,
                    )

                cursor.execute(
                    "UPDATE bcsfuse_embedding_call_windows "
                    "SET window_start = ?, window_calls = ? WHERE model = ?",
                    (window_start, calls + 1, model),
                )
                connection.commit()
                return (True, 0.0, "available")
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()

    def mark_exhausted(
        self,
        model: str,
        *,
        blocked_until_epoch_seconds: float,
        reason_code: str = "quota_exhausted",
    ) -> tuple[str, float]:
        self._validate_model(model)
        self._validate_reason(reason_code, blocked_until_epoch_seconds)
        with sqlite3.connect(
            self.db_path, timeout=30.0, isolation_level=None
        ) as connection:
            cursor = connection.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")
                cursor.execute(
                    """
                    INSERT INTO bcsfuse_embedding_capacity_states
                        (model, state, expires_at, updated_at)
                    VALUES (?, ?, ?, unixepoch())
                    ON CONFLICT(model) DO UPDATE SET
                        state = excluded.state,
                        expires_at = MAX(
                            bcsfuse_embedding_capacity_states.expires_at,
                            excluded.expires_at
                        ),
                        updated_at = excluded.updated_at
                    """,
                    (model, reason_code, blocked_until_epoch_seconds),
                )
                cursor.execute(
                    """
                    SELECT state, expires_at FROM
                        bcsfuse_embedding_capacity_states WHERE model = ?
                    """,
                    (model,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise RuntimeError("embedding capacity state was not persisted")
                connection.commit()
                return (str(row[0]), float(row[1]))
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()

    @staticmethod
    def _validate_model(model: str) -> None:
        if not model:
            raise ValueError("model is required")

    @staticmethod
    def _validate_reason(reason_code: str, expires_at: float) -> None:
        if not reason_code:
            raise ValueError("reason_code is required")
        if expires_at <= 0:
            raise ValueError("blocked_until_epoch_seconds must be positive")


    def _execute(self, operation):
        with sqlite3.connect(
            self.db_path, timeout=30.0, isolation_level=None
        ) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=30000")
            cursor = connection.cursor()
            try:
                return operation(cursor)
            finally:
                cursor.close()

    def _read(self, cursor, model: str):
        return cursor.execute(
            "SELECT state, expires_at FROM bcsfuse_embedding_capacity_states "
            "WHERE model = ?",
            (model,),
        ).fetchone()


__all__ = ["EmbeddingCapacityStore", "SQLiteEmbeddingCapacityStore"]
