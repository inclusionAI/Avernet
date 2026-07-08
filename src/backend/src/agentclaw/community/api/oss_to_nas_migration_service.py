"""Service API Protocol for OSS-to-NAS file migration."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class OssToNasMigrationServiceProtocol(Protocol):
    """Service API for migrating OSS-backed bot data into NAS."""

    def oss_path_to_nas_path(self, oss_file_path: Path) -> Path | None: ...

    def migrate(self, *args: Any, **kwargs: Any) -> Any: ...
