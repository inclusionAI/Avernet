"""Database plugin Protocol — SPI contract for database access.

Abstracts the two-tier connection model: sync ZDAS connections and
async SQLAlchemy sessions.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager, AbstractContextManager
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session


class DatabasePluginConfig(Protocol):
    """Minimal config contract for constructing a database plugin.

    The plugin's ``__init__`` receives the connection parameters (URL,
    credentials, schema/seed flags). ``init_database`` takes no arguments —
    it simply activates the already-constructed plugin.
    """

    plugin_type: Any
    """Which plugin backend was selected (e.g. ``sqlite``)."""

    db_url: str
    """Optional database URL override from application config."""


class ConnectionProvider(Protocol):
    """Minimal interface for obtaining database connections."""

    def get_connection(self, datasource_name: str) -> Any:
        """Return a raw database connection for the given datasource."""
        ...


class DataSourcePlugin(Protocol):
    """SPI contract for database access.

    Implementations must provide:

    * ``sync_connection`` — raw connection for cursor-based access
    * ``orm_session`` — sync SQLAlchemy ORM session context
    * ``session`` — async SQLAlchemy ORM session context
    * ``close`` — dispose all connection pools
    * ``create_all`` — create database schema (no-op for managed schemas)
    * ``seed`` — insert required seed data (no-op if not applicable)
    * ``init_database`` — activate the plugin (create schema, seed data)

    The plugin's ``__init__`` receives all connection parameters (URL,
    credentials, schema/seed flags). ``init_database`` takes no arguments —
    it simply activates the already-constructed plugin.
    """

    def sync_connection(self, datasource_name: str) -> AbstractContextManager[Any]:
        """Get a synchronous database connection.

        Yields a raw connection object with cursor() support.
        """
        ...

    def orm_session(self) -> AbstractContextManager[Session]:
        """Get a sync SQLAlchemy ORM session context.

        Yields an ORM Session with commit/rollback/close lifecycle
        managed by the context manager.
        """
        ...

    def session(self) -> AbstractAsyncContextManager[AsyncSession]:
        """Get an async SQLAlchemy session context.

        Session lifecycle (commit/rollback/close) is managed by the context
        manager.  Use as ``async with plugin.session() as session:``.
        """
        ...

    async def close(self) -> None:
        """Close all database connections and dispose connection pools."""
        ...

    def create_all(self) -> None:
        """Create the database schema (tables, indexes).

        For plugins backed by externally managed databases (e.g. ZDAS
        in production), this is a no-op.  For self-managed databases
        (e.g. SQLite), this creates all tables.
        """
        ...

    def seed(self, session: Session) -> None:
        """Insert required seed data into the database.

        Called after ``create_all()`` with an ORM session obtained via
        ``orm_session()``.  Implementations that do not require seeding
        should leave this as a no-op.

        Args:
            session: An active ORM Session.
        """
        ...

    def init_database(self) -> None:
        """Activate the plugin: create schema, seed data, and register.

        Called after construction. All connection parameters are already
        resolved in ``__init__`` — this method only performs schema creation
        (if enabled), seed data insertion (if enabled), and any plugin
        registration.
        """
        ...
