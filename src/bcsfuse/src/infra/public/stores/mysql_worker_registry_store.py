"""
MySQL Worker Registry Store (Connection Pool Version)

MySQL implementation for production OSS deployments.

R15-PATCH-A: Worker Registry Schema and Interface Parity
- ADDED: lifecycle_state column (management layer: ACTIVE/INACTIVE/DISABLED)
- ADDED: runtime_state column (denormalized from worker_runtime_state, for scan performance)
- ADDED: active_profile_key column (reference to active profile)
- ADDED: source_type column (API/FILE/REGISTRY)
- ADDED: source_ref column (source reference)
- ADDED: external_id column (external ID)
- ADDED: config column (worker configuration JSON)
- ADDED: created_by column (creator ID)
- ADDED: updated_by column (updater ID)
- ADDED: Indexes on lifecycle_state, source_type, active_profile_key, runtime_state
- ADDED: Data backfill for lifecycle_state, runtime_state, active_profile_key
- FIXED: Semantic separation of lifecycle_state vs runtime_state vs availability
- FIXED: list() method to support lifecycle_states, source_types, domains filters
- FIXED: _row_to_worker() method to return Worker domain model
- FIXED: update_lifecycle_state() to update lifecycle_state column, not availability

Schema Migration:
- Migration is IDEMPOTENT (can be run multiple times safely)
- Uses INFORMATION_SCHEMA to check if columns exist (MySQL version independent)
- Does NOT delete or rename existing columns
- Does NOT destroy existing data
- Backfills data from existing tables (heuristic for lifecycle_state, canonical for runtime_state)

Semantic Separation:
- lifecycle_state (management layer): ACTIVE/INACTIVE/DISABLED - controls worker lifecycle
- runtime_state (operational layer): ONLINE/OFFLINE - actual operational status
- availability (visibility layer): PRIVATE/PROTECTED/PUBLIC - access control visibility
- CRITICAL: DO NOT confuse these three layers!

Source of Truth:
- lifecycle_state: workers.lifecycle_state column
- runtime_state: worker_runtime_state table (CANONICAL), workers.runtime_state (DENORMALIZED MIRROR)
- availability: workers.availability column
- active_profile_key: workers.active_profile_key column, synced from bcsfuse_worker_profile_bindings

R12-Pool-3 Fix:
- Replaced shared self._connection with MySQLConnectionPoolProvider
- Each method gets its own connection from pool
- Connection returned to pool after use (conn.close())
- Thread-safe by design (pool handles connection distribution)
- No more Fatal Python error from concurrent MySQL connector access

Migration from R12-6:
- REMOVED: self._connection (shared connection instance)
- REMOVED: self._lock (RLock no longer needed)
- REMOVED: _ensure_connection() (replaced by pool.get_connection())
- ADDED: connection_pool parameter in __init__
- ADDED: Per-method connection borrowing from pool
- ADDED: Safe diagnostics logging
"""
import os
import json
import threading
import logging
from typing import Optional, List, TYPE_CHECKING, Union
from datetime import datetime
import mysql.connector
from mysql.connector import Error

if TYPE_CHECKING:
    from src.infra.public.database.mysql_connection_pool import MySQLConnectionPoolProvider

logger = logging.getLogger(__name__)


def _normalize_enum_string(raw_value) -> str:
    """
    Normalize enum string for tolerant parsing.

    Handles legacy corrupted values like:
    - "Availability.PROTECTED" -> "protected"
    - "PROTECTED" -> "protected"
    - Availability.PROTECTED (enum) -> "protected"

    Args:
        raw_value: Raw enum value from database (could be enum, string, or None)

    Returns:
        Normalized lowercase string value
    """
    if raw_value is None:
        return None

    # If it's an enum object, extract the value
    if hasattr(raw_value, 'value'):
        return raw_value.value

    # Convert to string and clean up
    raw_str = str(raw_value).strip()

    # If it contains a dot, it's likely "ClassName.VALUE" format
    if "." in raw_str:
        raw_str = raw_str.split(".")[-1]

    # Return lowercase
    return raw_str.lower()


def _canonicalize_enum_for_storage(enum_value) -> str:
    """
    Canonicalize enum value for database storage.

    Ensures enum values are stored as canonical lowercase strings:
    - Availability.PROTECTED (enum) -> "protected"
    - "PROTECTED" -> "protected"
    - "protected" -> "protected"

    Args:
        enum_value: Enum object or string value

    Returns:
        Canonical lowercase string value for storage
    """
    if enum_value is None:
        return None

    # If it's an enum object, extract the value
    if hasattr(enum_value, 'value'):
        return enum_value.value

    # Convert to string
    value_str = str(enum_value).strip()

    # If it contains a dot (ClassName.VALUE), extract just the value
    if "." in value_str:
        value_str = value_str.split(".")[-1]

    # Return lowercase for consistency
    return value_str.lower()


