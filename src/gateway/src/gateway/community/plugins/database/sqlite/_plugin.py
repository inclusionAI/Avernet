"""Bare database plugin — SQLAlchemy with SQLite dialect for testing.

Provides a DataSourcePlugin backed by in-memory SQLite. Creates the
schema from ``Base.metadata`` and supports both sync and async sessions.
"""

from __future__ import annotations

import datetime
import sqlite3
from collections.abc import AsyncIterator, Generator
from contextlib import AbstractContextManager, asynccontextmanager, contextmanager
from typing import Any

from sqlalchemy import StaticPool, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from gateway.community.logger import get_logger
from gateway.community.spi.database import DatabasePluginConfig, DataSourcePlugin

logger = get_logger("database")

# Python 3.12+ deprecated sqlite3's default datetime adapter.
# Register an ISO format adapter globally to suppress the warning.
sqlite3.register_adapter(
    datetime.datetime,
    lambda dt: dt.isoformat(sep=" ", timespec="milliseconds"),
)


def _register_sqlite_json_functions(dbapi_connection, connection_record):
    """Register MySQL-compatible JSON functions on SQLite connections.

    SQLite 3.46+ has JSON_EXTRACT, JSON_SET built-in but not JSON_UNQUOTE.
    Since SQLite's json_extract() returns unquoted values (unlike MySQL
    where JSON_EXTRACT returns quoted strings), JSON_UNQUOTE is a no-op.
    """
    if isinstance(dbapi_connection, sqlite3.Connection):
        dbapi_connection.create_function("JSON_UNQUOTE", 1, lambda v: v)


class SqliteDatabasePlugin(DataSourcePlugin):
    """SQLite ORM plugin for bare mode.

    Uses SQLAlchemy with SQLite dialect (in-memory by default).
    Supports both sync and async sessions.
    """

    def __init__(self, database_url: str = "sqlite:///:memory:") -> None:
        logger.info("SqliteDatabasePlugin initializing, database_url=%s", database_url)

        self._sync_engine: Engine = create_engine(
            database_url,
            echo=False,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        event.listen(self._sync_engine, "connect", _register_sqlite_json_functions)
        self._sync_session_factory = sessionmaker(
            bind=self._sync_engine,
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )

        self._async_engine = None
        self._async_session_factory = None
        try:
            async_url = database_url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
            self._async_engine = create_async_engine(
                async_url,
                echo=False,
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
            self._async_session_factory = async_sessionmaker(
                bind=self._async_engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )
        except Exception:
            logger.debug(
                "Async SQLite engine not available (aiosqlite missing), "
                "async session() will raise RuntimeError"
            )

        logger.info("SqliteDatabasePlugin initialized successfully")

    def create_all(self) -> None:
        """Create all ORM tables from ``Base.metadata``."""
        from sqlalchemy import Integer

        from gateway.community.spi.database import Base

        for table in Base.metadata.sorted_tables:
            for col in table.primary_key.columns.values():
                if str(col.type).upper() == "BIGINT":
                    col.type = Integer()

        Base.metadata.create_all(self._sync_engine)
        logger.info("SqliteDatabasePlugin: tables created")

    def sync_connection(self, datasource_name: str) -> AbstractContextManager[Any]:
        raise NotImplementedError(
            "SQLite does not support cursor()-based connections. "
            "Use orm_session() for sync ORM access instead."
        )

    @contextmanager
    def orm_session(self) -> Generator[Session, None, None]:
        session = self._sync_session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        if self._async_session_factory is None:
            raise RuntimeError("Async session unavailable — install aiosqlite")
        session = self._async_session_factory()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    def seed(self, session: Session) -> None:
        """No-op seed for the bare plugin.

        Override in subclasses or add seed data when ORM models are
        introduced.
        """

    def init_database(self, config: DatabasePluginConfig) -> None:
        """Configure schema and seed data.

        Resolves the database URL from ``DATABASE_URL`` env var,
        ``config.db_url``, or falls back to ``sqlite:///:memory:``.
        Calls ``create_all()`` and ``seed()``.
        """
        import os

        resolved_url = (
            os.environ.get("DATABASE_URL") or config.db_url or "sqlite:///:memory:"
        )
        logger.info("init_database: database_url=%s", resolved_url)

        self.create_all()
        with self.orm_session() as session:
            self.seed(session)

        logger.info("init_database: schema created and seeded")

    async def close(self) -> None:
        if self._sync_engine:
            self._sync_engine.dispose()
        if self._async_engine:
            await self._async_engine.dispose()
        logger.info("SqliteDatabasePlugin: both engines disposed")
