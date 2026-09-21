"""Contract tests for the generic Skill parameter-document adapter."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from agentclaw.community.core.skill_center.skill_parameter_storage import (
    DeviceFileSystemParameterStorage,
)


@pytest.mark.asyncio
async def test_device_storage_preserves_read_failure_semantics() -> None:
    device_fs = AsyncMock()
    device_fs.read_file = AsyncMock(return_value=None)
    storage = DeviceFileSystemParameterStorage(device_fs)

    assert await storage.read("/parameters.json") is None
    device_fs.read_file.assert_awaited_once_with(
        "/parameters.json",
        preserve_read_errors=True,
    )

    device_fs.read_file.side_effect = TimeoutError("runtime unavailable")
    with pytest.raises(TimeoutError, match="runtime unavailable"):
        await storage.read("/parameters.json")


@pytest.mark.asyncio
async def test_device_storage_delegates_parameter_document_writes() -> None:
    device_fs = AsyncMock()
    device_fs.write_file = AsyncMock()
    storage = DeviceFileSystemParameterStorage(device_fs)

    await storage.write("/parameters.json", b"{}")

    device_fs.write_file.assert_awaited_once_with("/parameters.json", b"{}")
