"""SQLite ORM plugin — SQLAlchemy with SQLite dialect for testing."""

from __future__ import annotations

import datetime
import sqlite3
from collections.abc import AsyncIterator, Generator
from contextlib import AbstractContextManager, contextmanager
from typing import Any

from sqlalchemy import StaticPool, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from secbaas.community.logger import get_logger
from secbaas.community.spi.database import DatabasePluginConfig, DataSourcePlugin

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


class SqliteOrmPlugin(DataSourcePlugin):
    def __init__(self, database_url: str = "sqlite:///:memory:") -> None:
        logger.info(f"SqliteOrmPlugin initializing, database_url={database_url}")

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

        logger.info("SqliteOrmPlugin initialized successfully")

    def create_all(self) -> None:
        """Create all ORM tables.

        Imports every known ORM model module first so that
        ``Base.metadata`` is populated, then creates all tables.

        This replaces the previous bootstrap-level model import.
        """
        _orm_models = [
            "secbaas.community.core.repository.ac_bot._orm_model",
            "secbaas.community.core.repository.ac_bot_publish._orm_model",
            "secbaas.community.core.repository.api_gateway._orm_model",
            "secbaas.community.core.repository.bot._orm_model",
            "secbaas.community.core.repository.bot_device_rel._orm_model",
            "secbaas.community.core.repository.bot_run._orm_model",
            "secbaas.community.core.repository.bot_session._orm_model",
            "secbaas.community.core.repository.device._orm_model",
            "secbaas.community.core.repository.device_binding._orm_model",
            "secbaas.community.core.repository.device_template._orm_model",
            "secbaas.community.core.repository.distributed_lock._orm_model",
            "secbaas.community.core.repository.local_user_machine._orm_model",
            "secbaas.community.core.repository.publish._orm_model",
            "secbaas.community.core.repository.publish_batch._orm_model",
            "secbaas.community.core.repository.publish_record._orm_model",
            "secbaas.community.core.repository.system_config._orm_model",
            "secbaas.community.core.repository.tenant._orm_model",
            "secbaas.community.core.repository.ws_relay_session._orm_model",
        ]
        for _mod in _orm_models:
            try:
                __import__(_mod)
            except Exception as _exc:
                logger.error("Failed to import %s: %s", _mod, _exc)
                raise

        from sqlalchemy import Integer

        from secbaas.community.spi.database import Base

        for table in Base.metadata.sorted_tables:
            for col in table.primary_key.columns.values():
                if str(col.type).upper() == "BIGINT":
                    col.type = Integer()

        Base.metadata.create_all(self._sync_engine)

        logger.info("SqliteOrmPlugin: tables created")

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
        """Insert seed data required for the application to function.

        Delegates to :func:`secbaas.community.plugins.database.seed.seed_database`
        which inserts the default tenant and ARCA device template that E2E tests
        expect.  Shared with the MySQL backend so both start with identical data.
        """
        from secbaas.community.plugins.database.seed import seed_database

        seed_database(session)

    def init_database(self, config: DatabasePluginConfig) -> None:
        """Configure schema, seed data, and register with the db_manager.

        Resolves the database URL from ``DATABASE_URL`` env var, ``config.db_url``,
        or falls back to ``sqlite:///:memory:``.  Calls ``create_all()`` and
        ``seed()``, then registers this plugin with the global ``db_manager``.
        """
        import os

        resolved_url = (
            os.environ.get("DATABASE_URL") or config.db_url or "sqlite:///:memory:"
        )
        logger.info("init_database: database_url=%s", resolved_url)

        if getattr(config, "create_schema", True):
            self.create_all()
        else:
            logger.info(
                "SqliteOrmPlugin: schema creation disabled (create_schema=false)"
            )

        if getattr(config, "seed_data", True):
            with self.orm_session() as session:
                self.seed(session)
        else:
            logger.info("SqliteOrmPlugin: seed disabled (seed_data=false)")

        from secbaas.community.core.database import db_manager

        db_manager.init_plugin(self)
        logger.info("init_database: plugin registered with db_manager")

    async def close(self) -> None:
        if self._sync_engine:
            self._sync_engine.dispose()
        if self._async_engine:
            await self._async_engine.dispose()
        logger.info("SqliteOrmPlugin: both engines disposed")
