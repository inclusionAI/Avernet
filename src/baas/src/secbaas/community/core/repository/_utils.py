from __future__ import annotations

from collections.abc import Callable
from typing import Any


def _is_expected_distributed_lock_conflict(
    instance: Any,
    func: Callable[..., Any],
    exc: Exception,
) -> bool:
    from sqlalchemy.exc import IntegrityError

    return (
        isinstance(exc, IntegrityError)
        and "OrmDistributedLockRepository" in type(instance).__name__
        and func.__name__ == "try_acquire_lock"
    )