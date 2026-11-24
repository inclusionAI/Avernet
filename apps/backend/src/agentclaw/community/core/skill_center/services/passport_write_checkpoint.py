"""Carry the guarded projection's write check to the Passport RPC boundary."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar


_WRITE_CHECK: ContextVar[Callable[[], None] | None] = ContextVar(
    "passport_write_check", default=None,
)


@contextmanager
def passport_write_checkpoint(check: Callable[[], None]) -> Iterator[None]:
    token = _WRITE_CHECK.set(check)
    try:
        yield
    finally:
        _WRITE_CHECK.reset(token)


def verify_passport_write() -> None:
    check = _WRITE_CHECK.get()
    if check is not None:
        check()
