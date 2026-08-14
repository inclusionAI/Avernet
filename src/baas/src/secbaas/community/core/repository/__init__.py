"""Repository package.

Public exports:
  - OrmConnectionMixin, with_orm_session — ORM session management
  - PluginDatabaseType — backend selection enum (re-exported from spi)
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from datetime import datetime
from functools import wraps
from typing import Any, TypeVar

from secbaas.community.spi.database import PluginDatabaseType

R = TypeVar("R")

# Open ORM sessions for the current execution context, keyed by repository
# instance id.
#
# Repositories are DI singletons (``providers.Singleton`` in
# ``bootstrap/_core_repository.py``), so one instance is shared by every caller
# in the process. Storing the open session as a plain ``self._session``
# attribute was safe only while every repository call ran to completion on the
# single event-loop thread. Once callers offload repository calls to worker
# threads (``asyncio.to_thread``), two concurrent requests race: thread B
# overwrites ``self._session`` between thread A's assignment and A's query, and
# A then runs against B's session — the wrong tenant's data, or a SQLAlchemy
# error from using one session on two threads.
#
# A ContextVar removes the sharing. ``asyncio.to_thread`` runs its callable in a
# copy of the calling context, and every asyncio task gets its own copy too, so
# a session bound here is visible only to the call that opened it.
#
# Always bind by replacing the mapping (``{**current, id(self): session}``),
# never by mutating it in place: copying a context copies the reference, not the
# dict, so an in-place write would leak straight back across the very thread
# boundary this exists to close.
_active_sessions: ContextVar[dict[int, Any]] = ContextVar("orm_active_sessions")


def with_orm_session[
    **P,
    R,
](func: Callable[P, R]) -> Callable[P, R]:
    """Decorator that injects sync ORM session into self._session."""

    import time

    from secbaas.community.logger import get_logger

    _perf_logger = get_logger("orm-timing")

    @wraps(func)
    def wrapper(self: Any, *args: P.args, **kwargs: P.kwargs) -> R:
        t0 = time.monotonic()
        try:
            with self._database.orm_session() as session:
                token = _active_sessions.set(
                    {**_active_sessions.get({}), id(self): session}
                )
                try:
                    conn_id = (
                        id(session.connection().connection)
                        if session.is_active
                        else "N/A"
                    )
                    session_start = time.monotonic()
                    result = func(self, *args, **kwargs)
                    query_end = time.monotonic()
                finally:
                    _active_sessions.reset(token)
            total_end = time.monotonic()

            _perf_logger.info(
                "%s,%s,conn=%s,acquire=%.2fms,query=%.2fms,commit=%.2fms,total=%.2fms",
                type(self).__name__,
                func.__name__,
                conn_id,
                (session_start - t0) * 1000,
                (query_end - session_start) * 1000,
                (total_end - query_end) * 1000,
                (total_end - t0) * 1000,
            )
            return result
        except Exception as exc:
            elapsed = (time.monotonic() - t0) * 1000
            from ._utils import _is_expected_distributed_lock_conflict

            if _is_expected_distributed_lock_conflict(self, func, exc):
                _perf_logger.warning(
                    "%s,%s,CONFLICT,elapsed=%.2fms",
                    type(self).__name__,
                    func.__name__,
                    elapsed,
                )
            else:
                _perf_logger.error(
                    "%s,%s,FAILED,elapsed=%.2fms",
                    type(self).__name__,
                    func.__name__,
                    elapsed,
                )
            raise

    return wrapper  # type: ignore[return-value]


class OrmConnectionMixin:
    """Mixin providing ORM session access and shared utility methods.

    Provides ``_database`` and ``_session`` attributes for use with
    the ``@with_orm_session`` decorator, plus common utility methods
    (e.g. :meth:`now`) so individual ORM repositories don't need to
    re-implement the same logic.

    ``_session`` reads the session bound to this repository in the *calling*
    context, not a shared instance attribute — repositories are singletons and
    their methods run on worker threads, so an attribute would be shared by
    concurrent requests. See :data:`_active_sessions`.
    """

    _database: Any
    _datasource_name: str | None = None

    @property
    def _session(self) -> Any:
        """The ORM session opened for the current ``@with_orm_session`` call."""
        session = _active_sessions.get({}).get(id(self))
        if session is None:
            raise RuntimeError(
                f"{type(self).__name__}: no ORM session bound in this context. "
                "Repository methods must be called through @with_orm_session."
            )
        return session

    @_session.setter
    def _session(self, session: Any) -> None:
        """Bind a session for the current context (tests and direct callers).

        The binding is not unwound on scope exit — only ``with_orm_session``
        tracks a token for that. It stays context-local either way, so an
        assignment here can never be seen by another request.
        """
        _active_sessions.set({**_active_sessions.get({}), id(self): session})

    @with_orm_session
    def now(self) -> datetime:
        """Return DB server datetime via ``func.now()``.

        Opens its own session.  Do NOT call from inside another
        ``@with_orm_session`` context — use
        ``self._session.execute(func.now()).scalar()`` instead.
        """
        from sqlalchemy import func

        return self._session.execute(func.now()).scalar()


__all__ = [
    "OrmConnectionMixin",
    "PluginDatabaseType",
    "with_orm_session",
]
