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
import math
import threading
import time
from typing import Optional, Dict, Any
from datetime import datetime

from mysql.connector import pooling, Error

logger = logging.getLogger(__name__)

DEFAULT_MYSQL_POOL_SIZE = 8
MAX_MYSQL_POOL_SIZE = 64
DEFAULT_MYSQL_STARTUP_RETRY_ATTEMPTS = 5
MAX_MYSQL_STARTUP_RETRY_ATTEMPTS = 30
DEFAULT_MYSQL_STARTUP_RETRY_INTERVAL_MS = 250.0
MAX_MYSQL_STARTUP_RETRY_INTERVAL_MS = 5000.0


def mysql_pool_size(pool_size: Optional[int] = None) -> int:
    """Return a bounded pool size from an explicit value or MYSQL_POOL_SIZE.

    The open-source runtime default is intentionally lower than the historical
    hard-coded value because a multi-pod deployment shares the MySQL server
    connection quota. Explicit scaling remains possible, but one pod cannot
    silently reserve an unbounded share of the shared database.
    """

    if pool_size is None:
        raw_value = os.getenv("MYSQL_POOL_SIZE", str(DEFAULT_MYSQL_POOL_SIZE))
        try:
            value = int(raw_value)
        except (TypeError, ValueError) as error:
            raise ValueError(
                "MYSQL_POOL_SIZE must be an integer between 1 and "
                f"{MAX_MYSQL_POOL_SIZE}"
            ) from error
    else:
        value = pool_size

    if value <= 0:
        raise ValueError(
            f"pool_size must be between 1 and {MAX_MYSQL_POOL_SIZE}, got {value}"
        )
    if value > MAX_MYSQL_POOL_SIZE:
        raise ValueError(
            f"pool_size must not exceed {MAX_MYSQL_POOL_SIZE}, got {value}"
        )
    return value


def _bounded_positive_int_env(name: str, default: int, maximum: int) -> int:
    raw_value = os.getenv(name, str(default))
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an integer between 1 and {maximum}") from error
    if value <= 0 or value > maximum:
        raise ValueError(f"{name} must be an integer between 1 and {maximum}")
    return value


