"""Storage contract for the historical Skill parameter document."""
from __future__ import annotations

from typing import Protocol

from agentclaw.community.core.devices.services.device_filesystem import (
    DeviceFileSystem,
)


class SkillParameterStorage(Protocol):
    """Load and save the single parameter document for one bot.

    ``None`` means the document is confirmed absent.  Other read failures must
    raise so :class:`SkillParameterService` can fail closed before any write.
    """

    async def read(self, file_path: str) -> bytes | None: ...

    async def write(self, file_path: str, content: bytes) -> None: ...


class DeviceFileSystemParameterStorage:
    """Adapt the generic device filesystem to parameter-document semantics."""

    def __init__(self, device_fs: DeviceFileSystem) -> None:
        self._device_fs = device_fs

    async def read(self, file_path: str) -> bytes | None:
        return await self._device_fs.read_file(
            file_path,
            preserve_read_errors=True,
        )

    async def write(self, file_path: str, content: bytes) -> None:
        await self._device_fs.write_file(file_path, content)
