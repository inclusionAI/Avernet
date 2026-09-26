"""Database provider contract for public open-core.

This port abstracts database connections to allow different implementations:
- Public: SQLite, PostgreSQL, MySQL
- Internal: OceanBase, ZDAS

Public code must depend on this contract, not internal database SDKs.
"""

from typing import Protocol, Any, ContextManager, runtime_checkable


@runtime_checkable
class DatabaseProvider(Protocol):
    """Public database provider contract.

    Implementations may be OSS defaults (SQLite, PostgreSQL, MySQL) or
    internal plugins (OceanBase, ZDAS).

    Public code must depend on this contract, not internal database SDKs.
    """

    def get_connection(self, datasource_name: str = "default") -> ContextManager[Any]:
        """Get a database connection for the specified datasource.

        Args:
            datasource_name: Name of the datasource (default: "default")

        Returns:
            Context manager yielding a database connection object.

        Usage:
            with db.get_connection("my_ds") as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT * FROM table")
        """
        ...

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
            Query result (implementation-specific).
        """
        ...

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
            Number of rows affected.
        """
        ...

    def begin_transaction(self, datasource_name: str = "default") -> ContextManager[Any]:
        """Begin a database transaction.

        Args:
            datasource_name: Name of the datasource

        Returns:
            Context manager for transaction scope.

        Usage:
            with db.begin_transaction() as tx:
                db.execute("INSERT ...", tx=tx)
                db.execute("UPDATE ...", tx=tx)
        """
        ...

    def close(self) -> None:
        """Close all database connections.

        Should be called during application shutdown.
        """
        ...

    def health_check(self) -> bool:
        """Check database health.

        Returns:
            True if database is healthy, False otherwise.
        """
        ...