def _bounded_positive_float_env(name: str, default: float, maximum: float) -> float:
    raw_value = os.getenv(name, str(default))
    try:
        value = float(raw_value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a number between 0 and {maximum}") from error
    if value <= 0 or value > maximum:
        raise ValueError(f"{name} must be a number between 0 and {maximum}")
    return value


def mysql_startup_retry_attempts(attempts: int | None = None) -> int:
    """Return a bounded retry count for transient MySQL connection quota errors."""
    if attempts is not None:
        if attempts <= 0 or attempts > MAX_MYSQL_STARTUP_RETRY_ATTEMPTS:
            raise ValueError(
                f"MYSQL_STARTUP_RETRY_ATTEMPTS must be between 1 and "
                f"{MAX_MYSQL_STARTUP_RETRY_ATTEMPTS}"
            )
        return attempts
    return _bounded_positive_int_env(
        "MYSQL_STARTUP_RETRY_ATTEMPTS",
        DEFAULT_MYSQL_STARTUP_RETRY_ATTEMPTS,
        MAX_MYSQL_STARTUP_RETRY_ATTEMPTS,
    )


def mysql_startup_retry_delay_ms(delay_ms: float | None = None) -> float:
    """Return the base backoff delay in milliseconds for startup retries."""
    if delay_ms is not None:
        if delay_ms <= 0 or delay_ms > MAX_MYSQL_STARTUP_RETRY_INTERVAL_MS:
            raise ValueError(
                f"MYSQL_STARTUP_RETRY_INTERVAL_MS must be between 0 and "
                f"{MAX_MYSQL_STARTUP_RETRY_INTERVAL_MS}"
            )
        return delay_ms
    return _bounded_positive_float_env(
        "MYSQL_STARTUP_RETRY_INTERVAL_MS",
        DEFAULT_MYSQL_STARTUP_RETRY_INTERVAL_MS,
        MAX_MYSQL_STARTUP_RETRY_INTERVAL_MS,
    )


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
        pool_size: Optional[int] = None,
        pool_name: Optional[str] = None,
        connection_timeout: Optional[float] = None,
    ):
        """Initialize MySQL connection pool.

        Args:
            host: MySQL host (default: MYSQL_HOST env var or localhost).
            port: MySQL port (default: MYSQL_PORT env var or 3306).
            user: MySQL user (default: MYSQL_USER env var).
            password: MySQL password (default: MYSQL_PASSWORD env var).
            database: MySQL database (default: MYSQL_DATABASE env var).
            pool_size: Connection pool size. Defaults to MYSQL_POOL_SIZE (8)
                and is capped at 64 to preserve shared MySQL quota across pods.
            pool_name: Pool name (default: bcsfuse_pool_<timestamp>).
            connection_timeout: MySQL connect timeout in seconds (default:
                MYSQL_CONNECT_TIMEOUT env var, then 3.0).

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

        # Initialize lifecycle attributes before config validation so __del__
        # remains safe if the constructor rejects invalid input.
        self._pool: Optional[pooling.MySQLConnectionPool] = None
        self._lock = threading.Lock()
        self._initialized = False
        self._connection_count = 0
        self._peak_connections = 0
        self._pool_failed_at: Optional[float] = None
        self._pool_failure_summary: Optional[str] = None
        self._pool_failure_cooldown_seconds = 30.0
        self.pool_size = mysql_pool_size(pool_size)

        self.connection_timeout = (
            float(os.getenv("MYSQL_CONNECT_TIMEOUT", "3.0"))
            if connection_timeout is None
            else float(connection_timeout)
        )
        if self.connection_timeout <= 0:
            raise ValueError("connection_timeout must be positive")

        # Generate unique pool name to avoid conflicts
        if pool_name is None:
            self.pool_name = f"bcsfuse_pool_{int(datetime.now().timestamp())}"
        else:
            self.pool_name = pool_name

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

            if self._pool_failed_at is not None:
                age = time.monotonic() - self._pool_failed_at
                remaining = self._pool_failure_cooldown_seconds - age
                if remaining > 0:
                    summary = self._pool_failure_summary or "unknown"
                    logger.warning(
                        f"[MySQLConnectionPool] Initialization cooldown active: "
                        f"pool_name={self.pool_name}, retry_in_seconds={remaining:.2f}, "
                        f"previous_error={summary}"
                    )
                    raise RuntimeError(
                        f"MySQL connection pool is in cooldown for "
                        f"{remaining:.2f}s; previous error: {summary}"
                    )

            attempts = mysql_startup_retry_attempts()
            base_delay_ms = mysql_startup_retry_delay_ms()

            for attempt in range(1, attempts + 1):
                try:
                    logger.info(
                        f"[MySQLConnectionPool] Initializing pool: pool_name={self.pool_name}, "
                        f"pool_size={self.pool_size}, host={self._mask_host()}, port={self.port}, "
                        f"database={self.database}, user={self._mask_user()}, "
                        f"connection_timeout={self.connection_timeout}, attempt={attempt}"
                    )

                    self._pool = pooling.MySQLConnectionPool(
                        pool_name=self.pool_name,
                        pool_size=self.pool_size,
                        host=self.host,
                        port=self.port,
                        user=self.user,
                        password=self.password,
                        database=self.database,
                        connection_timeout=max(1, math.ceil(self.connection_timeout)),
                        autocommit=True,
                        pool_reset_session=True,  # Reset session state when returning to pool
                    )

                    self._initialized = True
                    self._pool_failed_at = None
                    self._pool_failure_summary = None

                    logger.info(
                        f"[MySQLConnectionPool] Pool initialized successfully: "
                        f"pool_name={self.pool_name}, attempt={attempt}"
                    )
                    return

                except Error as error:
                    # Driver messages may echo credentials or a DSN. Keep a useful
                    # error type/code without exposing the driver's raw message.
                    error_summary = f"{type(error).__name__} (errno={error.errno})"
                    logger.warning(
                        f"[MySQLConnectionPool] Pool initialization attempt failed: "
                        f"pool_name={self.pool_name}, attempt={attempt}/{attempts}, "
                        f"host={self._mask_host()}, port={self.port}, "
                        f"database={self.database}, error={error_summary}"
                    )

                    # A horizontal-scale pod can start while the shared MySQL
                    # quota is temporarily exhausted. Back off and retry so a
                    # rolling restart does not cascade into failed pods. Other
                    # connectivity, authentication, and schema errors fail fast.
                    if error.errno != 1040 or attempt == attempts:
                        self._pool_failed_at = time.monotonic()
                        self._pool_failure_summary = error_summary
                        logger.error(
                            f"[MySQLConnectionPool] Pool initialization failed: "
                            f"host={self._mask_host()}, port={self.port}, "
                            f"database={self.database}, error={error_summary}"
                        )
                        raise RuntimeError(
                            f"Failed to initialize MySQL connection pool at "
                            f"{self._mask_host()}:{self.port}/{self.database}: "
                            f"{error_summary}"
                        ) from None

                    delay_ms = min(
                        base_delay_ms * (2 ** (attempt - 1)),
                        MAX_MYSQL_STARTUP_RETRY_INTERVAL_MS,
                    )
                    time.sleep(delay_ms / 1000.0)

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
            error_summary = f"{type(e).__name__} (errno={e.errno})"
            logger.error(
                f"[MySQLConnectionPool] Failed to get connection from pool: "
                f"pool_name={self.pool_name}, error={error_summary}"
            )
            raise RuntimeError(
                f"Failed to get connection from MySQL pool: {error_summary}"
            ) from None

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
            "connection_timeout": self.connection_timeout,
            "initialized": self._initialized,
            "host_masked": self._mask_host(),
            "port": self.port,
            "database": self.database,
            "user_masked": self._mask_user(),
            "password_present": bool(self.password),
            "active_connections": self._connection_count,
            "peak_connections": self._peak_connections,
            "pool_available": self._pool is not None,
            "pool_failure_summary": self._pool_failure_summary,
            "pool_failure_cooldown_seconds": self._pool_failure_cooldown_seconds,
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
