"""Unit tests for FileService device_fs branches."""
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from agentclaw.community.core.resources.services.file_service import FileService


class TestCreateDirectoryDeviceFs:
    """Tests for create_directory when device_fs is injected."""

    @pytest.mark.asyncio
    async def test_creates_keep_via_device_fs(self, tmp_path):
        service = FileService(tmp_path)
        device_fs = MagicMock()
        device_fs.write_file = AsyncMock()

        result = await service.create_directory("new_folder", device_fs=device_fs)

        assert result["name"] == "new_folder"
        assert result["path"] == "new_folder"
        assert result["is_dir"] is True
        device_fs.write_file.assert_awaited_once()
        written_path = device_fs.write_file.call_args[0][0]
        assert written_path.endswith("new_folder/.keep")
        assert device_fs.write_file.call_args[0][1] == b""

    @pytest.mark.asyncio
    async def test_skips_exists_check_with_device_fs(self, tmp_path):
        """When device_fs is injected, existing local dir should NOT raise."""
        target = tmp_path / "existing"
        target.mkdir()
        service = FileService(tmp_path)
        device_fs = MagicMock()
        device_fs.write_file = AsyncMock()

        result = await service.create_directory("existing", device_fs=device_fs)
        assert result["is_dir"] is True

    @pytest.mark.asyncio
    async def test_without_device_fs_raises_on_existing(self, tmp_path):
        """Without device_fs, existing dir should raise ValueError."""
        target = tmp_path / "existing"
        target.mkdir()
        service = FileService(tmp_path)

        with pytest.raises(ValueError, match="already exists"):
            await service.create_directory("existing")


class TestGetFilePathDeviceFs:
    """Tests for get_file_path when device_fs is injected."""

    @pytest.mark.asyncio
    async def test_returns_bytes_from_device_fs(self, tmp_path):
        service = FileService(tmp_path)
        device_fs = MagicMock()
        device_fs.read_file = AsyncMock(return_value=b"remote content")

        result = await service.get_file_path("readme.md", device_fs=device_fs)

        assert isinstance(result, bytes)
        assert result == b"remote content"
        device_fs.read_file.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_none_from_device_fs(self, tmp_path):
        service = FileService(tmp_path)
        device_fs = MagicMock()
        device_fs.read_file = AsyncMock(return_value=None)

        result = await service.get_file_path("missing.txt", device_fs=device_fs)
        assert result is None

    @pytest.mark.asyncio
    async def test_without_device_fs_returns_path(self, tmp_path):
        target = tmp_path / "hello.txt"
        target.write_text("hi")
        service = FileService(tmp_path)

        result = await service.get_file_path("hello.txt")
        assert isinstance(result, Path)
        assert result == target

    @pytest.mark.asyncio
    async def test_without_device_fs_missing_returns_none(self, tmp_path):
        service = FileService(tmp_path)

        result = await service.get_file_path("nope.txt")
        assert result is None
