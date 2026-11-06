"""Server-issued scope for one device creation/replacement operation.

This is an in-process capability, not an HTTP field or sandbox metadata.
ContextVar propagation through asyncio.to_thread keeps the trusted identity
available at a synchronous infrastructure boundary without widening its SPI.
"""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceCreationContext:
    tenant: str
    env: str
    device_uuid: str
    operation_id: str
    previous_provider_id: str | None


_creation_context: ContextVar[DeviceCreationContext | None] = ContextVar(
    "device_creation_context", default=None
)


def get_device_creation_context() -> DeviceCreationContext | None:
    """Return the current business operation; absent means no recovery grant."""
    return _creation_context.get()


@contextmanager
def device_creation_scope(
    context: DeviceCreationContext | None,
) -> Generator[None, None, None]:
    """Bind an identity, or explicitly disable recovery, then restore the scope."""
    token = _creation_context.set(context)
    try:
        yield
    finally:
        _creation_context.reset(token)
