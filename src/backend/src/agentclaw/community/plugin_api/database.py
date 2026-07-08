"""DatabasePlugin — relational database connection interface.

Provides a unified context-manager based interface for database access.
Implementations are selected by deploy profile and decide the concrete
connection type:
- corp: a connection to the corp relational store (with cursor support).
- community: a SQLAlchemy connection to the configured database (SQLite /
  Postgres / MySQL).
- test/local: an in-memory SQLite SQLAlchemy ``Session``.
"""

from typing import Protocol, Any, ContextManager
from agentclaw.community.plugin_api.base import Plugin


class DatabasePlugin(Plugin, Protocol):
    """Provide a database session/connection context."""

    def session(self) -> ContextManager[Any]:
        """Return a context manager yielding a database session or connection.

        Usage:
            with db.session() as s:
                # SQLite/community: s is a SQLAlchemy Session
                # corp: s is a connection with cursor() support
                ...
        """
        ...

    def orm_session(self) -> ContextManager[Any]:
        """Return a context manager yielding a SQLAlchemy ``Session``.

        Unlike :meth:`session`, this yields a SQLAlchemy ORM ``Session`` in
        **every** runtime, so a single ORM repository body runs unchanged
        against each profile's store:

        - SQLite / community: a SQLAlchemy ``Session`` bound to the
          configured engine.
        - corp: a ``Session`` bound to a SQLAlchemy engine layered over the
          raw corp connection.

        Writes persist without an explicit commit (the corp engine runs at
        ``AUTOCOMMIT`` isolation; the SQLite/community implementations commit
        on clean context exit).

        Usage:
            with db.orm_session() as s:
                s.add(record)   # persisted on clean exit, every runtime
        """
        ...
