"""
MySQL Worker Profile Binding Store (Connection Pool Version)

MySQL implementation for production OSS deployments.

S29C Status: Full CRUD implementation complete.
S30A Status: Observability logging added for real storage validation.

R12-Pool-4 Fix:
- Replaced shared self._connection with MySQLConnectionPoolProvider
- Each method gets its own connection from pool
- Connection returned to pool after use (conn.close())
- Thread-safe by design (pool handles connection distribution)
- Transaction handling: restore autocommit before returning connection
- No more Fatal Python error from concurrent MySQL connector access
"""

import logging
import os
import threading
import time
import uuid
from datetime import datetime
from typing import Optional, TYPE_CHECKING

import mysql.connector
from mysql.connector import Error

from src.domain.models.worker_profile_binding import WorkerProfileBinding
from src.domain.models.worker_source_info import WorkerSourceType
from src.infra.public.observability.storage_logging import (
    log_storage_event,
    log_storage_error,
    mask_host,
    mask_user,
    sanitize_key_fields,
)

if TYPE_CHECKING:
    from src.infra.public.database.mysql_connection_pool import MySQLConnectionPoolProvider

logger = logging.getLogger(__name__)


class MySQLProviderNotImplementedError(RuntimeError):
    """Raised when a MySQL provider method is called but not yet implemented."""
    pass


