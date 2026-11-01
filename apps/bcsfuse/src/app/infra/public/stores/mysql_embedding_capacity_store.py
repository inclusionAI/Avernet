"""MySQL shared embedding-capacity state for multi-replica deployment."""

from __future__ import annotations

from mysql.connector import Error

from app.infra.public.database.mysql_connection_pool import MySQLConnectionPoolProvider
from app.infra.public.stores.sqlite_embedding_capacity_store import (
    EmbeddingCapacityStore,
)

_CREATE_TABLES = (
    """
    CREATE TABLE IF NOT EXISTS bcsfuse_embedding_capacity_states (
        model VARCHAR(255) NOT NULL,
        state VARCHAR(64) NOT NULL,
        expires_at DOUBLE NOT NULL,
        updated_at DOUBLE NOT NULL,
        gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modify TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (model),
        KEY idx_bcsfuse_embedding_capacity_expiry (expires_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS bcsfuse_embedding_call_windows (
        model VARCHAR(255) NOT NULL,
        window_start DOUBLE NOT NULL,
        window_calls INTEGER NOT NULL,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (model)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
)


class MySQLEmbeddingCapacityStore:
    """Share model quota exhaustion through the production MySQL database."""

    def __init__(self, connection_pool: MySQLConnectionPoolProvider):
        if connection_pool is None:
            raise ValueError("connection_pool is required")
        self._pool = connection_pool
        # Capacity coordination fails open. Do not block composition or pod
        # startup when this optional coordination database is unavailable.
        self._schema_ready = False

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        connection = self._pool.get_connection()
        cursor = connection.cursor()
        try:
            for statement in _CREATE_TABLES:
                cursor.execute(statement)
            connection.commit()
        except Error as error:
            connection.rollback()
            raise RuntimeError(f"MySQL capacity schema init failed: {error}") from error
        finally:
            cursor.close()
            connection.close()
        self._schema_ready = True

    def observe(self, model: str) -> tuple[str, float]:
        if not model:
            raise ValueError("model is required")
        self._ensure_schema()
        connection = self._pool.get_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                "SELECT state, expires_at FROM "
                "bcsfuse_embedding_capacity_states WHERE model = %s",
                (model,),
            )
            row = cursor.fetchone()
            if row is None:
                return ("available", 0.0)
            return (str(row[0]), float(row[1]))
        finally:
            cursor.close()
            connection.close()

    def reserve(
        self,
        model: str,
        *,
        now_epoch_seconds: float,
        max_calls: int,
        period_seconds: float,
    ) -> tuple[bool, float, str]:
        """Atomically reserve one shared upstream call in the current window."""

        if not model:
            raise ValueError("model is required")
        if max_calls <= 0 or period_seconds <= 0:
            return (True, 0.0, "available")
        reason = "embedding_rate_limit_admission_rejected"
        self._ensure_schema()
        connection = self._pool.get_connection()
        cursor = connection.cursor()
        try:
            connection.start_transaction()
            cursor.execute(
                "SELECT window_start, window_calls FROM "
                "bcsfuse_embedding_call_windows WHERE model = %s FOR UPDATE",
                (model,),
            )
            row = cursor.fetchone()
            if row is None:
                cursor.execute(
                    "INSERT INTO bcsfuse_embedding_call_windows "
                    "(model, window_start, window_calls) VALUES (%s, %s, 1)",
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
                return (
                    False,
                    max(0.001, window_start + period_seconds - now_epoch_seconds),
                    reason,
                )
            cursor.execute(
                "UPDATE bcsfuse_embedding_call_windows "
                "SET window_start = %s, window_calls = %s WHERE model = %s",
                (window_start, calls + 1, model),
            )
            connection.commit()
            return (True, 0.0, "available")
        except Error as error:
            connection.rollback()
            raise RuntimeError(
                f"MySQL embedding call reserve failed: {error}"
            ) from error
        finally:
            cursor.close()
            connection.close()

    def mark_exhausted(
        self,
        model: str,
        *,
        blocked_until_epoch_seconds: float,
        reason_code: str = "embedding_quota_exhausted",
    ) -> tuple[str, float]:
        if not model:
            raise ValueError("model is required")
        if not reason_code:
            raise ValueError("reason_code is required")
        if blocked_until_epoch_seconds <= 0:
            raise ValueError("blocked_until_epoch_seconds must be positive")
        self._ensure_schema()
        connection = self._pool.get_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO bcsfuse_embedding_capacity_states
                    (model, state, expires_at, updated_at)
                VALUES (%s, %s, %s, COALESCE(UNIX_TIMESTAMP(NOW(6)), 0))
                ON DUPLICATE KEY UPDATE
                    state = VALUES(state),
                    expires_at = GREATEST(expires_at, VALUES(expires_at)),
                    updated_at = VALUES(updated_at)
                """,
                (model, reason_code, blocked_until_epoch_seconds),
            )
            cursor.execute(
                "SELECT state, expires_at FROM "
                "bcsfuse_embedding_capacity_states WHERE model = %s",
                (model,),
            )
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("embedding capacity state was not persisted")
            connection.commit()
            return (str(row[0]), float(row[1]))
        except Error as error:
            connection.rollback()
            raise RuntimeError(f"MySQL capacity update failed: {error}") from error
        finally:
            cursor.close()
            connection.close()

    def close(self) -> None:
        return None


__all__ = [
    "EmbeddingCapacityStore",
    "MySQLEmbeddingCapacityStore",
]
