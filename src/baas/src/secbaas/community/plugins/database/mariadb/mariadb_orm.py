"""MariaDB ORM plugin — SQLAlchemy with the MariaDB/MySQL dialect.

Wire up as ``plugins.database.plugin_database: MARIADB_ORM`` with the
``mariadb_*`` connection settings (or a ``database_url``).  Uses
``mysql+aiomysql`` for the async path and ``mysql+mysqlconnector`` for the
sync ORM path — MariaDB is wire-compatible with the MySQL protocol, so the
same driver dialects used by ``core/database/_manager.py`` apply.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator
from contextlib import AbstractContextManager, contextmanager
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from secbaas.community.logger import get_logger
from secbaas.community.spi.database import DatabasePluginConfig, DataSourcePlugin

logger = get_logger("database")


def _build_async_url(
    host: str, port: int, database: str, user: str, password: str
) -> str:
    """Build a ``mysql+aiomysql://`` URL from structured connection settings."""
    return (
        f"mysql+aiomysql://{user}:{password}@{host}:{port}/{database}?charset=utf8mb4"
    )


def _build_sync_url(
    host: str, port: int, database: str, user: str, password: str
) -> str:
    """Build a ``mysql+mysqlconnector://`` URL from structured connection settings."""
    return f"mysql+mysqlconnector://{user}:{password}@{host}:{port}/{database}?charset=utf8mb4"


