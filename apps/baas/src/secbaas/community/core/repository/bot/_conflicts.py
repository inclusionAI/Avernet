"""Translate duplicate bot slots only, after the database scope has exited."""

import sqlite3
from collections.abc import Callable
from functools import wraps

from sqlalchemy.exc import IntegrityError

from ._protocol import BotRecordConflictError


def bot_slot_conflict[**P, R](func: Callable[P, R]) -> Callable[P, R]:
    @wraps(func)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return func(*args, **kwargs)
        except IntegrityError as exc:
            original = exc.orig
            code = getattr(original, "sqlite_errorcode", None)
            mysql_code = original.args[0] if original.args else None
            if code == sqlite3.SQLITE_CONSTRAINT_UNIQUE or mysql_code == 1062:
                raise BotRecordConflictError(
                    "Bot record already exists in the requested live slot"
                ) from exc
            raise

    return wrapped
