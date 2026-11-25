"""Persistent community SQLite plugin; selected by the composition root.

Unlike the local in-memory MockSeam, this plugin honors a configured SQLite
file URL. It owns SQLite connection pragmas and the write-transaction boundary;
the shared SQLAlchemy plugin owns sessions and schema lifecycle.
"""

from contextlib import contextmanager

from sqlalchemy import create_engine, event

from agentclaw.community.plugins.community.database import (
    CommunityDatabase,
    _check_security_schema,
)


class CommunitySqliteDatabase(CommunityDatabase):
    """SQLite-specific connection, schema validation and write reservation."""

    @staticmethod
    def _make_engine(url: str):
        engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def enable_foreign_keys(connection, _record):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        return engine

    def _validate_security_schema(self) -> None:
        if self._security_schema_required:
            _check_security_schema(self._engine, dialect="SQLite")

    @contextmanager
    def transactional_orm_session(self):
        """Reserve the writer before reads and keep SAVEPOINTs in the UoW."""
        with self.orm_session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            yield session
