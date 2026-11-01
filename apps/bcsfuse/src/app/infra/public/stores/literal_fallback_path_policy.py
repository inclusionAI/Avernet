"""Deployment boundary for the literal fallback snapshot path."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Union


class EphemeralSnapshotPathError(ValueError):
    """The runtime snapshot path cannot guarantee persistence across pod restarts."""


class LiteralFallbackSnapshotPathPolicy:
    """Validate that a runtime snapshot path is explicitly persistent."""

    _EPHEMERAL_ROOTS = (
        "/tmp",
        "/private/tmp",
        "/var/tmp",
        "/run",
        "/var/run",
        "/dev/shm",
        "/var/folders",
        "/Users/Shared",
    )

    def __init__(self, *, require_explicit: bool = True) -> None:
        self.require_explicit = require_explicit

    def validate_runtime_path(self, value: Union[str, Path, os.PathLike[str]]) -> Path:
        if value is None:
            raise EphemeralSnapshotPathError(
                "runtime literal fallback snapshot path is required"
            )

        raw_path = os.fspath(value)
        if not raw_path:
            raise EphemeralSnapshotPathError(
                "runtime literal fallback snapshot path is required"
            )

        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            raise EphemeralSnapshotPathError(
                "runtime literal fallback snapshot path must be absolute"
            )

        resolved = path.resolve(strict=False)
        if self._is_ephemeral(resolved):
            raise EphemeralSnapshotPathError(
                f"runtime literal fallback snapshot path is ephemeral: {resolved}"
            )

        return resolved

    @classmethod
    def _is_ephemeral(cls, path: Path) -> bool:
        if not path.is_absolute():
            return True
        for root in cls._EPHEMERAL_ROOTS:
            root_parts = Path(root).parts
            if root_parts and path.parts[: len(root_parts)] == root_parts:
                return True
        return False


__all__ = [
    "EphemeralSnapshotPathError",
    "LiteralFallbackSnapshotPathPolicy",
]
