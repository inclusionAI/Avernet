"""Request-local admission policy for an authorized Manifest apply."""

from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterator


_manifest_install = ContextVar("manifest_mcp_install", default=False)


@contextmanager
def manifest_mcp_install_scope() -> Iterator[None]:
    token = _manifest_install.set(True)
    try:
        yield
    finally:
        _manifest_install.reset(token)


def is_manifest_mcp_install() -> bool:
    return _manifest_install.get()
