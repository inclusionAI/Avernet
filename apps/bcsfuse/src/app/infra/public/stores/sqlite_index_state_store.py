"""SQLite durable owner and revision state for development and smoke modes."""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

from app.domain.models.index_state import IndexStateRecord

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS bcsfuse_index_states (
    collection_pair TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    owner_expires_at REAL NOT NULL,
    index_version INTEGER NOT NULL DEFAULT 0,
    index_updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS bcsfuse_embedding_index_signatures (
    collection_pair TEXT PRIMARY KEY,
    embedding_model TEXT NOT NULL,
    embedding_dimension INTEGER NOT NULL,
    updated_at REAL NOT NULL
)
"""


class SQLiteIndexStateStore:
    """Coordinate one owner per collection pair in one SQLite database file."""

    def __init__(self, path: str):
        if not path:
            raise ValueError("path is required")
        self.db_path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._execute(lambda cursor: cursor.executescript(_CREATE_TABLE))

    def claim_owner(
        self, collection_pair: str, owner_id: str, *, lease_seconds: float
    ) -> IndexStateRecord:
        self._validate_identity(collection_pair, owner_id)
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")

        result = self._transaction(
            lambda cursor: self._claim(
                cursor,
                collection_pair=collection_pair,
                owner_id=owner_id,
                lease_seconds=lease_seconds,
            )
        )
        return result

    def read_owner(self, collection_pair: str) -> IndexStateRecord:
        if not collection_pair:
            raise ValueError("collection_pair is required")
        record = self._execute(
            lambda cursor: self._read(cursor, collection_pair)
        )
        if record is None:
            raise FileNotFoundError("index state row does not exist")
        return record

    def publish_index_version(
        self, collection_pair: str, owner_id: str, index_version: int
    ) -> IndexStateRecord | None:
        self._validate_identity(collection_pair, owner_id)
        if index_version < 0:
            raise ValueError("index_version must not be negative")
        record = self._transaction(
            lambda cursor: self._publish(
                cursor, collection_pair, owner_id, index_version
            )
        )
        if record is None:
            logger.warning(
                "[INDEX-STATE] rejected stale owner=%s for collection_pair=%s",
                owner_id, collection_pair,
            )
        return record

    def register_embedding_index_signature(
        self,
        collection_pair: str,
        *,
        embedding_model: str,
        embedding_dimension: int,
    ) -> tuple[str, int]:
        """Return the active model signature, registering only the first one."""
        if not collection_pair:
            raise ValueError("collection_pair is required")
        if not embedding_model:
            raise ValueError("embedding_model is required")
        if embedding_dimension <= 0:
            raise ValueError("embedding_dimension must be positive")
        return self._transaction(
            lambda cursor: self._register_signature(
                cursor,
                collection_pair=collection_pair,
                embedding_model=embedding_model,
                embedding_dimension=embedding_dimension,
            )
        )

    @staticmethod
    def _register_signature(
        cursor,
        *,
        collection_pair: str,
        embedding_model: str,
        embedding_dimension: int,
    ) -> tuple[str, int]:
        cursor.execute(
            """
            SELECT embedding_model, embedding_dimension
            FROM bcsfuse_embedding_index_signatures
            WHERE collection_pair = ?
            """,
            (collection_pair,),
        )
        row = cursor.fetchone()
        if row is not None:
            return (str(row[0]), int(row[1]))
        cursor.execute(
            """
            INSERT INTO bcsfuse_embedding_index_signatures
                (collection_pair, embedding_model, embedding_dimension, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (collection_pair, embedding_model, embedding_dimension, time.time()),
        )
        return (embedding_model, embedding_dimension)

    def release_owner(self, collection_pair: str, owner_id: str) -> None:
        self._validate_identity(collection_pair, owner_id)
        self._execute(
            lambda cursor: cursor.execute(
                "UPDATE bcsfuse_index_states SET owner_expires_at = ? "
                "WHERE collection_pair = ? AND owner_id = ?",
                (time.time() - 0.001, collection_pair, owner_id),
            )
        )

    @staticmethod
    def _validate_identity(collection_pair: str, owner_id: str) -> None:
        if not collection_pair:
            raise ValueError("collection_pair is required")
        if not owner_id:
            raise ValueError("owner_id is required")

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.db_path, timeout=30.0, isolation_level=None, check_same_thread=True
        )
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _execute(self, operation):
        with self._connection() as connection:
            cursor = connection.cursor()
            try:
                return operation(cursor)
            finally:
                cursor.close()

    def _transaction(self, operation):
        with self._connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")
                result = operation(cursor)
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()

    def _claim(
        self, cursor, *, collection_pair: str, owner_id: str,
        lease_seconds: float,
    ):
        cursor.execute(
            """
            SELECT collection_pair, owner_id, owner_expires_at, index_version,
                   index_updated_at
            FROM bcsfuse_index_states
            WHERE collection_pair = ?
            """,
            (collection_pair,),
        )
        row = cursor.fetchone()
        actual_now = time.time()
        observed_at = (
            max(actual_now, float(row[4])) if row is not None else actual_now
        )
        expires_at = actual_now + lease_seconds
        if row is None:
            cursor.execute(
                """
                INSERT INTO bcsfuse_index_states
                    (collection_pair, owner_id, owner_expires_at,
                     index_version, index_updated_at)
                VALUES (?, ?, ?, 0, ?)
                """,
                (collection_pair, owner_id, expires_at, actual_now),
            )
            return self._record(
                actual_now,
                row=(collection_pair, owner_id, expires_at, 0, actual_now),
            )
        if row[2] <= actual_now:
            cursor.execute(
                """
                UPDATE bcsfuse_index_states
                SET owner_id = ?, owner_expires_at = ?
                WHERE collection_pair = ?
                """,
                (owner_id, expires_at, collection_pair),
            )
            return self._record(
                observed_at,
                row=(collection_pair, owner_id, expires_at, row[3], row[4]),
            )
        if row[1] == owner_id:
            cursor.execute(
                "UPDATE bcsfuse_index_states SET owner_expires_at = ? WHERE collection_pair = ?",
                (expires_at, collection_pair),
            )
            return self._record(
                observed_at,
                row=(collection_pair, owner_id, expires_at, row[3], row[4]),
            )
        return self._record(observed_at, row=row)

    def _publish(
        self, cursor, collection_pair: str, owner_id: str,
        index_version: int,
    ):
        now = time.time()
        cursor.execute(
            "SELECT collection_pair, owner_id, owner_expires_at, index_version, "
            "index_updated_at FROM bcsfuse_index_states WHERE collection_pair = ?",
            (collection_pair,),
        )
        row = cursor.fetchone()
        if row is None or row[1] != owner_id or row[2] <= now:
            return None
        if int(row[3]) >= index_version:
            return None
        cursor.execute(
            """
            UPDATE bcsfuse_index_states
            SET index_version = ?, index_updated_at = ?
            WHERE collection_pair = ? AND owner_id = ?
            """,
            (index_version, now, collection_pair, owner_id),
        )
        return self._record(now, row=(row[0], row[1], row[2], index_version, now))

    def _read(self, cursor, collection_pair: str):
        now = time.time()
        cursor.execute(
            "SELECT collection_pair, owner_id, owner_expires_at, index_version, "
            "index_updated_at FROM bcsfuse_index_states WHERE collection_pair = ?",
            (collection_pair,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        observed_at = max(now, float(row[4]))
        return self._record(observed_at, row=row)

    @staticmethod
    def _record(now: float, *, row):
        if row is None:
            raise ValueError("index state row is required")
        return IndexStateRecord(
            collection_pair=row[0],
            owner_id=row[1],
            owner_expires_at=float(row[2]),
            index_version=int(row[3]),
            index_updated_at=float(row[4]),
            observed_at=now,
        )


__all__ = ["SQLiteIndexStateStore"]
