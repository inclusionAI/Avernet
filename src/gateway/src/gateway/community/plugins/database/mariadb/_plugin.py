"""MariaDB ORM plugin — SQLAlchemy with the MariaDB/MySQL dialect.

Wire up as ``plugins.database.plugin_database: MARIADB_ORM`` with the
``mariadb_*`` connection settings (or a ``database_url``).  Uses
``mysql+aiomysql`` for the async path and ``mysql+mysqlconnector`` for the
sync ORM path — MariaDB is wire-compatible with the MySQL protocol, so the
same driver dialects used by the baas ``mariadb_orm.py`` apply.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator
from contextlib import AbstractContextManager, contextmanager
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from gateway.community.logger import get_logger
from gateway.community.spi.database import DatabasePluginConfig, DataSourcePlugin

logger = get_logger("database")


def _build_async_url(
    host: str, port: int, database: str, user: str, password: str
) -> str:
    """Build a ``mysql+aiomysql://`` URL from structured connection settings."""
    return (
        f"mysql+aiomysql://{user}:{password}@{host}:{port}/{database}?charset=utf8mb4"
    )


class MariaDbOrmPlugin(DataSourcePlugin):
    """MariaDB/MySQL ORM plugin for the community gateway.

    Supports the two-tier connection model: a sync ``mysql+mysqlconnector``
    engine for cursor/ORM access and an async ``mysql+aiomysql`` engine
    for async SQLAlchemy sessions. Engines are built lazily in
    ``init_database`` so a bare ``MariaDbOrmPlugin()`` (as used by contract
    tests) can be constructed without a live server.
    """

    def __init__(self, database_url: str | None = None) -> None:
        """Create a MariaDB plugin.

        Args:
            database_url: Optional ``mysql+...://`` URL.  If omitted, the URL is
                derived from the ``DatabaseConfig`` passed to ``init_database``.
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
        return _build_async_url(
            host, port, database, user, password
        )  # pragma: no cover

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
        """Create the ORM tables owned by the gateway.

        Imports the gateway-owned core ORM model modules (access_key, app,
        tenant) so their tables are registered on ``Base.metadata``, then creates
        exactly those tables using the native MySQL/MariaDB dialect. No SQLite
        shims are applied — MariaDB keeps native ``JSON`` and ``BIGINT``.

        ``bcs_bots`` is intentionally NOT provisioned here: the bcs service owns
        and writes that table — the gateway only reads it (see the note in
        ``migrations/``). Because ``Base.metadata`` is a shared registry,
        ``create_all`` whitelists the gateway identity tables so a transitively
        registered ``bcs_bots`` is never created.
        """
        _orm_models = [
            "gateway.community.core.access_key._orm",
            "gateway.community.core.app._orm",
            "gateway.community.core.tenant._orm",
        ]
        for _mod in _orm_models:
            try:
                __import__(_mod)
            except Exception as _exc:  # pragma: no cover - defensive import guard
                logger.error("Failed to import %s: %s", _mod, _exc)
                raise

        if self._sync_engine is None:
            raise RuntimeError(
                "MariaDB plugin not initialized — call init_database first"
            )

        _owned_tables = {
            "avernet_application",
            "avernet_tenant",
            "avernet_access_key_token",
        }
        from gateway.community.spi.database import Base

        _tables = [t for t in Base.metadata.sorted_tables if t.name in _owned_tables]
        if not _tables:
            raise RuntimeError("No gateway-owned ORM tables registered")
        for _table in _tables:
            _table.create(self._sync_engine, checkfirst=True)

        logger.info(
            "MariaDbOrmPlugin: tables created (%s)",
            ", ".join(sorted(_owned_tables)),
        )

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
        """No-op — seed data is inserted by the bootstrap composition root.

        The SPI contract keeps ``seed`` for plugins that own self-contained
        seed data; the community MariaDB plugin's seed rows would reference core
        ORM models, so the actual seeding lives in ``bootstrap._database`` (the
        composition root, which may import core) to respect layer rules.
        """

    def init_database(self, config: DatabasePluginConfig) -> None:
        """Configure engines and optionally create the schema.

        Resolves the async URL from ``DATABASE_URL`` env, ``config.db_url``, or
        the structured ``mariadb_*`` settings, then builds the sync + async
        engines and creates the schema only if ``create_schema`` (default:
        false) is enabled. Seeding is left to the bootstrap composition root.
        Credentials are never logged.
        """
        async_url = self._resolve_url(config)
        self._init_engines(async_url)

        if getattr(config, "create_schema", False):
            self.create_all()
        else:
            logger.info(
                "MariaDbOrmPlugin: schema creation disabled (create_schema=false)"
            )

        logger.info(
            "init_database: MariaDbOrmPlugin initialized (database=%s)",
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


__all__ = ["MariaDbOrmPlugin"]