class MySQLWorkerProfileBindingStore:
    """MySQL Worker Profile Binding Store for OSS (Connection Pool Version).

    Suitable for production deployments with MySQL database.

    Thread Safety:
        - Uses connection pool for thread-safe access
        - Each method borrows connection from pool
        - Connection returned to pool after use
        - No shared connection state
        - Transaction handling: restore autocommit before returning

    Denormalized Column Sync (Phase C1):
        - After set_active_profile(): sync workers.active_profile_key
        - Workers.active_profile_key is DENORMALIZED MIRROR
        - bcsfuse_worker_profile_bindings is CANONICAL SOURCE OF TRUTH
    """

    def __init__(
        self,
        connection_pool: Optional["MySQLConnectionPoolProvider"] = None,
        host: Optional[str] = None,
        port: Optional[int] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        database: Optional[str] = None,
        worker_registry_store: Optional["MySQLWorkerRegistryStore"] = None,
    ):
        """Initialize MySQL store with connection pool.

        Args:
            connection_pool: MySQLConnectionPoolProvider instance (preferred).
            host: MySQL host (fallback if no pool provided).
            port: MySQL port (fallback if no pool provided).
            user: MySQL user (fallback if no pool provided).
            password: MySQL password (fallback if no pool provided).
            database: MySQL database (fallback if no pool provided).
            worker_registry_store: Worker registry store for denormalized column sync (Phase C1).
        """
        if connection_pool is None:
            from src.infra.public.database.mysql_connection_pool import MySQLConnectionPoolProvider

            self._pool = MySQLConnectionPoolProvider(
                host=host or os.getenv("MYSQL_HOST", "localhost"),
                port=port or int(os.getenv("MYSQL_PORT", "3306")),
                user=user or os.getenv("MYSQL_USER", ""),
                password=password or os.getenv("MYSQL_PASSWORD", ""),
                database=database or os.getenv("MYSQL_DATABASE", "bcsfuse"),
            )
            logger.info(
                "[MySQLWorkerProfileBindingStore] Created internal connection pool (fallback mode)"
            )
        else:
            self._pool = connection_pool
            logger.info(
                "[MySQLWorkerProfileBindingStore] Using injected connection pool"
            )

        self._registry_store = worker_registry_store
        self._schema_initialized = False
        self._schema_lock = threading.Lock()

    def _ensure_schema(self, conn) -> None:
        """Ensure database schema exists."""
        if self._schema_initialized:
            return

        with self._schema_lock:
            if self._schema_initialized:
                return

            component = "mysql_worker_profile_binding_store"
            table_name = "bcsfuse_worker_profile_bindings"
            start_time = time.time()

            log_storage_event(
                logger,
                logging.DEBUG,
                "mysql_schema_init_start",
                component=component,
                operation="init_schema",
                validation_phase="schema_init",
                backend="mysql",
                target_resource=table_name,
            )

            cursor = conn.cursor()

            try:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS bcsfuse_worker_profile_bindings (
                        binding_id VARCHAR(128) PRIMARY KEY,
                        worker_id VARCHAR(128) NOT NULL,
                        profile_key VARCHAR(256) NOT NULL,
                        source_type VARCHAR(64) NOT NULL,
                        is_active BOOLEAN NOT NULL DEFAULT FALSE,
                        bound_at TIMESTAMP NULL,
                        unbound_at TIMESTAMP NULL,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        INDEX idx_worker_id (worker_id),
                        INDEX idx_profile_key (profile_key),
                        INDEX idx_worker_active (worker_id, is_active),
                        UNIQUE INDEX uniq_worker_profile (worker_id, profile_key)
                    )
                """)

                duration_ms = (time.time() - start_time) * 1000

                log_storage_event(
                    logger,
                    logging.INFO,
                    "mysql_schema_init_success",
                    component=component,
                    operation="init_schema",
                    validation_phase="schema_init",
                    backend="mysql",
                    target_resource=table_name,
                    duration_ms=duration_ms,
                )

                self._schema_initialized = True

            except Error as e:
                duration_ms = (time.time() - start_time) * 1000

                log_storage_error(
                    logger,
                    "mysql_schema_init_failure",
                    component=component,
                    operation="init_schema",
                    validation_phase="schema_init",
                    backend="mysql",
                    target_resource=table_name,
                    error=e,
                    duration_ms=duration_ms,
                )

                raise
            finally:
                cursor.close()

    def _row_to_binding(self, row: dict) -> WorkerProfileBinding:
        """Convert database row to WorkerProfileBinding model."""
        return WorkerProfileBinding(
            id=row["binding_id"],
            worker_id=row["worker_id"],
            profile_key=row["profile_key"],
            source_type=WorkerSourceType(row["source_type"]),
            is_active=bool(row["is_active"]),
            bound_at=row["bound_at"] if row["bound_at"] else None,
            unbound_at=row["unbound_at"] if row["unbound_at"] else None,
            updated_at=row["updated_at"],
        )

    def bind_profile(
        self,
        worker_id: str,
        profile_key: str,
        source_type: WorkerSourceType,
    ) -> WorkerProfileBinding:
        """Bind Profile to Worker (thread-safe with connection pool)."""
        conn = self._pool.get_connection()
        try:
            self._ensure_schema(conn)

            cursor = conn.cursor(dictionary=True)

            try:
                now = datetime.utcnow()
                binding_id = f"binding_{uuid.uuid4().hex[:12]}"

                # First, deactivate all active bindings for this worker
                cursor.execute(
                    """
                    UPDATE bcsfuse_worker_profile_bindings
                    SET is_active = FALSE, unbound_at = %s, updated_at = %s
                    WHERE worker_id = %s AND is_active = TRUE
                    """,
                    (now, now, worker_id),
                )

                # Check if this binding already exists
                cursor.execute(
                    """
                    SELECT * FROM bcsfuse_worker_profile_bindings
                    WHERE worker_id = %s AND profile_key = %s
                    """,
                    (worker_id, profile_key),
                )
                existing = cursor.fetchone()

                if existing:
                    # Update existing binding to active
                    cursor.execute(
                        """
                        UPDATE bcsfuse_worker_profile_bindings
                        SET is_active = TRUE, unbound_at = NULL, updated_at = %s
                        WHERE binding_id = %s
                        """,
                        (now, existing["binding_id"]),
                    )

                    cursor.execute(
                        """
                        SELECT * FROM bcsfuse_worker_profile_bindings
                        WHERE binding_id = %s
                        """,
                        (existing["binding_id"],),
                    )
                    row = cursor.fetchone()
                    return self._row_to_binding(row)

                # Create new binding
                cursor.execute(
                    """
                    INSERT INTO bcsfuse_worker_profile_bindings
                    (binding_id, worker_id, profile_key, source_type, is_active, bound_at, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, TRUE, %s, %s, %s)
                    """,
                    (binding_id, worker_id, profile_key, source_type.value, now, now, now),
                )

                logger.debug(
                    f"[MySQLWorkerProfileBindingStore] bind_profile() completed: "
                    f"worker_id={worker_id}, profile_key={profile_key}, binding_id={binding_id}"
                )

                binding = WorkerProfileBinding(
                    id=binding_id,
                    worker_id=worker_id,
                    profile_key=profile_key,
                    source_type=source_type,
                    is_active=True,
                    bound_at=now,
                    updated_at=now,
                )

                # Phase C-Fast-3: Sync denormalized column workers.active_profile_key
                # This is CRITICAL for G5 fusion retrieval to find the active profile
                if self._registry_store:
                    try:
                        sync_success = self._registry_store.sync_active_profile_key_mirror(
                            worker_id=worker_id,
                            profile_key=profile_key,
                        )
                        if sync_success:
                            logger.info(
                                f"[MySQLWorkerProfileBindingStore] bind_profile() synced active_profile_key mirror: "
                                f"worker_id={worker_id}, profile_key={profile_key}"
                            )
                        else:
                            logger.warning(
                                f"[MySQLWorkerProfileBindingStore] bind_profile() failed to sync active_profile_key mirror: "
                                f"worker_id={worker_id}, profile_key={profile_key}"
                            )
                    except Exception as e:
                        logger.error(
                            f"[MySQLWorkerProfileBindingStore] bind_profile() error syncing active_profile_key mirror: {e}",
                            exc_info=True
                        )
                else:
                    logger.warning(
                        f"[MySQLWorkerProfileBindingStore] bind_profile() no registry_store, "
                        f"cannot sync active_profile_key mirror for worker_id={worker_id}"
                    )

                return binding

            finally:
                cursor.close()

        finally:
            conn.close()

    def unbind_profile(self, worker_id: str, profile_key: str) -> bool:
        """Unbind Profile (thread-safe with connection pool)."""
        conn = self._pool.get_connection()
        try:
            self._ensure_schema(conn)

            cursor = conn.cursor()

            try:
                now = datetime.utcnow()
                cursor.execute(
                    """
                    UPDATE bcsfuse_worker_profile_bindings
                    SET is_active = FALSE, unbound_at = %s, updated_at = %s
                    WHERE worker_id = %s AND profile_key = %s
                    """,
                    (now, now, worker_id, profile_key),
                )

                result = cursor.rowcount > 0

                logger.debug(
                    f"[MySQLWorkerProfileBindingStore] unbind_profile() completed: "
                    f"worker_id={worker_id}, profile_key={profile_key}, result={result}"
                )

                return result

            finally:
                cursor.close()

        finally:
            conn.close()

    def get_active_binding(self, worker_id: str) -> Optional[WorkerProfileBinding]:
        """Get active binding (thread-safe with connection pool)."""
        conn = self._pool.get_connection()
        try:
            self._ensure_schema(conn)

            cursor = conn.cursor(dictionary=True)

            try:
                cursor.execute(
                    """
                    SELECT * FROM bcsfuse_worker_profile_bindings
                    WHERE worker_id = %s AND is_active = TRUE
                    """,
                    (worker_id,),
                )
                row = cursor.fetchone()

                if row is None:
                    logger.debug(
                        f"[MySQLWorkerProfileBindingStore] get_active_binding() not found: "
                        f"worker_id={worker_id}"
                    )
                    return None

                logger.debug(
                    f"[MySQLWorkerProfileBindingStore] get_active_binding() found: "
                    f"worker_id={worker_id}, binding_id={row['binding_id']}"
                )

                return self._row_to_binding(row)

            finally:
                cursor.close()

        finally:
            conn.close()

    def set_active_profile(
        self,
        worker_id: str,
        profile_key: str,
    ) -> bool:
        """Set active Profile (thread-safe with connection pool).

        Transaction handling:
        - Sets autocommit=False for transaction
        - Deactivates previous active binding
        - Activates target binding
        - Commits or rolls back
        - Restores autocommit=True before returning connection to pool
        """
        conn = self._pool.get_connection()
        try:
            self._ensure_schema(conn)

            cursor = conn.cursor(dictionary=True)

            component = "mysql_worker_profile_binding_store"
            table_name = "bcsfuse_worker_profile_bindings"
            operation_name = "set_active_profile"
            start_time = time.time()

            log_storage_event(
                logger,
                logging.DEBUG,
                "mysql_transaction_start",
                component=component,
                operation=operation_name,
                validation_phase="operation",
                backend="mysql",
                target_resource=table_name,
                key_fields_masked=sanitize_key_fields({"worker_id": worker_id, "profile_key": profile_key}),
            )

            try:
                now = datetime.utcnow()

                # Start transaction
                conn.autocommit = False

                # Deactivate all active bindings for this worker
                cursor.execute(
                    """
                    UPDATE bcsfuse_worker_profile_bindings
                    SET is_active = FALSE, unbound_at = %s, updated_at = %s
                    WHERE worker_id = %s AND is_active = TRUE
                    """,
                    (now, now, worker_id),
                )

                # Activate the target binding
                cursor.execute(
                    """
                    UPDATE bcsfuse_worker_profile_bindings
                    SET is_active = TRUE, unbound_at = NULL, updated_at = %s
                    WHERE worker_id = %s AND profile_key = %s
                    """,
                    (now, worker_id, profile_key),
                )

                if cursor.rowcount == 0:
                    # Target binding not found, rollback
                    conn.rollback()
                    duration_ms = (time.time() - start_time) * 1000

                    log_storage_event(
                        logger,
                        logging.WARNING,
                        "mysql_transaction_rollback",
                        component=component,
                        operation=operation_name,
                        validation_phase="operation",
                        backend="mysql",
                        target_resource=table_name,
                        duration_ms=duration_ms,
                        result="rollback",
                        error_class="BindingNotFoundError",
                        error_code="NOT_FOUND",
                    )

                    return False

                # Commit transaction
                conn.commit()
                duration_ms = (time.time() - start_time) * 1000

                log_storage_event(
                    logger,
                    logging.INFO,
                    "mysql_transaction_commit",
                    component=component,
                    operation=operation_name,
                    validation_phase="operation",
                    backend="mysql",
                    target_resource=table_name,
                    duration_ms=duration_ms,
                )

                logger.debug(
                    f"[MySQLWorkerProfileBindingStore] set_active_profile() committed: "
                    f"worker_id={worker_id}, profile_key={profile_key}"
                )

                # Phase C1: Sync denormalized column workers.active_profile_key
                if self._registry_store:
                    try:
                        sync_success = self._registry_store.sync_active_profile_key_mirror(
                            worker_id=worker_id,
                            profile_key=profile_key,
                        )
                        if sync_success:
                            logger.info(
                                f"[MySQLWorkerProfileBindingStore] Synced active_profile_key mirror: "
                                f"worker_id={worker_id}, profile_key={profile_key}"
                            )
                        else:
                            logger.warning(
                                f"[MySQLWorkerProfileBindingStore] Failed to sync active_profile_key mirror: "
                                f"worker_id={worker_id}, profile_key={profile_key}"
                            )
                    except Exception as e:
                        logger.error(
                            f"[MySQLWorkerProfileBindingStore] Error syncing active_profile_key mirror: {e}",
                            exc_info=True
                        )
                else:
                    logger.debug(
                        f"[MySQLWorkerProfileBindingStore] No registry store, skipping active_profile_key sync"
                    )

                return True

            except Exception as e:
                conn.rollback()
                duration_ms = (time.time() - start_time) * 1000

                log_storage_error(
                    logger,
                    "mysql_transaction_rollback",
                    component=component,
                    operation=operation_name,
                    validation_phase="operation",
                    backend="mysql",
                    target_resource=table_name,
                    error=e,
                    duration_ms=duration_ms,
                )

                raise

            finally:
                # CRITICAL: Restore autocommit before returning connection to pool
                conn.autocommit = True
                cursor.close()

        finally:
            conn.close()

    def list_bindings_by_worker(self, worker_id: str) -> list[WorkerProfileBinding]:
        """List all bindings for Worker (thread-safe with connection pool)."""
        conn = self._pool.get_connection()
        try:
            self._ensure_schema(conn)

            cursor = conn.cursor(dictionary=True)

            try:
                cursor.execute(
                    """
                    SELECT * FROM bcsfuse_worker_profile_bindings
                    WHERE worker_id = %s
                    ORDER BY updated_at DESC
                    """,
                    (worker_id,),
                )
                rows = cursor.fetchall()

                logger.debug(
                    f"[MySQLWorkerProfileBindingStore] list_bindings_by_worker() completed: "
                    f"worker_id={worker_id}, count={len(rows)}"
                )

                return [self._row_to_binding(row) for row in rows]

            finally:
                cursor.close()

        finally:
            conn.close()

    def get_binding_by_profile_key(self, profile_key: str) -> Optional[WorkerProfileBinding]:
        """Get binding by profile_key (thread-safe with connection pool)."""
        import os
        logger.info("[G6-BINDING-QUERY] ========== get_binding_by_profile_key START ==========")
        logger.info("[G6-BINDING-QUERY] profile_key: %s", profile_key)
        logger.info("[G6-BINDING-QUERY] PID: %d", os.getpid())
        logger.info("[G6-BINDING-QUERY] Thread ID: %d", threading.current_thread().ident)

        conn = self._pool.get_connection()
        try:
            self._ensure_schema(conn)

            # Log connection details (masked)
            logger.info("[G6-BINDING-QUERY] Connection obtained from pool")
            logger.info("[G6-BINDING-QUERY] Connection type: %s", type(conn).__name__)

            cursor = conn.cursor(dictionary=True)

            try:
                query_start = time.time()
                logger.info("[G6-BINDING-QUERY] Executing query:")
                logger.info("[G6-BINDING-QUERY]   SELECT * FROM bcsfuse_worker_profile_bindings")
                logger.info("[G6-BINDING-QUERY]   WHERE profile_key = '%s' AND is_active = TRUE", profile_key)

                cursor.execute(
                    """
                    SELECT * FROM bcsfuse_worker_profile_bindings
                    WHERE profile_key = %s AND is_active = TRUE
                    """,
                    (profile_key,),
                )
                row = cursor.fetchone()
                query_elapsed = (time.time() - query_start) * 1000

                logger.info("[G6-BINDING-QUERY] Query completed in %.2f ms", query_elapsed)

                if row is None:
                    logger.warning("[G6-BINDING-QUERY] ❌ BINDING NOT FOUND")
                    logger.warning("[G6-BINDING-QUERY]   profile_key: %s", profile_key)
                    logger.warning("[G6-BINDING-QUERY]   is_active: TRUE")
                    logger.warning("[G6-BINDING-QUERY]   Possible causes:")
                    logger.warning("[G6-BINDING-QUERY]     1. No binding exists for this profile_key")
                    logger.warning("[G6-BINDING-QUERY]     2. Binding exists but is_active = FALSE")
                    logger.warning("[G6-BINDING-QUERY]     3. profile_key format mismatch (e.g., missing ':default' suffix)")
                    logger.warning("[G6-BINDING-QUERY] ========== get_binding_by_profile_key END (NOT FOUND) ==========")
                    return None

                logger.info("[G6-BINDING-QUERY] ✅ BINDING FOUND")
                logger.info("[G6-BINDING-QUERY]   binding_id: %s", row['binding_id'])
                logger.info("[G6-BINDING-QUERY]   worker_id: %s", row['worker_id'])
                logger.info("[G6-BINDING-QUERY]   profile_key: %s", row['profile_key'])
                logger.info("[G6-BINDING-QUERY]   is_active: %s", row['is_active'])
                logger.info("[G6-BINDING-QUERY]   source_type: %s", row['source_type'])
                logger.info("[G6-BINDING-QUERY]   bound_at: %s", row['bound_at'])
                logger.info("[G6-BINDING-QUERY] ========== get_binding_by_profile_key END (FOUND) ==========")

                return self._row_to_binding(row)

            finally:
                cursor.close()

        finally:
            conn.close()

    def close(self) -> None:
        """Close connection pool (for application shutdown)."""
        if self._pool:
            self._pool.close()
            logger.info("[MySQLWorkerProfileBindingStore] Connection pool closed")


__all__ = ["MySQLWorkerProfileBindingStore", "MySQLProviderNotImplementedError"]