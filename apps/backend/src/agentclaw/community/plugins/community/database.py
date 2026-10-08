"""CommunityDatabase — community DatabasePlugin over a configured SQLAlchemy URL.

Builds a SQLAlchemy engine from the configured database URL (SQLite file /
MySQL / Postgres) and hands out sessions. Which backend it talks to is one YAML
field — ``user_config.database.backend`` — with the DSN in
``user_config.database.url``; see ``di/config_community.py``.

Owns its schema when asked to. ``create_schema`` (default on) makes the plugin a
Lifecycle participant that runs ``core/schema.py``'s ``create_all`` at boot, the
same bootstrap the local SQLite plugin runs, because a container deployment has
nobody to apply DDL by hand before the pods start. An operator who provisions the
schema out of band (or runs migrations) sets ``create_schema: false`` and the
plugin becomes a pure connection provider again.

``session()`` does not auto-commit; ``orm_session()`` commits on clean exit
(write-persistence parity with corp's ``AUTOCOMMIT`` and the local impl).

A real, deployable implementation (not a ``MockSeam`` test double).
"""
from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin

logger = get_logger()


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def _is_mysql(url: str) -> bool:
    return url.startswith("mysql")


class CommunityDatabase(DatabasePlugin, LifecycleBase):
    """DatabasePlugin backed by a configured SQLAlchemy engine."""

    def __init__(self, url: str, *, create_schema: bool = True) -> None:
        self._url = url
        self._create_schema = create_schema
        self._engine = self._make_engine(url)
        self._session_factory = sessionmaker(
            autocommit=False, autoflush=False, bind=self._engine
        )

    @staticmethod
    def _make_engine(url: str):
        if _is_sqlite(url):
            # SQLite needs ``check_same_thread=False`` for FastAPI's threaded
            # request handling, and a per-connection ``PRAGMA foreign_keys=ON``
            # so ``ON DELETE CASCADE`` fires (parity with prod and the local
            # impl — SQLite does not enforce FKs otherwise).
            engine = create_engine(
                url, connect_args={"check_same_thread": False}
            )

            @event.listens_for(engine, "connect")
            def _set_sqlite_pragma(dbapi_conn, _conn_record):
                cursor = dbapi_conn.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

            return engine

        if _is_mysql(url):
            # A managed MySQL (Aliyun RDS and friends) closes idle connections
            # server-side — the default wait_timeout is 8h but proxies and
            # failovers cut them far sooner. Without these two the first request
            # after an idle period dies on a stale socket:
            #   pool_pre_ping — cheap liveness check on checkout, retries once;
            #   pool_recycle  — retire connections before the server does.
            return create_engine(
                url,
                pool_pre_ping=True,
                pool_recycle=3600,
            )

        # Postgres / etc.: default pooling is appropriate.
        return create_engine(url)

    async def bootstrap(self) -> None:
        """Lifecycle hook — create the schema unless the operator owns it."""
        if not self._create_schema:
            logger.info(
                "CommunityDatabase: create_schema=false — schema is operator-provisioned"
            )
            return

        from agentclaw.community.core.schema import create_all

        create_all(self._engine, mysql=_is_mysql(self._url))
        logger.info("CommunityDatabase: schema bootstrap complete")

    @contextmanager
    def session(self):
        """Yield a SQLAlchemy Session (no auto-commit)."""
        db = self._session_factory()
        try:
            yield db
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def orm_session(self):
        """Yield a SQLAlchemy Session that commits on clean exit."""
        db = self._session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def transactional_orm_session(self):
        """Reuse the existing community commit/rollback transaction."""
        return self.orm_session()
