"""
MySQL Worker Profile Content Store (Connection Pool Version)

MySQL implementation for production OSS deployments.

R12-Pool-4 Fix:
- Replaced shared self._connection with MySQLConnectionPoolProvider
- Each method gets its own connection from pool
- Connection returned to pool after use (conn.close())
- Thread-safe by design (pool handles connection distribution)
- Transaction handling: restore autocommit before returning connection
- No more Fatal Python error from concurrent MySQL connector access
"""
import os
import json
import logging
import threading
from typing import Optional, List, TYPE_CHECKING
import mysql.connector

if TYPE_CHECKING:
    from src.infra.public.database.mysql_connection_pool import MySQLConnectionPoolProvider

logger = logging.getLogger(__name__)


class MySQLWorkerProfileContentStore:
    """MySQL Worker Profile Content Store for OSS (Connection Pool Version).

    Suitable for production deployments with MySQL database.

    Thread Safety:
        - Uses connection pool for thread-safe access
        - Each method borrows connection from pool
        - Connection returned to pool after use
        - No shared connection state
        - Transaction handling: restore autocommit before returning
    """

    def __init__(
        self,
        connection_pool: Optional["MySQLConnectionPoolProvider"] = None,
        **kwargs
    ):
        """Initialize MySQL store with connection pool.

        Args:
            connection_pool: MySQLConnectionPoolProvider instance (preferred).
            **kwargs: Fallback MySQL connection parameters.
        """
        if connection_pool is None:
            from src.infra.public.database.mysql_connection_pool import MySQLConnectionPoolProvider

            self._pool = MySQLConnectionPoolProvider(
                host=kwargs.get("host") or os.getenv("MYSQL_HOST", "localhost"),
                port=kwargs.get("port") or int(os.getenv("MYSQL_PORT", "3306")),
                user=kwargs.get("user") or os.getenv("MYSQL_USER", "root"),
                password=kwargs.get("password") or os.getenv("MYSQL_PASSWORD", ""),
                database=kwargs.get("database") or os.getenv("MYSQL_DATABASE", "bcsfuse"),
            )
            logger.info(
                "[MySQLWorkerProfileContentStore] Created internal connection pool (fallback mode)"
            )
        else:
            self._pool = connection_pool
            logger.info(
                "[MySQLWorkerProfileContentStore] Using injected connection pool"
            )

        self._schema_initialized = False
        self._schema_lock = threading.Lock()

    def _ensure_schema(self, conn) -> None:
        """Ensure database schema exists."""
        if self._schema_initialized:
            return

        with self._schema_lock:
            if self._schema_initialized:
                return

            cursor = conn.cursor()

            try:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS worker_profile_content (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        worker_id VARCHAR(255) NOT NULL,
                        profile_id VARCHAR(255) NOT NULL,
                        content JSON,
                        is_active BOOLEAN DEFAULT FALSE,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        UNIQUE KEY unique_worker_profile (worker_id, profile_id),
                        INDEX idx_worker_id (worker_id),
                        INDEX idx_worker_active (worker_id, is_active)
                    )
                """)

                # Check if is_active column exists
                cursor.execute("SHOW COLUMNS FROM worker_profile_content LIKE 'is_active'")
                if cursor.fetchone() is None:
                    cursor.execute("ALTER TABLE worker_profile_content ADD COLUMN is_active BOOLEAN DEFAULT FALSE")
                    cursor.execute("ALTER TABLE worker_profile_content ADD INDEX idx_worker_active (worker_id, is_active)")

                self._schema_initialized = True
                logger.info(
                    "[MySQLWorkerProfileContentStore] Schema initialized successfully"
                )

            finally:
                cursor.close()

    def save(self, worker_id: str, profile_id: str, content: dict) -> bool:
        """Save profile content (thread-safe with connection pool)."""
        conn = self._pool.get_connection()
        try:
            self._ensure_schema(conn)

            cursor = conn.cursor()

            try:
                cursor.execute("""
                    INSERT INTO worker_profile_content (worker_id, profile_id, content)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE content = %s
                """, (worker_id, profile_id, json.dumps(content), json.dumps(content)))

                logger.debug(
                    f"[MySQLWorkerProfileContentStore] save() completed: "
                    f"worker_id={worker_id}, profile_id={profile_id}"
                )

                return True

            finally:
                cursor.close()

        finally:
            conn.close()

    def get(self, worker_id: str, profile_id: str) -> Optional[dict]:
        """Get profile content (thread-safe with connection pool).

        Returns dict with flattened fields (worker_id, profile_id, metadata, etc.)
        for compatibility with APIProfileSource.
        """
        conn = self._pool.get_connection()
        try:
            self._ensure_schema(conn)

            cursor = conn.cursor(dictionary=True)

            try:
                cursor.execute(
                    "SELECT worker_id, profile_id, content FROM worker_profile_content WHERE worker_id = %s AND profile_id = %s",
                    (worker_id, profile_id)
                )
                row = cursor.fetchone()

                if row:
                    content_dict = json.loads(row["content"]) if row["content"] else {}

                    # Flatten content fields to top level
                    result = {
                        "worker_id": row["worker_id"],
                        "profile_id": row["profile_id"],
                        **content_dict  # Spread all content fields (including metadata)
                    }

                    logger.debug(
                        f"[MySQLWorkerProfileContentStore] get() found: "
                        f"worker_id={worker_id}, profile_id={profile_id}, "
                        f"metadata_keys={sorted((content_dict.get('metadata') or {}).keys())}"
                    )
                    return result
                else:
                    logger.debug(
                        f"[MySQLWorkerProfileContentStore] get() not found: "
                        f"worker_id={worker_id}, profile_id={profile_id}"
                    )
                    return None

            finally:
                cursor.close()

        finally:
            conn.close()

    def list_profiles(self, worker_id: str) -> List[dict]:
        """List all profiles for worker (thread-safe with connection pool).

        Returns list of dicts with flattened fields (worker_id, profile_id, metadata, etc.)
        for compatibility with APIProfileSource.
        """
        conn = self._pool.get_connection()
        try:
            self._ensure_schema(conn)

            cursor = conn.cursor(dictionary=True)

            try:
                cursor.execute(
                    "SELECT worker_id, profile_id, content FROM worker_profile_content WHERE worker_id = %s",
                    (worker_id,)
                )
                rows = cursor.fetchall()

                logger.debug(
                    f"[MySQLWorkerProfileContentStore] list_profiles() completed: "
                    f"worker_id={worker_id}, count={len(rows)}"
                )

                results = []
                for row in rows:
                    content_dict = json.loads(row["content"]) if row["content"] else {}

                    # Flatten content fields to top level
                    flattened = {
                        "worker_id": row["worker_id"],
                        "profile_id": row["profile_id"],
                        **content_dict  # Spread all content fields (including metadata)
                    }
                    results.append(flattened)

                return results

            finally:
                cursor.close()

        finally:
            conn.close()

    def delete(self, worker_id: str, profile_id: str) -> bool:
        """Delete profile content (thread-safe with connection pool)."""
        conn = self._pool.get_connection()
        try:
            self._ensure_schema(conn)

            cursor = conn.cursor()

            try:
                cursor.execute(
                    "DELETE FROM worker_profile_content WHERE worker_id = %s AND profile_id = %s",
                    (worker_id, profile_id)
                )
                result = cursor.rowcount > 0

                logger.debug(
                    f"[MySQLWorkerProfileContentStore] delete() completed: "
                    f"worker_id={worker_id}, profile_id={profile_id}, deleted={result}"
                )

                return result

            finally:
                cursor.close()

        finally:
            conn.close()

    # ========================================
    # Compatibility methods for OSS routes
    # ========================================

    def upsert_profile(self, worker_id: str, profile_id: str, content: dict) -> bool:
        """Alias for save() method for OSS routes compatibility."""
        return self.save(worker_id, profile_id, content)

    def get_profile(self, worker_id: str, profile_id: str) -> Optional[dict]:
        """Alias for get() method for OSS routes compatibility.

        Returns dict with flattened fields (worker_id, profile_id, metadata, etc.)
        """
        return self.get(worker_id, profile_id)

    def delete_profile(self, worker_id: str, profile_id: str) -> bool:
        """Alias for delete() method for OSS routes compatibility."""
        return self.delete(worker_id, profile_id)

    def get_active_profiles(self) -> List[dict]:
        """Get all active profiles (thread-safe with connection pool).

        Returns list of dicts with flattened fields (worker_id, profile_id, metadata, etc.)
        for compatibility with APIProfileSource.
        """
        conn = self._pool.get_connection()
        try:
            self._ensure_schema(conn)

            cursor = conn.cursor(dictionary=True)

            try:
                cursor.execute(
                    "SELECT worker_id, profile_id, content FROM worker_profile_content WHERE is_active = TRUE"
                )
                rows = cursor.fetchall()

                logger.debug(
                    f"[MySQLWorkerProfileContentStore] get_active_profiles() completed: count={len(rows)}"
                )

                results = []
                for row in rows:
                    content_dict = json.loads(row["content"]) if row["content"] else {}

                    # Flatten content fields to top level
                    flattened = {
                        "worker_id": row["worker_id"],
                        "profile_id": row["profile_id"],
                        **content_dict  # Spread all content fields (including metadata)
                    }
                    results.append(flattened)

                return results

            finally:
                cursor.close()

        finally:
            conn.close()

    def get_all_active(self) -> List[dict]:
        """Get all active profiles (thread-safe with connection pool).

        Returns profiles in format compatible with APIProfileSource.scan().
        Flattens content fields to top level so metadata is accessible as record["metadata"].
        """
        conn = self._pool.get_connection()
        try:
            self._ensure_schema(conn)

            cursor = conn.cursor(dictionary=True)

            try:
                cursor.execute(
                    "SELECT worker_id, profile_id, content FROM worker_profile_content WHERE is_active = TRUE"
                )
                rows = cursor.fetchall()

                profiles = []
                for row in rows:
                    content_dict = json.loads(row["content"]) if row["content"] else {}

                    # Flatten content fields to top level
                    # This ensures metadata, soul_md, contents, etc. are accessible as top-level keys
                    flattened = {
                        "worker_id": row["worker_id"],
                        "profile_id": row["profile_id"],
                        **content_dict  # Spread all content fields (including metadata)
                    }
                    profiles.append(flattened)

                logger.debug(
                    f"[MySQLWorkerProfileContentStore] get_all_active() completed: count={len(profiles)}, "
                    f"sample_metadata_keys={sorted((profiles[0].get('metadata') or {}).keys()) if profiles else 'none'}"
                )

                return profiles

            finally:
                cursor.close()

        finally:
            conn.close()

    def get_active_profile_for_worker(self, worker_id: str) -> Optional[dict]:
        """Get the active profile for a specific worker (thread-safe with connection pool).

        Returns dict with flattened fields (worker_id, profile_id, metadata, etc.)
        for compatibility with APIProfileSource.
        """
        conn = self._pool.get_connection()
        try:
            self._ensure_schema(conn)

            cursor = conn.cursor(dictionary=True)

            try:
                cursor.execute(
                    "SELECT worker_id, profile_id, content FROM worker_profile_content WHERE worker_id = %s AND is_active = TRUE LIMIT 1",
                    (worker_id,)
                )
                row = cursor.fetchone()

                if row:
                    content_dict = json.loads(row["content"]) if row["content"] else {}

                    # Flatten content fields to top level
                    result = {
                        "worker_id": row["worker_id"],
                        "profile_id": row["profile_id"],
                        **content_dict  # Spread all content fields (including metadata)
                    }

                    logger.debug(
                        f"[MySQLWorkerProfileContentStore] get_active_profile_for_worker() found: "
                        f"worker_id={worker_id}, profile_id={row['profile_id']}, "
                        f"metadata_keys={sorted((content_dict.get('metadata') or {}).keys())}"
                    )
                    return result
                else:
                    logger.debug(
                        f"[MySQLWorkerProfileContentStore] get_active_profile_for_worker() not found: "
                        f"worker_id={worker_id}"
                    )
                    return None

            finally:
                cursor.close()

        finally:
            conn.close()

    def activate_profile(self, worker_id: str, profile_id: str) -> bool:
        """Mark a profile as active and deactivate others for the same worker.

        Transaction handling:
        - Sets autocommit=False for transaction
        - Deactivates all profiles for worker
        - Activates target profile
        - Commits or rolls back
        - Restores autocommit=True before returning connection to pool

        Args:
            worker_id: Worker ID
            profile_id: Profile ID to activate

        Returns:
            True if successful, False otherwise
        """
        conn = self._pool.get_connection()
        try:
            self._ensure_schema(conn)

            cursor = conn.cursor()

            try:
                # Start transaction
                conn.autocommit = False

                # Deactivate all profiles for this worker
                cursor.execute(
                    "UPDATE worker_profile_content SET is_active = FALSE WHERE worker_id = %s",
                    (worker_id,)
                )

                # Activate the specified profile
                cursor.execute(
                    "UPDATE worker_profile_content SET is_active = TRUE WHERE worker_id = %s AND profile_id = %s",
                    (worker_id, profile_id)
                )

                conn.commit()

                logger.debug(
                    f"[MySQLWorkerProfileContentStore] activate_profile() committed: "
                    f"worker_id={worker_id}, profile_id={profile_id}"
                )

                return cursor.rowcount > 0

            except Exception as e:
                conn.rollback()
                logger.error(
                    f"[MySQLWorkerProfileContentStore] activate_profile() rollback: "
                    f"worker_id={worker_id}, profile_id={profile_id}, error={e}"
                )
                raise e

            finally:
                # CRITICAL: Restore autocommit before returning connection to pool
                conn.autocommit = True
                cursor.close()

        finally:
            conn.close()

    def close(self) -> None:
        """Close connection pool (for application shutdown)."""
        if self._pool:
            self._pool.close()
            logger.info("[MySQLWorkerProfileContentStore] Connection pool closed")


__all__ = ["MySQLWorkerProfileContentStore"]