class MariaDbOrmPlugin(DataSourcePlugin):
    def __init__(self, database_url: str | None = None) -> None:
        """Create a MariaDB plugin.

        Args:
            database_url: Optional ``mysql+...://`` URL.  If omitted, the URL is
                derived from the ``DatabaseConfig`` passed to ``init_database``.
                The engines are built lazily in ``init_database`` so that a bare
                ``MariaDbOrmPlugin()`` (as used by contract tests) can still be
                constructed without a live server.
        """
        self._database_url = database_url
        self._sync_engine: Engine | None = None
        self._async_engine = None
        self._sync_session_factory = None
        self._async_session_factory = None
        logger.info("MariaDbOrmPlugin constructed (engines created on init_database)")

    def _resolve_url(self, config: DatabasePluginConfig) -> str:
        """Resolve the async MariaDB URL from config/env/URL override.

        Env vars take precedence and match the existing ``DATABASE_URL``
        convention: ``MARIADB_HOST`` / ``MARIADB_PORT`` / ``MARIADB_DATABASE`` /
        ``MARIADB_USER`` / ``MARIADB_PASSWORD``.

        The generic ``db_url`` (from the base ``application.yaml``) may point at
        SQLite by default; it is only honored here when it is explicitly a
        MySQL-compatible URL, so a MariaDB backend never resolves to SQLite.
        """
        import os

        env_url = os.environ.get("DATABASE_URL")
        if env_url:
            return env_url

        if self._database_url:
            return self._database_url

        host = (
            os.environ.get("MARIADB_HOST")
            or getattr(config, "mariadb_host", None)
            or "127.0.0.1"
        )
        port = int(
            os.environ.get("MARIADB_PORT")
            or getattr(config, "mariadb_port", None)
            or 3306
        )
        database = (
            os.environ.get("MARIADB_DATABASE")
            or getattr(config, "mariadb_database", "")
            or ""
        )
        user = (
            os.environ.get("MARIADB_USER") or getattr(config, "mariadb_user", "") or ""
        )
        password = (
            os.environ.get("MARIADB_PASSWORD")
            or getattr(config, "mariadb_password", "")
            or ""
        )
        if database:
            return _build_async_url(host, port, database, user, password)

        db_url = getattr(config, "db_url", "")
        if db_url and not db_url.startswith("sqlite://"):
            return db_url

        if not database:
            raise RuntimeError(
                "MariaDB backend requires a database — set MARIADB_DATABASE, "
                "plugins.database.mariadb_database, or a mysql+:// database_url"
            )
        return _build_async_url(host, port, database, user, password)

    def _init_engines(self, async_url: str) -> None:
        """Build sync (mysqlconnector) and async (aiomysql) engines.

        The sync URL is derived from the async URL host/port/database by
        swapping the dialect.  The original user/password components are reused
        to avoid re-encoding credentials.
        """
        from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

        parts = urlsplit(async_url)
        query = parse_qs(parts.query)
        query["charset"] = ["utf8mb4"]
        sync_url = urlunsplit(
            (
                "mysql+mysqlconnector",
                parts.netloc,
                parts.path,
                urlencode(query, doseq=True),
                parts.fragment,
            )
        )

        self._sync_engine = create_engine(sync_url, echo=False, pool_pre_ping=True)
        self._sync_session_factory = sessionmaker(
            bind=self._sync_engine,
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )

        self._async_engine = create_async_engine(
            async_url,
            echo=False,
            pool_pre_ping=True,
        )
        self._async_session_factory = async_sessionmaker(
            bind=self._async_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        logger.info("MariaDbOrmPlugin engines initialized")

    def create_all(self) -> None:
        """Create all ORM tables.

        Imports every known ORM model module (same set as SqliteOrmPlugin) so
        that ``Base.metadata`` is populated, then calls
        ``Base.metadata.create_all()`` using the native MySQL/MariaDB dialect.
        No SQLite shims are applied — MariaDB keeps native JSON columns and
        ``BIGINT`` primary keys.
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

        from secbaas.community.spi.database import Base

        Base.metadata.create_all(self._sync_engine)

        logger.info("MariaDbOrmPlugin: tables created")

    def sync_connection(self, datasource_name: str) -> AbstractContextManager[Any]:
        if self._sync_engine is None:
            raise RuntimeError(
                "MariaDB plugin not initialized — call init_database first"
            )

        @contextmanager
        def _conn() -> AbstractContextManager[Any]:
            raw = self._sync_engine.raw_connection()
            try:
                yield raw
            finally:
                raw.close()

        return _conn()

    @contextmanager
    def orm_session(self) -> Generator[Session, None, None]:
        if self._sync_session_factory is None:
            raise RuntimeError(
                "MariaDB plugin not initialized — call init_database first"
            )
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
            raise RuntimeError(
                "MariaDB plugin not initialized — call init_database first"
            )
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

        Reuses the shared backend-agnostic seed logic (default tenant and ARCA
        device template) that the SQLite backend uses, so both backends start
        with identical seed data.
        """
        from secbaas.community.plugins.database.seed import seed_database

        seed_database(session)

    def init_database(self, config: DatabasePluginConfig) -> None:
        """Configure engines, create schema, seed data, and register.

        Resolves the async URL from ``DATABASE_URL`` env, ``config.db_url``, or
        the structured ``mariadb_*`` settings.  Then builds the sync + async
        engines, creates the schema, seeds, and registers with ``db_manager``.
        Credentials are never logged.
        """
        async_url = self._resolve_url(config)
        self._init_engines(async_url)

        if getattr(config, "create_schema", True):
            self.create_all()
        else:
            logger.info(
                "MariaDbOrmPlugin: schema creation disabled (create_schema=false)"
            )

        if getattr(config, "seed_data", True):
            with self.orm_session() as session:
                self.seed(session)
        else:
            logger.info("MariaDbOrmPlugin: seed disabled (seed_data=false)")

        from secbaas.community.core.database import db_manager

        db_manager.init_plugin(self)
        logger.info(
            "init_database: MariaDbOrmPlugin registered with db_manager (database=%s)",
            self._resolve_database_label(async_url),
        )

    @staticmethod
    def _resolve_database_label(async_url: str) -> str:
        """Return a log-safe label (host/database only, never credentials)."""
        from urllib.parse import urlsplit

        parts = urlsplit(async_url)
        hostport = parts.netloc.rsplit("@", 1)[-1]
        return f"{hostport}{parts.path}"

    async def close(self) -> None:
        if self._sync_engine:
            self._sync_engine.dispose()
        if self._async_engine:
            await self._async_engine.dispose()
        logger.info("MariaDbOrmPlugin: engines disposed")
