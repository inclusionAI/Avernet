"""
MySQL Connection Pool Provider

Thread-safe MySQL connection pooling for production OSS deployments.

R12-Pool-1: Replaces shared self._connection with connection pool to fix
G1 fusion crash caused by concurrent MySQL connector access.

Design:
- Uses mysql.connector.pooling.MySQLConnectionPool
- Pool size >= 10 to cover G1/G2/G5 concurrent workers
- Thread-safe by design (pool handles connection distribution)
- No password logging
- Safe diagnostics (masked DSN)
"""

import os
import logging
import threading
from typing import Optional, Dict, Any
from datetime import datetime

import mysql.connector
from mysql.connector import pooling, Error

logger = logging.getLogger(__name__)


class _TrackedConnection:
    """Wrapper for pooled connection that tracks when connection is returned.

    This wrapper ensures that _connection_count is decremented when the
    connection is returned to the pool via close().
    """

    def __init__(self, conn, pool_provider):
        self._conn = conn
        self._pool_provider = pool_provider
        self._closed = False

    def __getattr__(self, name):
        # Delegate all other attributes to the underlying connection
        return getattr(self._conn, name)

    def close(self):
        """Return connection to pool and decrement active count.

        Handles unread results by consuming them before closing.
        """
        if self._closed:
            return

        self._closed = True

        # Handle unread results before closing
        # MySQL connector raises "Unread result found" if cursor not fully consumed
        try:
            # Try to consume any unread results
            if hasattr(self._conn, 'unread_result') and self._conn.unread_result:
                # Consume the unread result
                cursor = self._conn.cursor()
                try:
                    while cursor.nextset():
                        pass
                except Exception:
                    pass
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as e:
            logger.debug(
                f"[MySQLConnectionPool] Error handling unread results: {e}"
            )

        # Decrement active connection count
        with self._pool_provider._lock:
            self._pool_provider._connection_count = max(
                0, self._pool_provider._connection_count - 1
            )

        logger.debug(
            f"[MySQLConnectionPool] Connection returned: pool_name={self._pool_provider.pool_name}, "
            f"active_connections={self._pool_provider._connection_count}"
        )

        # Return connection to pool
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


