"""ArcaErrorClassifier — wraps a DeviceFileSystem to classify arca_io errors.

When an ``arca_io`` operation fails because the ARCA sandbox is unreachable
(TCP ``connection refused`` → proxy 502), the raw error is a generic
exception. This wrapper inspects the error and re-raises it as a
:class:`DeviceConnectionUnavailableError` so callers can distinguish
transient sandbox unavailability from permanent configuration errors.

Usage::

    fs = ArcaDeviceFileSystem(...)
    fs = ArcaErrorClassifier(fs, sandbox_id="ARCA-SANDBOX-...")
    await fs.list_dir("/path")  # connection refused → DeviceConnectionUnavailableError
"""
from __future__ import annotations

import errno
from typing import Any

from agentclaw.community.core.devices.services.device_context import (
    DeviceConnectionUnavailableError,
)
from agentclaw.community.core.devices.services.device_filesystem import DeviceFileSystem
from agentclaw.community.log import get_logger

logger = get_logger()

# Substrings that indicate the ARCA sandbox is temporarily unreachable.
# Matched case-insensitively against str(exc) and the __cause__ chain.
# "11-50201" is the proxy error code returned by agentclawproxy when
# the downstream sandbox is unreachable (see trace 0b95c0dd…).
_ARCA_UNREACHABLE_MARKERS: tuple[str, ...] = (
    "connection refused",
    "11-50201",
)

# Exception types whose errno equals ECONNREFUSED signal a transient
# sandbox outage regardless of the message text.
_ECONNREFUSED_TYPES: tuple[type, ...] = (
    ConnectionRefusedError,
)


def _is_arca_unreachable(exception: BaseException) -> bool:
    """Return True when *exception* signals a transient sandbox outage.

    Checks in order:
    1. Exception type — ``ConnectionRefusedError`` with errno==ECONNREFUSED.
    2. ``str(exc)`` — case‑insensitive substring match against known markers.
    3. ``__cause__`` chain — recurse into chained exceptions.
    """
    # 1. Type-based: ConnectionRefusedError with ECONNREFUSED.
    if isinstance(exception, _ECONNREFUSED_TYPES):
        if getattr(exception, "errno", None) == errno.ECONNREFUSED:
            return True

    # 2. Text-based: case‑insensitive substring match.
    msg = str(exception).casefold()
    if any(marker in msg for marker in _ARCA_UNREACHABLE_MARKERS):
        return True

    # 3. Walk the __cause__ chain.
    cause = exception.__cause__
    while cause is not None:
        if isinstance(cause, _ECONNREFUSED_TYPES):
            if getattr(cause, "errno", None) == errno.ECONNREFUSED:
                return True
        cause_msg = str(cause).casefold()
        if any(marker in cause_msg for marker in _ARCA_UNREACHABLE_MARKERS):
            return True
        cause = cause.__cause__

    return False


def _classify(
    sandbox_id: str,
    operation: str,
    path: str,
    exc: BaseException,
) -> DeviceConnectionUnavailableError:
    """Build a classified error with sandbox / operation context."""
    detail = str(exc)[:300]
    logger.warning(
        "[arca-error-classifier] %s → DeviceConnectionUnavailableError: "
        "sandbox_id=%s path=%s cause=%s",
        operation, sandbox_id, path, detail,
    )
    return DeviceConnectionUnavailableError(
        f"ARCA sandbox temporarily unreachable: sandbox_id={sandbox_id}, "
        f"operation={operation}, path={path}. "
        f"Upstream cause: {detail}"
    )


def _reraise_classified(
    sandbox_id: str,
    operation: str,
    path: str,
    exc: BaseException,
) -> None:
    """Re-raise *exc* as DeviceConnectionUnavailableError if appropriate."""
    if _is_arca_unreachable(exc):
        raise _classify(sandbox_id, operation, path, exc) from exc


class ArcaErrorClassifier:
    """Wraps a :class:`DeviceFileSystem` and classifies arca_io errors.

    Each filesystem operation is intercepted: if the underlying call raises
    an exception that signals a transient sandbox outage, it is re-raised as
    :class:`DeviceConnectionUnavailableError`; all other exceptions pass
    through unchanged.
    """

    def __init__(self, inner: DeviceFileSystem, *, sandbox_id: str) -> None:
        self._inner = inner
        self._sandbox_id = sandbox_id

    async def read_file(
        self, file_path: str, **kwargs: Any
    ) -> bytes | None:
        try:
            return await self._inner.read_file(file_path, **kwargs)
        except BaseException as exc:
            _reraise_classified(self._sandbox_id, "read_file", file_path, exc)
            raise

    async def write_file(self, file_path: str, content: bytes) -> None:
        try:
            return await self._inner.write_file(file_path, content)
        except BaseException as exc:
            _reraise_classified(self._sandbox_id, "write_file", file_path, exc)
            raise

    async def delete_tree(self, dir_path: str) -> bool:
        try:
            return await self._inner.delete_tree(dir_path)
        except BaseException as exc:
            _reraise_classified(self._sandbox_id, "delete_tree", dir_path, exc)
            raise

    async def delete_file(self, file_path: str) -> bool:
        try:
            return await self._inner.delete_file(file_path)
        except BaseException as exc:
            _reraise_classified(self._sandbox_id, "delete_file", file_path, exc)
            raise

    async def list_dir(
        self, dir_path: str, *, recursive: bool = False
    ) -> list[dict[str, Any]] | None:
        try:
            return await self._inner.list_dir(dir_path, recursive=recursive)
        except BaseException as exc:
            _reraise_classified(self._sandbox_id, "list_dir", dir_path, exc)
            raise

    async def exists(self, path: str) -> bool:
        try:
            return await self._inner.exists(path)
        except BaseException as exc:
            _reraise_classified(self._sandbox_id, "exists", path, exc)
            raise
