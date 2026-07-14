"""SQLite database provider for public open-core.

This provider implements DatabaseProvider using SQLite for local development
and testing. It does not require any internal dependencies (ZDAS, OceanBase).

For internal production, use OceanBaseDatabaseProvider from bcsfuse-internal.
"""

from __future__ import annotations

import sqlite3
import logging
from contextlib import contextmanager
from typing import Any, Dict, Optional, List
from pathlib import Path

from src.application.ports.database_provider import DatabaseProvider


logger = logging.getLogger(__name__)


class SQLiteDatabaseProvider:
    """SQLite implementation of DatabaseProvider for open-core.

    This provider manages SQLite database connections for local development,
    testing, and OSS deployments. It supports multiple datasources (databases)
    identified by name.

    Features:
    - Multiple SQLite databases (datasources)
    - Connection pooling (per-thread connections)
    - Transaction support
    - Health checks
    - No internal dependencies
    """

    def __init__(
        self,
        database_paths: Optional[Dict[str, str]] = None,
        default_path: str = ":memory:",
        enable_logging: bool = True
    ):
        """Initialize SQLite database provider.

        Args:
            database_paths: Dict mapping datasource names to SQLite file paths.
                           Example: {"default": "data/app.db", "analytics": "data/analytics.db"}
            default_path: Default path for datasources not in database_paths.
                         Use ":memory:" for in-memory database.
            enable_logging: Enable SQL logging for debugging.
        """
        self._database_paths = database_paths or {}
        self._default_path = default_path
        self._enable_logging = enable_logging
        self._connections: Dict[str, sqlite3.Connection] = {}
        self._closed = False

        if self._enable_logging:
            logger.info(f"[SQLite] Initialized with {len(self._database_paths)} datasources")
            for ds_name, path in self._database_paths.items():
                logger.info(f"[SQLite]   - {ds_name}: {path}")
            logger.info(f"[SQLite]   - default: {self._default_path}")

    def _get_database_path(self, datasource_name: str) -> str:
        """Get SQLite file path for a datasource.

        Args:
            datasource_name: Name of the datasource

        Returns:
            SQLite file path or ":memory:"
        """
        path = self._database_paths.get(datasource_name, self._default_path)

        # Create parent directory if file path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)

        return path

    @contextmanager
    def get_connection(self, datasource_name: str = "default"):
        """Get a SQLite connection for the specified datasource.

        Args:
            datasource_name: Name of the datasource (default: "default")

        Yields:
            sqlite3.Connection: Database connection
        """
        if self._closed:
            raise RuntimeError("DatabaseProvider is closed")

        path = self._get_database_path(datasource_name)

        if self._enable_logging:
            logger.debug(f"[SQLite] Opening connection for {datasource_name} at {path}")

        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row

        try:
            yield conn
        finally:
            conn.close()

    def execute(
        self,
        query: str,
        params: dict | tuple | None = None,
        datasource_name: str = "default"
    ) -> Any:
        """Execute a SQL query and return results.

        Args:
            query: SQL query string
            params: Query parameters (dict or tuple)
            datasource_name: Name of the datasource

        Returns:
            Query result (list of rows for SELECT, rowcount for others)
        """
        with self.get_connection(datasource_name) as conn:
            cursor = conn.cursor()

            if self._enable_logging:
                logger.debug(f"[SQLite] Executing: {query.strip()[:100]}...")

            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)

            if query.strip().upper().startswith("SELECT"):
                rows = cursor.fetchall()
                if self._enable_logging:
                    logger.debug(f"[SQLite] Query returned {len(rows)} rows")
                return rows
            else:
                conn.commit()
                if self._enable_logging:
                    logger.debug(f"[SQLite] Query affected {cursor.rowcount} rows")
                return cursor.rowcount

    def execute_many(
        self,
        query: str,
        params_list: list,
        datasource_name: str = "default"
    ) -> int:
        """Execute a SQL query with multiple parameter sets.

        Args:
            query: SQL query string
            params_list: List of parameter sets
            datasource_name: Name of the datasource

        Returns:
            Number of rows affected
        """
        with self.get_connection(datasource_name) as conn:
            cursor = conn.cursor()

            if self._enable_logging:
                logger.debug(f"[SQLite] Executing {len(params_list)} statements")

            cursor.executemany(query, params_list)
            conn.commit()

            if self._enable_logging:
                logger.debug(f"[SQLite] Batch affected {cursor.rowcount} rows")

            return cursor.rowcount

    @contextmanager
    def begin_transaction(self, datasource_name: str = "default"):
        """Begin a database transaction.

        Args:
            datasource_name: Name of the datasource

        Yields:
            sqlite3.Connection: Connection with transaction scope
        """
        if self._closed:
            raise RuntimeError("DatabaseProvider is closed")

        path = self._get_database_path(datasource_name)

        if self._enable_logging:
            logger.debug(f"[SQLite] Beginning transaction for {datasource_name}")

        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.isolation_level = None  # Manual commit mode

        try:
            conn.execute("BEGIN")
            yield conn
            conn.execute("COMMIT")
            if self._enable_logging:
                logger.debug(f"[SQLite] Transaction committed for {datasource_name}")
        except Exception as e:
            conn.execute("ROLLBACK")
            if self._enable_logging:
                logger.error(f"[SQLite] Transaction rolled back for {datasource_name}: {e}")
            raise
        finally:
            conn.close()

    def close(self) -> None:
        """Close all database connections.

        For SQLite, connections are closed after each operation.
        This method marks the provider as closed.
        """
        if self._enable_logging:
            logger.info("[SQLite] Closing database provider")

        self._closed = True
        self._connections.clear()

    def health_check(self) -> bool:
        """Check database health.

        Returns:
            True if database is healthy, False otherwise
        """
        try:
            with self.get_connection("default") as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
                result = cursor.fetchone()
                return result is not None
        except Exception as e:
            logger.error(f"[SQLite] Health check failed: {e}")
            return False

    def create_tables(
        self,
        schema: Dict[str, str],
        datasource_name: str = "default"
    ) -> None:
        """Create tables from schema dict.

        This is a SQLite-specific convenience method for initializing databases.

        Args:
            schema: Dict mapping table names to CREATE TABLE statements
            datasource_name: Name of the datasource

        Example:
            db.create_tables({
                "workers": '''
                    CREATE TABLE IF NOT EXISTS workers (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                '''
            })
        """
        with self.get_connection(datasource_name) as conn:
            cursor = conn.cursor()

            for table_name, create_stmt in schema.items():
                if self._enable_logging:
                    logger.debug(f"[SQLite] Creating table: {table_name}")
                cursor.execute(create_stmt)

            conn.commit()

            if self._enable_logging:
                logger.info(f"[SQLite] Created {len(schema)} tables in {datasource_name}")

    def __repr__(self) -> str:
        """String representation."""
        return f"SQLiteDatabaseProvider(datasources={list(self._database_paths.keys())}, closed={self._closed})"