class MySQLConnectionPoolProvider:
    """
    Thread-safe MySQL connection pool provider.

    Suitable for production deployments with concurrent access.

    Usage:
        pool = MySQLConnectionPoolProvider(
            host="localhost",
            port=3306,
            user="your_user",
            password="your_password",
            database="bcsfuse",
            pool_size=15,
        )

        # Get connection from pool
        conn = pool.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM workers")
            results = cursor.fetchall()
        finally:
            conn.close()  # Returns connection to pool, does NOT close

    Thread Safety:
        - Pool is thread-safe by design (mysql.connector handles locking)
        - Each thread gets its own connection from pool
        - No shared connection state across threads
        - Connection.close() returns connection to pool (reuse)

    R12-Pool-1 Fix:
        - BEFORE: Multiple threads share self._connection instance
        - AFTER: Each thread borrows connection from pool, returns after use
        - NO MORE CRASH: mysql.connector.connection_cext.is_connected() not called concurrently
    """

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        database: Optional[str] = None,
        pool_size: int = 15,
        pool_name: Optional[str] = None,
    ):
        """Initialize MySQL connection pool.

        Args:
            host: MySQL host (default: MYSQL_HOST env var or localhost).
            port: MySQL port (default: MYSQL_PORT env var or 3306).
            user: MySQL user (default: MYSQL_USER env var).
            password: MySQL password (default: MYSQL_PASSWORD env var).
            database: MySQL database (default: MYSQL_DATABASE env var).
            pool_size: Connection pool size (default: 15, covers G1/G2/G5 concurrent workers).
            pool_name: Pool name (default: bcsfuse_pool_<timestamp>).

        Raises:
            RuntimeError: If pool initialization fails.
        """
        self.host = host or os.getenv("MYSQL_HOST", "localhost")
        self.port = port or int(os.getenv("MYSQL_PORT", "3306"))
        self.user = user or os.getenv("MYSQL_USER", "")
        self.password = password or os.getenv("MYSQL_PASSWORD", "")
        if not self.password:
            logger.warning(
                "[MySQLConnectionPool] MYSQL_PASSWORD is empty or not set. "
                "This is insecure for production. Set MYSQL_PASSWORD environment variable."
            )
        self.database = database or os.getenv("MYSQL_DATABASE", "bcsfuse")
        self.pool_size = pool_size

        # Generate unique pool name to avoid conflicts
        if pool_name is None:
            self.pool_name = f"bcsfuse_pool_{int(datetime.now().timestamp())}"
        else:
            self.pool_name = pool_name

        self._pool: Optional[pooling.MySQLConnectionPool] = None
        self._lock = threading.Lock()
        self._initialized = False
        self._connection_count = 0
        self._peak_connections = 0

        # Initialize pool lazily on first get_connection() call
        logger.info(
            "[MySQLConnectionPool] Provider created (lazy init): "
            f"pool_name={self.pool_name}, pool_size={self.pool_size}, "
            f"host={self._mask_host()}, port={self.port}, database={self.database}, "
            f"user={self._mask_user()}"
        )

    def _mask_host(self) -> str:
        """Mask host for safe logging."""
        if not self.host:
            return "<empty>"
        if self.host in ["localhost", "127.0.0.1"]:
            return self.host
        # Mask non-localhost hosts: example.com -> e********m
        if len(self.host) <= 2:
            return "<masked>"
        return f"{self.host[0]}{'*' * (len(self.host) - 2)}{self.host[-1]}"

    def _mask_user(self) -> str:
        """Mask user for safe logging."""
        if not self.user:
            return "<empty>"
        if len(self.user) <= 2:
            return "<masked>"
        return f"{self.user[0]}{'*' * (len(self.user) - 2)}{self.user[-1]}"

    def _ensure_pool(self) -> None:
        """Initialize connection pool on first use (lazy initialization)."""
        if self._pool is not None:
            return

        with self._lock:
            # Double-check after acquiring lock
            if self._pool is not None:
                return

            try:
                logger.info(
                    f"[MySQLConnectionPool] Initializing pool: pool_name={self.pool_name}, "
                    f"pool_size={self.pool_size}, host={self._mask_host()}, port={self.port}, "
                    f"database={self.database}, user={self._mask_user()}"
                )

                self._pool = pooling.MySQLConnectionPool(
                    pool_name=self.pool_name,
                    pool_size=self.pool_size,
                    host=self.host,
                    port=self.port,
                    user=self.user,
                    password=self.password,
                    database=self.database,
                    autocommit=True,
                    pool_reset_session=True,  # Reset session state when returning to pool
                )

                self._initialized = True

                logger.info(
                    f"[MySQLConnectionPool] Pool initialized successfully: pool_name={self.pool_name}"
                )

            except Error as e:
                logger.error(
                    f"[MySQLConnectionPool] Pool initialization failed: "
                    f"host={self._mask_host()}, port={self.port}, database={self.database}, "
                    f"error={e}"
                )
                raise RuntimeError(
                    f"Failed to initialize MySQL connection pool at "
                    f"{self._mask_host()}:{self.port}/{self.database}: {e}"
                ) from e

    def get_connection(self):
        """Get a connection from the pool.

        Returns:
            PooledMySQLConnection: Connection from pool.

        Usage:
            conn = pool.get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM workers")
                results = cursor.fetchall()
                cursor.close()
            finally:
                conn.close()  # Returns connection to pool

        Thread Safety:
            - Thread-safe: Each thread gets its own connection
            - Connection.close() does NOT close underlying connection
            - Connection.close() returns connection to pool for reuse

        Raises:
            RuntimeError: If pool is not initialized or connection fails.
        """
        self._ensure_pool()

        try:
            conn = self._pool.get_connection()

            # Track connection metrics
            with self._lock:
                self._connection_count += 1
                if self._connection_count > self._peak_connections:
                    self._peak_connections = self._connection_count

            logger.debug(
                f"[MySQLConnectionPool] Connection acquired: pool_name={self.pool_name}, "
                f"active_connections={self._connection_count}, peak={self._peak_connections}"
            )

            # Wrap connection to track when it's returned to pool
            return _TrackedConnection(conn, self)

        except Error as e:
            logger.error(
                f"[MySQLConnectionPool] Failed to get connection from pool: "
                f"pool_name={self.pool_name}, error={e}"
            )
            raise RuntimeError(
                f"Failed to get connection from MySQL pool: {e}"
            ) from e

    def return_connection(self, conn) -> None:
        """Return connection to pool (deprecated - use conn.close() instead).

        NOTE: This method is DEPRECATED. Use conn.close() instead.
        mysql.connector pooling automatically returns connections to pool
        when conn.close() is called.

        Args:
            conn: Connection to return.

        Deprecated:
            Use conn.close() instead. This method is kept for backward compatibility.
        """
        if conn is None:
            return

        try:
            conn.close()

            # Track connection metrics
            with self._lock:
                self._connection_count = max(0, self._connection_count - 1)

            logger.debug(
                f"[MySQLConnectionPool] Connection returned: pool_name={self.pool_name}, "
                f"active_connections={self._connection_count}"
            )

        except Error as e:
            logger.warning(
                f"[MySQLConnectionPool] Failed to return connection to pool: "
                f"pool_name={self.pool_name}, error={e}"
            )

    def get_diagnostics(self) -> Dict[str, Any]:
        """Get pool diagnostics (safe for logging - no password).

        Returns:
            Dict with pool status:
                - pool_name: Pool name
                - pool_size: Pool size
                - initialized: Whether pool is initialized
                - host_masked: Masked host
                - port: Port
                - database: Database name
                - user_masked: Masked user
                - password_present: Whether password is set (True/False)
                - active_connections: Current active connections
                - peak_connections: Peak connection count
        """
        return {
            "pool_name": self.pool_name,
            "pool_size": self.pool_size,
            "initialized": self._initialized,
            "host_masked": self._mask_host(),
            "port": self.port,
            "database": self.database,
            "user_masked": self._mask_user(),
            "password_present": bool(self.password),
            "active_connections": self._connection_count,
            "peak_connections": self._peak_connections,
        }

    def close(self) -> None:
        """Close all connections in the pool.

        NOTE: This closes ALL connections in the pool and should only be
        called during application shutdown.
        """
        if self._pool is None:
            return

        logger.info(
            f"[MySQLConnectionPool] Closing pool: pool_name={self.pool_name}"
        )

        # mysql.connector pooling does not provide explicit close() method
        # Connections are closed when pool object is garbage collected
        # Set pool to None to allow garbage collection
        self._pool = None
        self._initialized = False

        logger.info(
            f"[MySQLConnectionPool] Pool closed: pool_name={self.pool_name}"
        )

    def __del__(self):
        """Cleanup pool on object destruction."""
        self.close()


__all__ = ["MySQLConnectionPoolProvider"]