class MySQLWorkerRegistryStore:
    """
    MySQL Worker Registry Store for OSS (Connection Pool Version).

    Suitable for production deployments with MySQL database.

    Thread Safety:
        - Uses connection pool for thread-safe access
        - Each method borrows connection from pool
        - Connection returned to pool after use
        - No shared connection state

    Usage:
        from src.infra.public.database.mysql_connection_pool import MySQLConnectionPoolProvider

        pool = MySQLConnectionPoolProvider(host="localhost", ...)
        store = MySQLWorkerRegistryStore(connection_pool=pool)

        # Thread-safe operations
        worker = store.get("worker_123")
    """

    def __init__(
        self,
        connection_pool: Optional["MySQLConnectionPoolProvider"] = None,
        host: Optional[str] = None,
        port: Optional[int] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        database: Optional[str] = None,
        pool_size: int = 15,
    ):
        """Initialize MySQL store with connection pool.

        Args:
            connection_pool: MySQLConnectionPoolProvider instance (preferred).
            host: MySQL host (fallback if no pool provided).
            port: MySQL port (fallback if no pool provided).
            user: MySQL user (fallback if no pool provided).
            password: MySQL password (fallback if no pool provided).
            database: MySQL database (fallback if no pool provided).
            pool_size: Pool size if creating pool internally (default: 15).

        Migration Guide:
            BEFORE (R12-6):
                store = MySQLWorkerRegistryStore(
                    host="localhost",
                    port=3306,
                    user="your_user",
                    password="your_password",
                    database="bcsfuse"
                )

            AFTER (R12-Pool-3):
                from src.infra.public.database.mysql_connection_pool import MySQLConnectionPoolProvider

                pool = MySQLConnectionPoolProvider(
                    host="localhost",
                    port=3306,
                    user="your_user",
                    password="your_password",
                    database="bcsfuse",
                    pool_size=15
                )
                store = MySQLWorkerRegistryStore(connection_pool=pool)
        """
        if connection_pool is None:
            # Fallback: create pool internally (for backward compatibility)
            from src.infra.public.database.mysql_connection_pool import MySQLConnectionPoolProvider

            _password = password or os.getenv("MYSQL_PASSWORD", "")
            if not _password:
                logger.warning(
                    "[MySQLWorkerRegistryStore] MYSQL_PASSWORD is empty or not set. "
                    "This is insecure for production. Set MYSQL_PASSWORD environment variable."
                )
            self._pool = MySQLConnectionPoolProvider(
                host=host or os.getenv("MYSQL_HOST", "localhost"),
                port=port or int(os.getenv("MYSQL_PORT", "3306")),
                user=user or os.getenv("MYSQL_USER", ""),
                password=_password,
                database=database or os.getenv("MYSQL_DATABASE", "bcsfuse"),
                pool_size=pool_size,
            )
            logger.info(
                "[MySQLWorkerRegistryStore] Created internal connection pool (fallback mode)"
            )
        else:
            # Preferred: use injected pool
            self._pool = connection_pool
            logger.info(
                "[MySQLWorkerRegistryStore] Using injected connection pool"
            )

        self._schema_initialized = False
        self._schema_lock = threading.Lock()

    def _ensure_schema(self, conn) -> None:
        """Ensure database schema exists (called once per connection).

        Args:
            conn: Database connection from pool.

        Note:
            This method is called on first use, not in __init__.
            Schema initialization is thread-safe due to _schema_lock.
        """
        if self._schema_initialized:
            return

        with self._schema_lock:
            # Double-check after acquiring lock
            if self._schema_initialized:
                return

            cursor = conn.cursor()

            try:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS workers (
                        worker_id VARCHAR(255) PRIMARY KEY,
                        identity_name VARCHAR(255),
                        identity_handle VARCHAR(255),
                        identity_description TEXT,
                        worker_type VARCHAR(50),
                        state VARCHAR(50),
                        availability VARCHAR(50),
                        trust_level VARCHAR(50),
                        capabilities JSON,
                        skills JSON,
                        resources JSON,
                        metadata JSON,
                        domains JSON,
                        responsibilities JSON,
                        constraints JSON,
                        memory_refs JSON,
                        version INT DEFAULT 1,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        INDEX idx_state (state),
                        INDEX idx_availability (availability)
                    )
                """)

                # Run migrations
                self._migrate_schema(conn, cursor)

                self._schema_initialized = True

                logger.info(
                    "[MySQLWorkerRegistryStore] Schema initialized successfully"
                )

            finally:
                cursor.close()

    def _migrate_schema(self, conn, cursor) -> None:
        """Migrate existing table schema (add missing columns for R15 parity).

        R15-PATCH-A: Worker Registry Schema and Interface Parity

        This migration adds missing columns to match root_original SQLite schema:
        - lifecycle_state: Worker lifecycle state (ACTIVE/INACTIVE/DISABLED)
        - active_profile_key: Reference to active profile
        - source_type: Worker source type (API/FILE/REGISTRY)
        - source_ref: Source reference
        - external_id: External ID
        - config: Worker configuration JSON
        - created_by: Creator ID
        - updated_by: Updater ID
        - runtime_state: Denormalized runtime state (optional, for scan performance)

        Migration is IDEMPOTENT (can be run multiple times safely).

        Args:
            conn: Database connection from pool.
            cursor: Database cursor.
        """
        try:
            # R15-PATCH-A: Add lifecycle_state column
            cursor.execute("""
                SELECT COUNT(*)
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = %s
                AND TABLE_NAME = 'workers'
                AND COLUMN_NAME = 'lifecycle_state'
            """, (self._pool.database,))

            if cursor.fetchone()[0] == 0:
                logger.info("[MySQLWorkerRegistryStore] Adding lifecycle_state column (R15-PATCH-A)")
                cursor.execute("""
                    ALTER TABLE workers
                    ADD COLUMN lifecycle_state VARCHAR(50) DEFAULT 'active'
                """)
                logger.info("[MySQLWorkerRegistryStore] lifecycle_state column added successfully")

            # R15-PATCH-A: Add active_profile_key column
            cursor.execute("""
                SELECT COUNT(*)
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = %s
                AND TABLE_NAME = 'workers'
                AND COLUMN_NAME = 'active_profile_key'
            """, (self._pool.database,))

            if cursor.fetchone()[0] == 0:
                logger.info("[MySQLWorkerRegistryStore] Adding active_profile_key column (R15-PATCH-A)")
                cursor.execute("""
                    ALTER TABLE workers
                    ADD COLUMN active_profile_key VARCHAR(255) NULL
                """)
                logger.info("[MySQLWorkerRegistryStore] active_profile_key column added successfully")

            # R15-PATCH-A: Add source_type column
            cursor.execute("""
                SELECT COUNT(*)
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = %s
                AND TABLE_NAME = 'workers'
                AND COLUMN_NAME = 'source_type'
            """, (self._pool.database,))

            if cursor.fetchone()[0] == 0:
                logger.info("[MySQLWorkerRegistryStore] Adding source_type column (R15-PATCH-A)")
                cursor.execute("""
                    ALTER TABLE workers
                    ADD COLUMN source_type VARCHAR(50) DEFAULT 'api'
                """)
                logger.info("[MySQLWorkerRegistryStore] source_type column added successfully")

            # R15-PATCH-A: Add source_ref column
            cursor.execute("""
                SELECT COUNT(*)
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = %s
                AND TABLE_NAME = 'workers'
                AND COLUMN_NAME = 'source_ref'
            """, (self._pool.database,))

            if cursor.fetchone()[0] == 0:
                logger.info("[MySQLWorkerRegistryStore] Adding source_ref column (R15-PATCH-A)")
                cursor.execute("""
                    ALTER TABLE workers
                    ADD COLUMN source_ref VARCHAR(255) NULL
                """)
                logger.info("[MySQLWorkerRegistryStore] source_ref column added successfully")

            # R15-PATCH-A: Add external_id column
            cursor.execute("""
                SELECT COUNT(*)
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = %s
                AND TABLE_NAME = 'workers'
                AND COLUMN_NAME = 'external_id'
            """, (self._pool.database,))

            if cursor.fetchone()[0] == 0:
                logger.info("[MySQLWorkerRegistryStore] Adding external_id column (R15-PATCH-A)")
                cursor.execute("""
                    ALTER TABLE workers
                    ADD COLUMN external_id VARCHAR(255) NULL
                """)
                logger.info("[MySQLWorkerRegistryStore] external_id column added successfully")

            # R15-PATCH-A: Add config column
            cursor.execute("""
                SELECT COUNT(*)
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = %s
                AND TABLE_NAME = 'workers'
                AND COLUMN_NAME = 'config'
            """, (self._pool.database,))

            if cursor.fetchone()[0] == 0:
                logger.info("[MySQLWorkerRegistryStore] Adding config column (R15-PATCH-A)")
                cursor.execute("""
                    ALTER TABLE workers
                    ADD COLUMN config JSON NULL
                """)
                logger.info("[MySQLWorkerRegistryStore] config column added successfully")

            # R15-PATCH-A: Add created_by column
            cursor.execute("""
                SELECT COUNT(*)
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = %s
                AND TABLE_NAME = 'workers'
                AND COLUMN_NAME = 'created_by'
            """, (self._pool.database,))

            if cursor.fetchone()[0] == 0:
                logger.info("[MySQLWorkerRegistryStore] Adding created_by column (R15-PATCH-A)")
                cursor.execute("""
                    ALTER TABLE workers
                    ADD COLUMN created_by VARCHAR(255) NULL
                """)
                logger.info("[MySQLWorkerRegistryStore] created_by column added successfully")

            # R15-PATCH-A: Add updated_by column
            cursor.execute("""
                SELECT COUNT(*)
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = %s
                AND TABLE_NAME = 'workers'
                AND COLUMN_NAME = 'updated_by'
            """, (self._pool.database,))

            if cursor.fetchone()[0] == 0:
                logger.info("[MySQLWorkerRegistryStore] Adding updated_by column (R15-PATCH-A)")
                cursor.execute("""
                    ALTER TABLE workers
                    ADD COLUMN updated_by VARCHAR(255) NULL
                """)
                logger.info("[MySQLWorkerRegistryStore] updated_by column added successfully")

            # R15-PATCH-A: Add runtime_state column (denormalized, optional)
            # This is a denormalized mirror of worker_runtime_state.state for scan performance
            # Canonical source of truth is still worker_runtime_state table
            cursor.execute("""
                SELECT COUNT(*)
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = %s
                AND TABLE_NAME = 'workers'
                AND COLUMN_NAME = 'runtime_state'
            """, (self._pool.database,))

            if cursor.fetchone()[0] == 0:
                logger.info("[MySQLWorkerRegistryStore] Adding runtime_state column (R15-PATCH-A, denormalized)")
                cursor.execute("""
                    ALTER TABLE workers
                    ADD COLUMN runtime_state VARCHAR(50) DEFAULT 'offline'
                """)
                logger.info("[MySQLWorkerRegistryStore] runtime_state column added successfully")

            # R15-PATCH-A: Add indexes for new columns
            self._create_indexes_if_not_exist(conn, cursor)

            # R15-PATCH-A: Backfill lifecycle_state from existing state column (heuristic)
            self._backfill_lifecycle_state(conn, cursor)

            # R15-PATCH-A: Backfill runtime_state from worker_runtime_state table
            self._backfill_runtime_state(conn, cursor)

            # R15-PATCH-A: Backfill active_profile_key from binding table
            self._backfill_active_profile_key(conn, cursor)

            # R15-PATCH-A: Add missing columns for PATCH operation support (legacy)
            self._add_legacy_columns(conn, cursor)

            logger.info("[MySQLWorkerRegistryStore] R15-PATCH-A schema migration completed successfully")

        except Error as e:
            logger.error(
                f"[MySQLWorkerRegistryStore] Schema migration failed (CRITICAL): {e}"
            )
            raise

    def _create_indexes_if_not_exist(self, conn, cursor) -> None:
        """Create indexes for new columns (R15-PATCH-A).

        Args:
            conn: Database connection from pool.
            cursor: Database cursor.
        """
        indexes = [
            ("idx_workers_lifecycle_state", "lifecycle_state"),
            ("idx_workers_source_type", "source_type"),
            ("idx_workers_active_profile_key", "active_profile_key"),
            ("idx_workers_runtime_state", "runtime_state"),
        ]

        for index_name, column_name in indexes:
            try:
                cursor.execute(f"""
                    SELECT COUNT(*)
                    FROM information_schema.STATISTICS
                    WHERE TABLE_SCHEMA = %s
                    AND TABLE_NAME = 'workers'
                    AND INDEX_NAME = %s
                """, (self._pool.database, index_name))

                if cursor.fetchone()[0] == 0:
                    logger.info(f"[MySQLWorkerRegistryStore] Creating index {index_name} (R15-PATCH-A)")
                    cursor.execute(f"""
                        CREATE INDEX {index_name} ON workers({column_name})
                    """)
                    logger.info(f"[MySQLWorkerRegistryStore] Index {index_name} created successfully")

            except Error as e:
                logger.warning(
                    f"[MySQLWorkerRegistryStore] Failed to create index {index_name} (non-critical): {e}"
                )

    def _backfill_lifecycle_state(self, conn, cursor) -> None:
        """Backfill lifecycle_state from existing state column (R15-PATCH-A).

        This is a HEURISTIC migration based on existing state values.
        Manual review is recommended after migration.

        Args:
            conn: Database connection from pool.
            cursor: Database cursor.
        """
        try:
            # Check if there are rows with NULL or empty lifecycle_state
            cursor.execute("""
                SELECT COUNT(*)
                FROM workers
                WHERE lifecycle_state IS NULL OR lifecycle_state = ''
            """)

            null_count = cursor.fetchone()[0]

            if null_count > 0:
                logger.info(
                    f"[MySQLWorkerRegistryStore] Backfilling lifecycle_state for {null_count} workers (R15-PATCH-A)"
                )

                # Heuristic migration from existing state column
                # DO NOT infer lifecycle_state from availability or runtime_state
                # Default to 'active' for all existing workers
                cursor.execute("""
                    UPDATE workers
                    SET lifecycle_state = 'active'
                    WHERE lifecycle_state IS NULL OR lifecycle_state = ''
                """)

                logger.info(
                    f"[MySQLWorkerRegistryStore] Backfilled lifecycle_state for {null_count} workers"
                )

        except Error as e:
            logger.warning(
                f"[MySQLWorkerRegistryStore] Failed to backfill lifecycle_state (non-critical): {e}"
            )

    def _backfill_runtime_state(self, conn, cursor) -> None:
        """Backfill runtime_state from worker_runtime_state table (R15-PATCH-A).

        The worker_runtime_state table is the CANONICAL source of truth for runtime_state.
        This denormalized column is only for scan performance.

        Args:
            conn: Database connection from pool.
            cursor: Database cursor.
        """
        try:
            # Check if worker_runtime_state table exists
            cursor.execute("""
                SELECT COUNT(*)
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = %s
                AND TABLE_NAME = 'worker_runtime_state'
            """, (self._pool.database,))

            if cursor.fetchone()[0] == 0:
                logger.info(
                    "[MySQLWorkerRegistryStore] worker_runtime_state table not found, skipping backfill"
                )
                return

            # Check if there are rows with NULL or empty runtime_state
            cursor.execute("""
                SELECT COUNT(*)
                FROM workers
                WHERE runtime_state IS NULL OR runtime_state = ''
            """)

            null_count = cursor.fetchone()[0]

            if null_count > 0:
                logger.info(
                    f"[MySQLWorkerRegistryStore] Backfilling runtime_state for {null_count} workers (R15-PATCH-A)"
                )

                # Backfill from worker_runtime_state table
                cursor.execute("""
                    UPDATE workers w
                    LEFT JOIN worker_runtime_state wrs ON w.worker_id = wrs.worker_id
                    SET w.runtime_state = COALESCE(wrs.state, 'offline')
                    WHERE w.runtime_state IS NULL OR w.runtime_state = ''
                """)

                logger.info(
                    f"[MySQLWorkerRegistryStore] Backfilled runtime_state for {null_count} workers"
                )

        except Error as e:
            logger.warning(
                f"[MySQLWorkerRegistryStore] Failed to backfill runtime_state (non-critical): {e}"
            )

    def _backfill_active_profile_key(self, conn, cursor) -> None:
        """Backfill active_profile_key from binding table (R15-PATCH-A).

        Args:
            conn: Database connection from pool.
            cursor: Database cursor.
        """
        try:
            # Check if bcsfuse_worker_profile_bindings table exists
            cursor.execute("""
                SELECT COUNT(*)
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = %s
                AND TABLE_NAME = 'bcsfuse_worker_profile_bindings'
            """, (self._pool.database,))

            if cursor.fetchone()[0] == 0:
                logger.info(
                    "[MySQLWorkerRegistryStore] bcsfuse_worker_profile_bindings table not found, skipping backfill"
                )
                return

            # Check if there are rows with NULL active_profile_key
            cursor.execute("""
                SELECT COUNT(*)
                FROM workers
                WHERE active_profile_key IS NULL
            """)

            null_count = cursor.fetchone()[0]

            if null_count > 0:
                logger.info(
                    f"[MySQLWorkerRegistryStore] Backfilling active_profile_key for {null_count} workers (R15-PATCH-A)"
                )

                # Backfill from binding table
                cursor.execute("""
                    UPDATE workers w
                    LEFT JOIN (
                        SELECT worker_id, profile_key
                        FROM bcsfuse_worker_profile_bindings
                        WHERE is_active = 1
                    ) b ON w.worker_id = b.worker_id
                    SET w.active_profile_key = b.profile_key
                    WHERE w.active_profile_key IS NULL
                """)

                logger.info(
                    f"[MySQLWorkerRegistryStore] Backfilled active_profile_key for {null_count} workers"
                )

        except Error as e:
            logger.warning(
                f"[MySQLWorkerRegistryStore] Failed to backfill active_profile_key (non-critical): {e}"
            )

    def _add_legacy_columns(self, conn, cursor) -> None:
        """Add legacy columns for PATCH operation support (R12-6 compatibility).

        Args:
            conn: Database connection from pool.
            cursor: Database cursor.
        """
        # Check if domains column exists
        cursor.execute("""
            SELECT COUNT(*)
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s
            AND TABLE_NAME = 'workers'
            AND COLUMN_NAME = 'domains'
        """, (self._pool.database,))

        if cursor.fetchone()[0] == 0:
            # Add missing columns for PATCH operation support
            columns_to_add = [
                ("domains", "JSON"),
                ("responsibilities", "JSON"),
                ("constraints", "JSON"),
                ("memory_refs", "JSON"),
            ]

            for column_name, column_type in columns_to_add:
                try:
                    cursor.execute(f"""
                        ALTER TABLE workers
                        ADD COLUMN {column_name} {column_type}
                    """)
                except Error as e:
                    # Column might already exist, ignore error
                    if e.errno != 1060:  # 1060 = Duplicate column name
                        raise

    def register(self, worker_id: str, worker_info: dict) -> bool:
        """Register a new worker (thread-safe with connection pool)."""
        start_time = datetime.now()
        thread_id = threading.current_thread().ident

        conn = self._pool.get_connection()
        try:
            self._ensure_schema(conn)

            cursor = conn.cursor()

            try:
                # Canonicalize enum values for storage
                availability_value = _canonicalize_enum_for_storage(
                    worker_info.get("availability", "private")
                )
                trust_level_value = _canonicalize_enum_for_storage(
                    worker_info.get("trust_level", "guarded")
                )

                cursor.execute("""
                    INSERT INTO workers (
                        worker_id, identity_name, identity_handle, identity_description,
                        worker_type, state, availability, trust_level,
                        capabilities, skills, resources, metadata,
                        active_profile_key, lifecycle_state, source_type
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    worker_id,
                    worker_info.get("identity", {}).get("name", ""),
                    worker_info.get("identity", {}).get("handle", ""),
                    worker_info.get("identity", {}).get("description", ""),
                    worker_info.get("worker_type", "bot"),
                    worker_info.get("state", "created"),
                    availability_value,
                    trust_level_value,
                    json.dumps(worker_info.get("capabilities", [])),
                    json.dumps(worker_info.get("skills", [])),
                    json.dumps(worker_info.get("resources", [])),
                    json.dumps(worker_info.get("metadata", {})),
                    worker_info.get("active_profile_key"),
                    worker_info.get("lifecycle_state", "inactive"),
                    worker_info.get("source_type", "api"),
                ))

                elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000

                logger.debug(
                    f"[MySQLWorkerRegistryStore] register() completed: "
                    f"worker_id={worker_id}, thread_id={thread_id}, elapsed_ms={elapsed_ms:.2f}"
                )

                return True

            except Error as e:
                if e.errno == 1062:  # Duplicate entry
                    return False
                raise
            finally:
                cursor.close()

        finally:
            conn.close()  # Return connection to pool

    def get(self, worker_id: str, return_dict: bool = False) -> Optional[Union["Worker", dict]]:
        """
        Get worker by ID (R15-PATCH-A enhanced).

        Args:
            worker_id: Worker ID
            return_dict: Return dict instead of Worker model (backward compatibility)

        Returns:
            Worker model or dict, or None if not found
        """
        start_time = datetime.now()
        thread_id = threading.current_thread().ident

        conn = self._pool.get_connection()
        try:
            self._ensure_schema(conn)

            cursor = conn.cursor(dictionary=True)

            try:
                cursor.execute("SELECT * FROM workers WHERE worker_id = %s", (worker_id,))
                row = cursor.fetchone()

                elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000

                if not row:
                    logger.debug(
                        f"[MySQLWorkerRegistryStore] get() not found: "
                        f"worker_id={worker_id}, thread_id={thread_id}, elapsed_ms={elapsed_ms:.2f}"
                    )
                    return None

                logger.debug(
                    f"[MySQLWorkerRegistryStore] get() completed: "
                    f"worker_id={worker_id}, thread_id={thread_id}, elapsed_ms={elapsed_ms:.2f}"
                )

                if return_dict:
                    # Backward compatibility: return dict
                    return {
                        "worker_id": row["worker_id"],
                        "identity": {
                            "name": row["identity_name"],
                            "handle": row["identity_handle"],
                            "description": row["identity_description"],
                        },
                        "worker_type": row.get("worker_type"),
                        "lifecycle_state": row.get("lifecycle_state"),
                        "runtime_state": row.get("runtime_state"),
                        "state": row.get("state"),
                        "availability": row.get("availability"),
                        "trust_level": row.get("trust_level"),
                        "active_profile_key": row.get("active_profile_key"),
                        "source_type": row.get("source_type"),
                        "capabilities": json.loads(row["capabilities"]) if row.get("capabilities") else [],
                        "skills": json.loads(row["skills"]) if row.get("skills") else [],
                        "resources": json.loads(row["resources"]) if row.get("resources") else [],
                        "domains": json.loads(row["domains"]) if row.get("domains") else [],
                        "metadata": json.loads(row["metadata"]) if row.get("metadata") else {},
                        "version": row.get("version"),
                        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
                        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
                    }
                else:
                    # Return Worker model (R15-PATCH-A)
                    return self._row_to_worker(row)

            finally:
                cursor.close()

        finally:
            conn.close()  # Return connection to pool

    def exists(self, worker_id: str) -> bool:
        """
        Check if worker exists (R15-PATCH-A).

        Args:
            worker_id: Worker ID

        Returns:
            True if worker exists, False otherwise
        """
        start_time = datetime.now()
        thread_id = threading.current_thread().ident

        conn = self._pool.get_connection()
        try:
            cursor = conn.cursor()

            try:
                cursor.execute("SELECT 1 FROM workers WHERE worker_id = %s LIMIT 1", (worker_id,))
                result = cursor.fetchone()

                elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000

                logger.debug(
                    f"[MySQLWorkerRegistryStore] exists() completed: "
                    f"worker_id={worker_id}, exists={result is not None}, "
                    f"thread_id={thread_id}, elapsed_ms={elapsed_ms:.2f}"
                )

                return result is not None

            finally:
                cursor.close()

        finally:
            conn.close()

    def count(self, lifecycle_states: Optional[List["WorkerLifecycleState"]] = None) -> int:
        """
        Count workers with optional lifecycle filter (R15-PATCH-A).

        Args:
            lifecycle_states: Filter by lifecycle states

        Returns:
            Count of workers
        """
        from src.domain.models.worker_lifecycle_state import WorkerLifecycleState

        start_time = datetime.now()
        thread_id = threading.current_thread().ident

        conn = self._pool.get_connection()
        try:
            cursor = conn.cursor()

            try:
                query = "SELECT COUNT(*) FROM workers WHERE 1=1"
                params = []

                if lifecycle_states:
                    lifecycle_values = [ls.value for ls in lifecycle_states]
                    query += f" AND lifecycle_state IN ({','.join(['%s'] * len(lifecycle_values))})"
                    params.extend(lifecycle_values)

                cursor.execute(query, params)
                result = cursor.fetchone()

                elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000

                count = result[0] if result else 0

                logger.debug(
                    f"[MySQLWorkerRegistryStore] count() completed: "
                    f"count={count}, filters=lifecycle_states={lifecycle_states}, "
                    f"thread_id={thread_id}, elapsed_ms={elapsed_ms:.2f}"
                )

                return count

            finally:
                cursor.close()

        finally:
            conn.close()

    def update(self, worker) -> "Worker":
        """
        Update worker (thread-safe with connection pool).

        Args:
            worker: Worker object to update

        Returns:
            Updated Worker object

        Raises:
            WorkerNotFoundException: Worker not found
            ValueError: Version conflict
        """
        from src.domain.models.worker import Worker

        if not isinstance(worker, Worker):
            raise TypeError(f"Expected Worker object, got {type(worker)}")

        worker_id = worker.id

        # Check if worker exists and version matches (optimistic locking)
        existing = self.get_by_id(worker_id)
        if existing is None:
            from src.domain.exceptions import WorkerNotFoundException
            raise WorkerNotFoundException(worker_id)

        if worker.version != existing.version:
            raise ValueError(
                f"Version conflict: expected {existing.version}, got {worker.version}"
            )

        # Convert worker to dict for database update
        worker_dict = worker.model_dump() if hasattr(worker, 'model_dump') else worker.dict()

        start_time = datetime.now()
        thread_id = threading.current_thread().ident

        conn = self._pool.get_connection()
        try:
            self._ensure_schema(conn)

            cursor = conn.cursor()

            try:
                # Build dynamic update
                set_clauses = []
                values = []

                # Map worker_dict to database columns
                field_mappings = {
                    "worker_type": "worker_type",
                    "lifecycle_state": "lifecycle_state",
                    "active_profile_key": "active_profile_key",
                    "source_type": "source_type",
                    "source_ref": "source_ref",
                    "external_id": "external_id",
                    "version": "version",
                }

                json_fields = ["capabilities", "skills", "resources", "metadata", "domains",
                              "responsibilities", "constraints", "memory_refs", "config"]

                # Handle simple fields
                for dict_key, db_column in field_mappings.items():
                    if dict_key in worker_dict:
                        set_clauses.append(f"{db_column} = %s")
                        values.append(worker_dict[dict_key])

                # Handle identity fields
                if "identity" in worker_dict:
                    identity = worker_dict["identity"]
                    if isinstance(identity, dict):
                        set_clauses.append("identity_name = %s")
                        values.append(identity.get("name", ""))
                        set_clauses.append("identity_handle = %s")
                        values.append(identity.get("handle", ""))
                        set_clauses.append("identity_description = %s")
                        values.append(identity.get("description", ""))
                        # Note: identity_title column does not exist in database schema

                # Handle state fields
                if "state" in worker_dict:
                    state = worker_dict["state"]
                    if isinstance(state, dict):
                        if "availability" in state:
                            # Canonicalize enum value for storage
                            avail_value = _canonicalize_enum_for_storage(state["availability"])
                            set_clauses.append("availability = %s")
                            values.append(avail_value)
                        if "trust_level" in state:
                            # Canonicalize enum value for storage
                            trust_value = _canonicalize_enum_for_storage(state["trust_level"])
                            set_clauses.append("trust_level = %s")
                            values.append(trust_value)
                        if "runtime_state" in state:
                            # Canonicalize enum value for storage
                            runtime_value = _canonicalize_enum_for_storage(state["runtime_state"])
                            set_clauses.append("runtime_state = %s")
                            values.append(runtime_value)

                # Handle JSON fields
                for json_field in json_fields:
                    if json_field in worker_dict:
                        set_clauses.append(f"{json_field} = %s")
                        values.append(json.dumps(worker_dict[json_field]))

                if not set_clauses:
                    return worker

                values.append(worker_id)

                # Increment version
                set_clauses.append("version = version + 1")

                cursor.execute(f"""
                    UPDATE workers
                    SET {', '.join(set_clauses)}
                    WHERE worker_id = %s
                """, values)

                elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000

                logger.debug(
                    f"[MySQLWorkerRegistryStore] update() completed: "
                    f"worker_id={worker_id}, thread_id={thread_id}, elapsed_ms={elapsed_ms:.2f}"
                )

                # Return updated worker with incremented version
                updated_worker = worker.model_copy(deep=True)
                updated_worker.version += 1
                return updated_worker

            finally:
                cursor.close()

        finally:
            conn.close()  # Return connection to pool

    def delete(self, worker_id: str) -> bool:
        """Delete worker (thread-safe with connection pool)."""
        start_time = datetime.now()
        thread_id = threading.current_thread().ident

        conn = self._pool.get_connection()
        try:
            self._ensure_schema(conn)

            cursor = conn.cursor()

            try:
                cursor.execute("DELETE FROM workers WHERE worker_id = %s", (worker_id,))

                elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000

                logger.debug(
                    f"[MySQLWorkerRegistryStore] delete() completed: "
                    f"worker_id={worker_id}, thread_id={thread_id}, elapsed_ms={elapsed_ms:.2f}"
                )

                return cursor.rowcount > 0

            finally:
                cursor.close()

        finally:
            conn.close()  # Return connection to pool

    def list_all(self) -> List[dict]:
        """List all workers (thread-safe with connection pool)."""
        start_time = datetime.now()
        thread_id = threading.current_thread().ident

        conn = self._pool.get_connection()
        try:
            self._ensure_schema(conn)

            cursor = conn.cursor(dictionary=True)

            try:
                cursor.execute("SELECT * FROM workers ORDER BY created_at DESC")
                rows = cursor.fetchall()

                elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000

                logger.debug(
                    f"[MySQLWorkerRegistryStore] list_all() completed: "
                    f"count={len(rows)}, thread_id={thread_id}, elapsed_ms={elapsed_ms:.2f}"
                )

                return [
                    {
                        "worker_id": row["worker_id"],
                        "identity": {
                            "name": row["identity_name"],
                            "handle": row["identity_handle"],
                            "description": row["identity_description"],
                        },
                        "worker_type": row["worker_type"],
                        "state": row["state"],
                        "availability": row["availability"],
                        "trust_level": row["trust_level"],
                        "capabilities": json.loads(row["capabilities"]) if row["capabilities"] else [],
                        "skills": json.loads(row["skills"]) if row["skills"] else [],
                        "resources": json.loads(row["resources"]) if row["resources"] else [],
                        "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                        "version": row["version"],
                        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
                    }
                    for row in rows
                ]

            finally:
                cursor.close()

        finally:
            conn.close()  # Return connection to pool

    def find_by_capability(self, capability: str) -> List[dict]:
        """Find workers by capability (thread-safe with connection pool)."""
        start_time = datetime.now()
        thread_id = threading.current_thread().ident

        conn = self._pool.get_connection()
        try:
            self._ensure_schema(conn)

            cursor = conn.cursor(dictionary=True)

            try:
                cursor.execute("""
                    SELECT * FROM workers
                    WHERE JSON_CONTAINS(capabilities, JSON_QUOTE(%s))
                    ORDER BY created_at DESC
                """, (capability,))

                rows = cursor.fetchall()

                elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000

                logger.debug(
                    f"[MySQLWorkerRegistryStore] find_by_capability() completed: "
                    f"capability={capability}, count={len(rows)}, thread_id={thread_id}, elapsed_ms={elapsed_ms:.2f}"
                )

                return [
                    {
                        "worker_id": row["worker_id"],
                        "identity": {
                            "name": row["identity_name"],
                            "handle": row["identity_handle"],
                            "description": row["identity_description"],
                        },
                        "worker_type": row["worker_type"],
                        "state": row["state"],
                        "availability": row["availability"],
                        "trust_level": row["trust_level"],
                        "capabilities": json.loads(row["capabilities"]) if row["capabilities"] else [],
                        "skills": json.loads(row["skills"]) if row["skills"] else [],
                        "resources": json.loads(row["resources"]) if row["resources"] else [],
                        "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                        "version": row["version"],
                        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
                    }
                    for row in rows
                ]

            finally:
                cursor.close()

        finally:
            conn.close()  # Return connection to pool

    # ========================================
    # Compatibility methods for OSS routes
    # ========================================

    def get_by_id(self, worker_id: str) -> Optional[dict]:
        """Alias for get() method for OSS routes compatibility."""
        return self.get(worker_id)

    def get_worker(self, worker_id: str) -> Optional[dict]:
        """Alias for get() method for OSS routes compatibility."""
        return self.get(worker_id)

    def list(
        self,
        lifecycle_states: Optional[List["WorkerLifecycleState"]] = None,
        source_types: Optional[List["WorkerSourceType"]] = None,
        domains: Optional[List[str]] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        return_dicts: bool = False,
    ) -> List[Union["Worker", dict]]:
        """
        List workers with filters (R15-PATCH-A enhanced).

        Args:
            lifecycle_states: Filter by lifecycle states (ACTIVE/INACTIVE/DISABLED)
            source_types: Filter by source types (API/FILE/IMPORT)
            domains: Filter by domains (JSON contains)
            limit: Maximum number of results
            offset: Offset for pagination
            return_dicts: Return dicts instead of Worker models (backward compatibility)

        Returns:
            List of Worker models (or dicts if return_dicts=True)

        Note:
            This method supports filtering by lifecycle_states, which is CRITICAL for
            RegistryWorkerProfileSource.scan() to work correctly.

            Without this filter, scan() returns ALL workers including INACTIVE/DISABLED,
            causing G5 retrieval to include unwanted workers.
        """
        from src.domain.models.worker_lifecycle_state import WorkerLifecycleState
        from src.domain.models.worker_source_info import WorkerSourceType

        start_time = datetime.now()
        thread_id = threading.current_thread().ident

        conn = self._pool.get_connection()
        try:
            self._ensure_schema(conn)

            cursor = conn.cursor(dictionary=True)

            try:
                query = "SELECT * FROM workers WHERE 1=1"
                params = []

                # Filter by lifecycle_states (R15-PATCH-A CRITICAL)
                if lifecycle_states:
                    lifecycle_values = [ls.value for ls in lifecycle_states]
                    query += f" AND lifecycle_state IN ({','.join(['%s'] * len(lifecycle_values))})"
                    params.extend(lifecycle_values)

                # Filter by source_types (R15-PATCH-A)
                if source_types:
                    source_values = [st.value for st in source_types]
                    query += f" AND source_type IN ({','.join(['%s'] * len(source_values))})"
                    params.extend(source_values)

                # Filter by domains (JSON contains)
                if domains:
                    for domain in domains:
                        query += " AND JSON_CONTAINS(domains, %s)"
                        params.append(f'"{domain}"')

                # Order and pagination
                query += " ORDER BY created_at DESC"

                if limit is not None:
                    query += " LIMIT %s"
                    params.append(limit)
                if offset is not None:
                    query += " OFFSET %s"
                    params.append(offset)

                cursor.execute(query, params)
                rows = cursor.fetchall()

                elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000

                logger.debug(
                    f"[MySQLWorkerRegistryStore] list() completed: "
                    f"count={len(rows)}, filters=lifecycle_states={lifecycle_states}, "
                    f"thread_id={thread_id}, elapsed_ms={elapsed_ms:.2f}"
                )

                if return_dicts:
                    # Backward compatibility: return dicts
                    return [
                        {
                            "worker_id": row["worker_id"],
                            "identity": {
                                "name": row["identity_name"],
                                "handle": row["identity_handle"],
                                "description": row["identity_description"],
                            },
                            "worker_type": row.get("worker_type"),
                            "lifecycle_state": row.get("lifecycle_state"),
                            "runtime_state": row.get("runtime_state"),
                            "state": row.get("state"),
                            "availability": row.get("availability"),
                            "trust_level": row.get("trust_level"),
                            "active_profile_key": row.get("active_profile_key"),
                            "source_type": row.get("source_type"),
                            "capabilities": json.loads(row["capabilities"]) if row.get("capabilities") else [],
                            "skills": json.loads(row["skills"]) if row.get("skills") else [],
                            "resources": json.loads(row["resources"]) if row.get("resources") else [],
                            "domains": json.loads(row["domains"]) if row.get("domains") else [],
                            "metadata": json.loads(row["metadata"]) if row.get("metadata") else {},
                            "version": row.get("version"),
                            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
                            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
                        }
                        for row in rows
                    ]
                else:
                    # Return Worker models (R15-PATCH-A)
                    return [self._row_to_worker(row) for row in rows]

            finally:
                cursor.close()

        finally:
            conn.close()  # Return connection to pool

    def list_workers(self) -> List[dict]:
        """Alias for list_all() method for OSS routes compatibility."""
        return self.list_all()

    def _row_to_worker(self, row: dict) -> "Worker":
        """
        Convert MySQL row dict to Worker domain model (R15-PATCH-A).

        This method handles the semantic separation of lifecycle_state, runtime_state, and availability:
        - lifecycle_state: Management layer (ACTIVE/INACTIVE/DISABLED)
        - runtime_state: Operational layer (ONLINE/OFFLINE)
        - availability: Visibility layer (PRIVATE/PROTECTED/PUBLIC)

        Args:
            row: MySQL row dict from cursor.fetchone() or cursor.fetchall()

        Returns:
            Worker domain model instance

        Note:
            - JSON fields are safely parsed (bad JSON -> default value, with warning)
            - Enum parse failures -> default value, with warning
            - runtime_state: Prioritizes worker_runtime_state table, falls back to denormalized column
            - Does NOT output raw profile content or secrets
        """
        from src.domain.models.worker import (
            Worker,
            WorkerType,
            WorkerIdentity,
            WorkerState,
            Availability,
            TrustLevel,
            Capability,
            CapabilityLevel,
            Constraint,
            ConstraintKind,
            ConstraintSeverity,
            SkillRef,
            SkillSource,
            ResourceRef,
            ResourceKind,
            ResourceAccess,
            PerformanceStats,
        )
        from src.domain.models.worker_lifecycle_state import WorkerLifecycleState
        from src.domain.models.worker_runtime_state import WorkerRuntimeState
        from src.domain.models.worker_source_info import WorkerSourceType

        # Parse availability (visibility layer) with tolerant parsing
        availability = Availability.PRIVATE
        if row.get("availability"):
            try:
                # Normalize legacy corrupted values like "Availability.PROTECTED"
                normalized_avail = _normalize_enum_string(row["availability"])
                if normalized_avail:
                    availability = Availability(normalized_avail)
            except ValueError as e:
                logger.warning(
                    f"[MySQLWorkerRegistryStore] Invalid availability value '{row.get('availability')}' "
                    f"(normalized: '{normalized_avail}'), using default PRIVATE: {e}"
                )
                availability = Availability.PRIVATE

        # Parse trust_level with tolerant parsing
        trust_level = TrustLevel.GUARDED
        if row.get("trust_level"):
            try:
                # Normalize legacy corrupted values like "TrustLevel.UNVERIFIED"
                normalized_trust = _normalize_enum_string(row["trust_level"])
                if normalized_trust:
                    trust_level = TrustLevel(normalized_trust)
            except ValueError as e:
                logger.warning(
                    f"[MySQLWorkerRegistryStore] Invalid trust_level value '{row.get('trust_level')}' "
                    f"(normalized: '{normalized_trust}'), using default GUARDED: {e}"
                )
                trust_level = TrustLevel.GUARDED

        # Parse lifecycle_state (management layer) - R15-PATCH-A
        lifecycle_state = WorkerLifecycleState.ACTIVE
        if row.get("lifecycle_state"):
            try:
                lifecycle_state = WorkerLifecycleState(row["lifecycle_state"])
            except ValueError as e:
                logger.warning(
                    f"[MySQLWorkerRegistryStore] Invalid lifecycle_state value '{row.get('lifecycle_state')}', "
                    f"using default ACTIVE: {e}"
                )
                lifecycle_state = WorkerLifecycleState.ACTIVE

        # Parse runtime_state (operational layer) - R15-PATCH-A
        # Priority: worker_runtime_state table > denormalized runtime_state column > default OFFLINE
        runtime_state = WorkerRuntimeState.OFFLINE
        if row.get("runtime_state"):
            try:
                runtime_state = WorkerRuntimeState(row["runtime_state"])
            except ValueError as e:
                logger.warning(
                    f"[MySQLWorkerRegistryStore] Invalid runtime_state value '{row.get('runtime_state')}', "
                    f"using default OFFLINE: {e}"
                )
                runtime_state = WorkerRuntimeState.OFFLINE

        # Parse worker_type
        worker_type = WorkerType.BOT
        if row.get("worker_type"):
            try:
                worker_type = WorkerType(row["worker_type"])
            except ValueError as e:
                logger.warning(
                    f"[MySQLWorkerRegistryStore] Invalid worker_type value '{row.get('worker_type')}', "
                    f"using default BOT: {e}"
                )
                worker_type = WorkerType.BOT

        # Parse source_type - R15-PATCH-A
        source_type = WorkerSourceType.API
        if row.get("source_type"):
            try:
                source_type = WorkerSourceType(row["source_type"])
            except ValueError as e:
                logger.warning(
                    f"[MySQLWorkerRegistryStore] Invalid source_type value '{row.get('source_type')}', "
                    f"using default API: {e}"
                )
                source_type = WorkerSourceType.API

        # Safe JSON parsing helper
        def safe_json_parse(json_str: str, field_name: str, default_value):
            """Safely parse JSON string, return default on failure."""
            if not json_str:
                return default_value
            try:
                return json.loads(json_str)
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(
                    f"[MySQLWorkerRegistryStore] Failed to parse {field_name} JSON: {e}, using default"
                )
                return default_value

        # Parse JSON fields
        capabilities_data = safe_json_parse(row.get("capabilities"), "capabilities", [])
        skills_data = safe_json_parse(row.get("skills"), "skills", [])
        resources_data = safe_json_parse(row.get("resources"), "resources", [])
        domains = safe_json_parse(row.get("domains"), "domains", [])
        responsibilities = safe_json_parse(row.get("responsibilities"), "responsibilities", [])
        constraints_data = safe_json_parse(row.get("constraints"), "constraints", [])
        memory_refs = safe_json_parse(row.get("memory_refs"), "memory_refs", [])
        metadata = safe_json_parse(row.get("metadata"), "metadata", {})
        config_data = safe_json_parse(row.get("config"), "config", {})

        # Parse capabilities
        capabilities = []
        for cap_data in capabilities_data:
            try:
                if isinstance(cap_data, dict):
                    level = CapabilityLevel.INTERMEDIATE
                    if cap_data.get("level"):
                        try:
                            level = CapabilityLevel(cap_data["level"])
                        except ValueError:
                            level = CapabilityLevel.INTERMEDIATE

                    capabilities.append(Capability(
                        name=cap_data.get("name", ""),
                        level=level,
                        evidence_refs=cap_data.get("evidence_refs", [])
                    ))
                elif isinstance(cap_data, str):
                    # Simple string capability (backward compatibility)
                    capabilities.append(Capability(
                        name=cap_data,
                        level=CapabilityLevel.INTERMEDIATE,
                        evidence_refs=[]
                    ))
            except Exception as e:
                logger.warning(
                    f"[MySQLWorkerRegistryStore] Failed to parse capability: {e}"
                )

        # Parse skills
        skills = []
        for skill_data in skills_data:
            try:
                if isinstance(skill_data, dict):
                    source = SkillSource.MANAGED
                    if skill_data.get("source"):
                        try:
                            source = SkillSource(skill_data["source"])
                        except ValueError:
                            source = SkillSource.MANAGED

                    skills.append(SkillRef(
                        name=skill_data.get("name", ""),
                        source=source,
                        description=skill_data.get("description")
                    ))
                elif isinstance(skill_data, str):
                    # Simple string skill (backward compatibility)
                    skills.append(SkillRef(
                        name=skill_data,
                        source=SkillSource.MANAGED,
                        description=None
                    ))
            except Exception as e:
                logger.warning(
                    f"[MySQLWorkerRegistryStore] Failed to parse skill: {e}"
                )

        # Parse resources
        resources = []
        for res_data in resources_data:
            try:
                if isinstance(res_data, dict):
                    kind = ResourceKind.FILE
                    if res_data.get("kind"):
                        try:
                            kind = ResourceKind(res_data["kind"])
                        except ValueError:
                            kind = ResourceKind.FILE

                    access = ResourceAccess.READ
                    if res_data.get("access"):
                        try:
                            access = ResourceAccess(res_data["access"])
                        except ValueError:
                            access = ResourceAccess.READ

                    resources.append(ResourceRef(
                        id=res_data.get("id", ""),
                        kind=kind,
                        name=res_data.get("name", ""),
                        description=res_data.get("description"),
                        uri=res_data.get("uri"),
                        access=access,
                        owner=res_data.get("owner"),
                        tags=res_data.get("tags", [])
                    ))
            except Exception as e:
                logger.warning(
                    f"[MySQLWorkerRegistryStore] Failed to parse resource: {e}"
                )

        # Parse constraints
        constraints = []
        for const_data in constraints_data:
            try:
                if isinstance(const_data, dict):
                    kind = ConstraintKind.POLICY
                    if const_data.get("kind"):
                        try:
                            kind = ConstraintKind(const_data["kind"])
                        except ValueError:
                            kind = ConstraintKind.POLICY

                    severity = ConstraintSeverity.MEDIUM
                    if const_data.get("severity"):
                        try:
                            severity = ConstraintSeverity(const_data["severity"])
                        except ValueError:
                            severity = ConstraintSeverity.MEDIUM

                    constraints.append(Constraint(
                        kind=kind,
                        rule=const_data.get("rule", ""),
                        severity=severity
                    ))
            except Exception as e:
                logger.warning(
                    f"[MySQLWorkerRegistryStore] Failed to parse constraint: {e}"
                )

        # Build WorkerState
        state = WorkerState(
            availability=availability,
            trust_level=trust_level,
            runtime_state=runtime_state,
            current_load=row.get("current_load"),
            last_seen_at=row.get("last_seen_at")
        )

        # Build WorkerIdentity
        identity = WorkerIdentity(
            name=row.get("identity_name", ""),
            handle=row.get("identity_handle", row.get("worker_id", "")),
            title=row.get("identity_title"),
            owner_team=row.get("identity_owner_team"),
            description=row.get("identity_description")
        )

        # Build Worker
        return Worker(
            id=row["worker_id"],
            type=worker_type,
            identity=identity,
            responsibilities=responsibilities,
            domains=domains,
            capabilities=capabilities,
            constraints=constraints,
            skills=skills,
            resources=resources,
            memory_refs=memory_refs,
            state=state,
            performance_stats=PerformanceStats(),  # Default empty stats
            lifecycle_state=lifecycle_state,
            source_type=source_type,
            source_ref=row.get("source_ref"),
            external_id=row.get("external_id"),
            active_profile_key=row.get("active_profile_key"),
            version=row.get("version", 1),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
            created_by=row.get("created_by"),
            updated_by=row.get("updated_by")
        )

    def create(self, worker) -> Optional[dict]:
        """Create worker from Worker object (OSS routes compatibility)."""
        # Extract worker info from Worker object
        worker_id = worker.id if hasattr(worker, 'id') else None
        if not worker_id:
            return None

        # Extract availability and trust_level with enum canonicalization
        availability = "private"  # default
        trust_level = "guarded"  # default
        if hasattr(worker, 'state') and worker.state:
            if hasattr(worker.state, 'availability'):
                availability = _canonicalize_enum_for_storage(worker.state.availability) or "private"
            if hasattr(worker.state, 'trust_level'):
                trust_level = _canonicalize_enum_for_storage(worker.state.trust_level) or "guarded"

        worker_info = {
            "identity": {
                "name": worker.identity.name if hasattr(worker, 'identity') and worker.identity else "",
                "handle": worker.identity.handle if hasattr(worker, 'identity') and worker.identity else worker_id,
                "description": worker.identity.description if hasattr(worker, 'identity') and worker.identity else "",
            },
            "worker_type": str(worker.type.value) if hasattr(worker, 'type') and hasattr(worker.type, 'value') else str(worker.type) if hasattr(worker, 'type') else "bot",
            "state": "active",
            "availability": availability,
            "trust_level": trust_level,
            "capabilities": [str(cap) for cap in worker.capabilities] if hasattr(worker, 'capabilities') else [],
            "skills": [str(skill) for skill in worker.skills] if hasattr(worker, 'skills') else [],
            "resources": [str(res) for res in worker.resources] if hasattr(worker, 'resources') else [],
            "metadata": dict(worker.metadata) if hasattr(worker, 'metadata') else {},
            "active_profile_key": worker.active_profile_key if hasattr(worker, 'active_profile_key') else None,
            "lifecycle_state": str(worker.lifecycle_state.value) if hasattr(worker, 'lifecycle_state') and hasattr(worker.lifecycle_state, 'value') else str(worker.lifecycle_state) if hasattr(worker, 'lifecycle_state') else "inactive",
            "source_type": str(worker.source_type.value) if hasattr(worker, 'source_type') and hasattr(worker.source_type, 'value') else str(worker.source_type) if hasattr(worker, 'source_type') else "api",
        }

        success = self.register(worker_id, worker_info)
        if success:
            return self.get(worker_id)
        return None

    def update_lifecycle_state(
        self,
        worker_id: str,
        lifecycle_state,
        version: Optional[int] = None
    ) -> Optional[Union["Worker", dict]]:
        """
        Update worker lifecycle state (R15-PATCH-A fixed).

        CRITICAL SEMANTIC SEPARATION:
        - lifecycle_state (management layer): ACTIVE/INACTIVE/DISABLED
        - runtime_state (operational layer): ONLINE/OFFLINE
        - availability (visibility layer): PRIVATE/PROTECTED/PUBLIC

        This method ONLY updates lifecycle_state column.
        DO NOT confuse lifecycle_state with availability or runtime_state!

        Constraints enforced by WorkerRuntimeStateService:
        - lifecycle_state IN [INACTIVE, DISABLED] ⇒ runtime_state MUST be OFFLINE
        - lifecycle_state = ACTIVE ⇒ runtime_state CAN be ONLINE or OFFLINE

        Args:
            worker_id: Worker ID
            lifecycle_state: WorkerLifecycleState enum value
            version: Optional version for optimistic locking (not implemented yet)

        Returns:
            Updated Worker model or dict, or None if failed
        """
        from src.domain.models.worker_lifecycle_state import WorkerLifecycleState

        start_time = datetime.now()
        thread_id = threading.current_thread().ident

        # Convert to enum value if string
        if isinstance(lifecycle_state, str):
            try:
                lifecycle_state = WorkerLifecycleState(lifecycle_state)
            except ValueError:
                logger.error(
                    f"[MySQLWorkerRegistryStore] Invalid lifecycle_state value: {lifecycle_state}"
                )
                return None

        # R15-PATCH-A: ONLY update lifecycle_state column
        # DO NOT modify availability (visibility layer)
        # DO NOT modify runtime_state (operational layer) here - use WorkerRuntimeStateService

        conn = self._pool.get_connection()
        try:
            cursor = conn.cursor()

            try:
                # Update only lifecycle_state column
                cursor.execute("""
                    UPDATE workers
                    SET lifecycle_state = %s,
                        version = version + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE worker_id = %s
                """, (lifecycle_state.value, worker_id))

                if cursor.rowcount == 0:
                    logger.warning(
                        f"[MySQLWorkerRegistryStore] update_lifecycle_state() failed: "
                        f"worker_id={worker_id} not found"
                    )
                    return None

                elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000

                logger.info(
                    f"[MySQLWorkerRegistryStore] update_lifecycle_state() completed: "
                    f"worker_id={worker_id}, lifecycle_state={lifecycle_state.value}, "
                    f"thread_id={thread_id}, elapsed_ms={elapsed_ms:.2f}"
                )

                # Return updated worker
                return self.get(worker_id)

            finally:
                cursor.close()

        except Error as e:
            logger.error(
                f"[MySQLWorkerRegistryStore] update_lifecycle_state() failed: {e}"
            )
            return None

        finally:
            conn.close()

    def sync_runtime_state_mirror(
        self,
        worker_id: str,
        runtime_state: str,
    ) -> bool:
        """
        Sync runtime_state to workers table (denormalized mirror).

        CRITICAL SEMANTIC SEPARATION:
        - worker_runtime_state table is CANONICAL SOURCE OF TRUTH
        - workers.runtime_state is DENORMALIZED MIRROR for scan performance
        - This method ONLY updates the mirror, NOT the canonical source

        This method should be called by WorkerRuntimeStateService after:
        - set_online(): sync runtime_state='online'
        - set_offline(): sync runtime_state='offline'

        Args:
            worker_id: Worker ID
            runtime_state: Runtime state value ('online' or 'offline')

        Returns:
            True if update succeeded, False otherwise
        """
        start_time = datetime.now()
        thread_id = threading.current_thread().ident

        conn = self._pool.get_connection()
        try:
            cursor = conn.cursor()

            try:
                # Update only runtime_state mirror column
                cursor.execute("""
                    UPDATE workers
                    SET runtime_state = %s,
                        version = version + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE worker_id = %s
                """, (runtime_state, worker_id))

                if cursor.rowcount == 0:
                    logger.warning(
                        f"[MySQLWorkerRegistryStore] sync_runtime_state_mirror() failed: "
                        f"worker_id={worker_id} not found"
                    )
                    return False

                elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000

                logger.info(
                    f"[MySQLWorkerRegistryStore] sync_runtime_state_mirror() completed: "
                    f"worker_id={worker_id}, runtime_state={runtime_state}, "
                    f"thread_id={thread_id}, elapsed_ms={elapsed_ms:.2f}"
                )

                return True

            finally:
                cursor.close()

        except Error as e:
            logger.error(
                f"[MySQLWorkerRegistryStore] sync_runtime_state_mirror() failed: {e}"
            )
            return False

        finally:
            conn.close()

    def sync_active_profile_key_mirror(
        self,
        worker_id: str,
        profile_key: str,
    ) -> bool:
        """
        Sync active_profile_key to workers table (denormalized mirror).

        Source of Truth:
        - bcsfuse_worker_profile_bindings table is CANONICAL SOURCE OF TRUTH
        - workers.active_profile_key is DENORMALIZED MIRROR for scan performance
        - This method ONLY updates the mirror, NOT the canonical source

        This method should be called after profile activation:
        - After set_active_profile(): sync active_profile_key

        Args:
            worker_id: Worker ID
            profile_key: Active profile key (format: "{worker_id}:{profile_id}")

        Returns:
            True if update succeeded, False otherwise
        """
        start_time = datetime.now()
        thread_id = threading.current_thread().ident

        conn = self._pool.get_connection()
        try:
            cursor = conn.cursor()

            try:
                # Update only active_profile_key mirror column
                cursor.execute("""
                    UPDATE workers
                    SET active_profile_key = %s,
                        version = version + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE worker_id = %s
                """, (profile_key, worker_id))

                if cursor.rowcount == 0:
                    logger.warning(
                        f"[MySQLWorkerRegistryStore] sync_active_profile_key_mirror() failed: "
                        f"worker_id={worker_id} not found"
                    )
                    return False

                elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000

                logger.info(
                    f"[MySQLWorkerRegistryStore] sync_active_profile_key_mirror() completed: "
                    f"worker_id={worker_id}, profile_key={profile_key}, "
                    f"thread_id={thread_id}, elapsed_ms={elapsed_ms:.2f}"
                )

                return True

            finally:
                cursor.close()

        except Error as e:
            logger.error(
                f"[MySQLWorkerRegistryStore] sync_active_profile_key_mirror() failed: {e}"
            )
            return False

        finally:
            conn.close()

    def close(self) -> None:
        """Close connection pool (for application shutdown)."""
        if self._pool:
            self._pool.close()
            logger.info("[MySQLWorkerRegistryStore] Connection pool closed")


__all__ = ["MySQLWorkerRegistryStore"]