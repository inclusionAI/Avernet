"""ArcaErrorClassifier — wraps a DeviceFileSystem to classify arca_io errors.

When an ``arca_io`` operation fails because the ARCA sandbox is unreachable
(TCP ``connection refused`` → proxy 502), the raw error is a generic
exception. This wrapper inspects the error message and re-raises it as a
:class:`DeviceConnectionUnavailableError` so callers can distinguish
transient sandbox unavailability from permanent configuration errors.

Usage::

    fs = ArcaDeviceFileSystem(...)
    fs = ArcaErrorClassifier(fs, sandbox_id="ARCA-SANDBOX-...")
    await fs.list_dir("/path")  # connection refused → DeviceConnectionUnavailableError
"""
from __future__ import annotations

import functools
from typing import Any

from agentclaw.community.core.devices.services.device_context import (
    DeviceConnectionUnavailableError,
)
from agentclaw.community.core.devices.services.device_filesystem import DeviceFileSystem
from agentclaw.community.log import get_logger

logger = get_logger()

# Substrings that indicate the ARCA sandbox is temporarily unreachable.
_ARCA_UNREACHABLE_MARKERS: tuple[str, ...] = (
    "connection refused",
    "11-50201",
)


def _is_arca_unreachable(exception: Exception) -> bool:
    """Return True when *exception* looks like a transient sandbox outage."""
    msg = str(exception)
    return any(marker in msg for marker in _ARCA_UNREACHABLE_MARKERS)


def _classify(
    sandbox_id: str,
    operation: str,
    path: str,
    exc: Exception,
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
    exc: Exception,
) -> None:
    """Re-raise *exc* as DeviceConnectionUnavailableError if appropriate."""
    if _is_arca_unreachable(exc):
        raise _classify(sandbox_id, operation, path, exc) from exc


class ArcaErrorClassifier:
    """Wraps a :class:`DeviceFileSystem` and classifies arca_io errors.

    Each filesystem operation is intercepted: if the underlying call raises
    an exception whose message matches known transient-unreachable patterns
    (``connection refused`` / ``11-50201``), it is re-raised as
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
        except Exception as exc:
            _reraise_classified(self._sandbox_id, "read_file", file_path, exc)
            raise

    async def write_file(self, file_path: str, content: bytes) -> None:
        try:
            return await self._inner.write_file(file_path, content)
        except Exception as exc:
            _reraise_classified(self._sandbox_id, "write_file", file_path, exc)
            raise

    async def delete_tree(self, dir_path: str) -> bool:
        try:
            return await self._inner.delete_tree(dir_path)
        except Exception as exc:
            _reraise_classified(self._sandbox_id, "delete_tree", dir_path, exc)
            raise

    async def delete_file(self, file_path: str) -> bool:
        try:
            return await self._inner.delete_file(file_path)
        except Exception as exc:
            _reraise_classified(self._sandbox_id, "delete_file", file_path, exc)
            raise

    async def list_dir(
        self, dir_path: str, *, recursive: bool = False
    ) -> list[dict[str, Any]] | None:
        try:
            return await self._inner.list_dir(dir_path, recursive=recursive)
        except Exception as exc:
            _reraise_classified(self._sandbox_id, "list_dir", dir_path, exc)
            raise

    async def exists(self, path: str) -> bool:
        try:
            return await self._inner.exists(path)
        except Exception as exc:
            _reraise_classified(self._sandbox_id, "exists", path, exc)
            raise
