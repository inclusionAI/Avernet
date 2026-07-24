"""Repository package.

Public exports:
  - OrmConnectionMixin, with_orm_session — ORM session management
  - PluginDatabaseType — backend selection enum (re-exported from spi)
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from functools import wraps
from typing import Any, TypeVar

from secbaas.spi.database import PluginDatabaseType

R = TypeVar("R")


def _is_expected_distributed_lock_conflict(
    instance: Any,
    func: Callable[..., Any],
    exc: Exception,
) -> bool:
    from sqlalchemy.exc import IntegrityError

    return (
        isinstance(exc, IntegrityError)
        and "OrmDistributedLockRepository" in type(instance).__name__
        and func.__name__ == "insert_lock"
    )


def with_orm_session[
    **P,
    R,
](func: Callable[P, R]) -> Callable[P, R]:
    """Decorator that injects sync ORM session into self._session."""

    import time

    from secbaas.logger import get_logger

    _perf_logger = get_logger("orm-timing")

    @wraps(func)
    def wrapper(self: Any, *args: P.args, **kwargs: P.kwargs) -> R:
        t0 = time.monotonic()
        try:
            with self._database.orm_session() as session:
                self._session = session
                conn_id = (
                    id(session.connection().connection) if session.is_active else "N/A"
                )
                session_start = time.monotonic()
                result = func(self, *args, **kwargs)
                query_end = time.monotonic()
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
    """

    _database: Any
    _session: Any
    _datasource_name: str | None = None

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
