"""CommunityDatabase — community DatabasePlugin over a configured SQLAlchemy URL.

A pure connection provider: it builds a SQLAlchemy engine from the configured
database URL (SQLite file / Postgres / MySQL) and hands out sessions. It does
**not** create tables and is **not** a lifecycle participant — in a community
deployment the schema is operator-provisioned (the operator runs their own DDL /
migrations before the app boots). The local SQLite impl's ``create_all`` exists
only because its in-memory database is recreated empty on every process start;
that does not apply to a persistent community store.

``session()`` does not auto-commit; ``orm_session()`` commits on clean exit
(write-persistence parity with corp's ``AUTOCOMMIT`` and the local impl).

A real, deployable implementation (not a ``MockSeam`` test double).
"""
from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from agentclaw.community.plugin_api.database import DatabasePlugin


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


class CommunityDatabase(DatabasePlugin):
    """DatabasePlugin backed by a configured SQLAlchemy engine."""

    def __init__(self, url: str) -> None:
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
        # Postgres / MySQL / etc.: default pooling is appropriate.
        return create_engine(url)

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
