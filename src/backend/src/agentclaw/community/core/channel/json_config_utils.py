"""
JSON configuration file CRUD utilities with support for nested keys.

New architecture version - uses DeviceFileSystem for file operations.

Usage:
    # For simple one-off operations (auto-save)
    await JsonConfigUtils.set("config.json", "key.subkey", "value", device_fs)

    # For multiple modifications (manual save)
    config = await JsonConfigFile.load("config.json", device_fs)
    config.set("key1.subkey", "value1")
    config.set("key2.subkey", "value2")
    await config.save()  # Write all changes at once
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Union, TYPE_CHECKING

if TYPE_CHECKING:
    from agentclaw.community.core.devices.services.device_filesystem import DeviceFileSystem

from agentclaw.community.log import get_logger

logger = get_logger()


def _is_local_mode(device_fs: "DeviceFileSystem | None") -> bool:
    """Check if using LocalDeviceFileSystem (local mode).

    Args:
        device_fs: DeviceFileSystem instance

    Returns:
        True if device_fs is LocalDeviceFileSystem or None (fallback to local fs)
    """
    if device_fs is None:
        return True  # No device_fs means fallback to local filesystem
    return type(device_fs).__name__ == "LocalDeviceFileSystem"


def _get_local_base_dir() -> Path:
    """Get local mode base directory: ${HOME}/.openclaw"""
    return Path.home() / ".openclaw"


def _rewrite_path_for_local(file_path: Union[str, Path], device_fs: "DeviceFileSystem | None" = None) -> Path:
    """Rewrite file path for local mode.

    In local mode, the file path is rewritten to:
        ${HOME}/.openclaw/{filename}

    Args:
        file_path: Original file path
        device_fs: DeviceFileSystem instance (used to determine local mode)

    Returns:
        Rewritten path for local mode, or original path if not in local mode
    """
    if not _is_local_mode(device_fs):
        return Path(file_path)

    path = Path(file_path)
    local_base = _get_local_base_dir()
    new_path = local_base / path.name
    logger.info(f"[json_config_utils] Rewrote path for local mode: {path} -> {new_path}")
    return new_path


class JsonConfigFile:
    """JSON configuration file editor with in-memory modifications and explicit save.

    Usage:
        config = await JsonConfigFile.load("config.json", device_fs)
        config.set("channels.dingtalk.xxx.client_id", "new_id")
        config.set("channels.dingtalk.xxx.dm_policy", "disabled")
        await config.save()  # All changes written at once
    """

    def __init__(self, file_path: Union[str, Path], data: dict[str, Any], device_fs: "DeviceFileSystem | None" = None) -> None:
        """Initialize JsonConfigFile.

        Args:
            file_path: Path to JSON file
            data: Parsed JSON data
            device_fs: DeviceFileSystem instance (optional, falls back to local fs)
        """
        self.file_path = Path(file_path)
        self._data = data
        self._modified = False
        self._device_fs = device_fs

    @classmethod
    async def load(
        cls,
        file_path: Union[str, Path],
        device_fs: "DeviceFileSystem | None" = None
    ) -> "JsonConfigFile":
        """Load JSON file into JsonConfigFile instance.

        Args:
            file_path: Path to JSON file
            device_fs: DeviceFileSystem instance (optional, falls back to local fs)

        Returns:
            JsonConfigFile instance

        Raises:
            FileNotFoundError: If file does not exist
            json.JSONDecodeError: If file is not valid JSON
        """
        # Rewrite path for local mode
        path = _rewrite_path_for_local(file_path, device_fs)

        if device_fs:
            # Use DeviceFileSystem (local or Arca)
            logger.info(f"[JsonConfigFile.load] Using DeviceFileSystem: path={str(path)}")
            content_bytes = await device_fs.read_file(str(path))
            if content_bytes is None:
                raise FileNotFoundError(f"JSON config file not found: {path}")
            content = content_bytes.decode('utf-8')
            data = json.loads(content)
            return cls(path, data, device_fs)

        # Fallback to local filesystem
        if not path.exists():
            raise FileNotFoundError(f"JSON config file not found: {path}")

        content = path.read_text(encoding="utf-8")
        data = json.loads(content)
        return cls(path, data)

    async def save(self, indent: int = 2) -> None:
        """Save all modifications to file.

        Args:
            indent: JSON indentation level
        """
        if self._modified:
            content = json.dumps(self._data, indent=indent, ensure_ascii=False)

            if self._device_fs:
                # Use DeviceFileSystem (local or Arca)
                logger.info(f"[JsonConfigFile.save] Using DeviceFileSystem: path={str(self.file_path)}")
                await self._device_fs.write_file(str(self.file_path), content.encode('utf-8'))
            else:
                # Fallback to local filesystem
                self.file_path.parent.mkdir(parents=True, exist_ok=True)
                self.file_path.write_text(content, encoding="utf-8")

            self._modified = False

    @staticmethod
    def _parse_key_path(key_path: str) -> list[str]:
        """Parse dot notation key path."""
        return key_path.split(".")

    def _get_nested(self, keys: list[str]) -> Any:
        """Get nested value from data."""
        current = self._data
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return None
        return current

    def _set_nested(self, keys: list[str], value: Any) -> None:
        """Set nested value in data, creating dicts if needed."""
        current = self._data
        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]
        current[keys[-1]] = value
        self._modified = True

    def get(self, key_path: str, default: Any = None) -> Any:
        """Get value by dot notation key path.

        Args:
            key_path: Dot notation path like "channels.dingtalk.xxx.client_id"
            default: Default value if key not found

        Returns:
            Value or default
        """
        keys = self._parse_key_path(key_path)
        value = self._get_nested(keys)
        return value if value is not None else default

    def set(self, key_path: str, value: Any) -> None:
        """Set value by dot notation key path (in-memory, no save yet).

        Args:
            key_path: Dot notation path
            value: Value to set
        """
        keys = self._parse_key_path(key_path)
        self._set_nested(keys, value)

    def delete(self, key_path: str) -> bool:
        """Delete key by dot notation path (in-memory, no save yet).

        Args:
            key_path: Dot notation path

        Returns:
            True if deleted, False if not found
        """
        keys = self._parse_key_path(key_path)
        current = self._data

        for key in keys[:-1]:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return False

        last_key = keys[-1]
        if isinstance(current, dict) and last_key in current:
            del current[last_key]
            self._modified = True
            return True
        return False

    def exists(self, key_path: str) -> bool:
        """Check if key exists.

        Args:
            key_path: Dot notation path

        Returns:
            True if exists
        """
        return self.get(key_path) is not None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return dict(self._data)

    def dumps(self, indent: int = 2) -> str:
        """Serialize to JSON string."""
        return json.dumps(self._data, indent=indent, ensure_ascii=False)


class JsonConfigUtils:
    """JSON config file CRUD utility class (one-off operations with auto-save).

    For multiple modifications, use JsonConfigFile instead:
        config = await JsonConfigFile.load("config.json", device_fs)
        config.set(...)
        await config.save()
    """

    @staticmethod
    async def load(
        file_path: Union[str, Path],
        device_fs: "DeviceFileSystem | None" = None
    ) -> dict[str, Any]:
        """Load JSON file.

        Args:
            file_path: Path to JSON file
            device_fs: DeviceFileSystem instance (optional, falls back to local fs)

        Returns:
            Parsed JSON as dict

        Raises:
            FileNotFoundError: If file does not exist
        """
        # Rewrite path for local mode
        path = _rewrite_path_for_local(file_path, device_fs)

        if device_fs:
            # Use DeviceFileSystem (local or Arca)
            logger.info(f"[JsonConfigUtils.load] Using DeviceFileSystem: path={str(path)}")
            content_bytes = await device_fs.read_file(str(path))
            if content_bytes is None:
                raise FileNotFoundError(f"JSON config file not found: {path}")
            content = content_bytes.decode('utf-8')
            return json.loads(content)

        # Fallback to local filesystem
        if not path.exists():
            raise FileNotFoundError(f"JSON config file not found: {path}")

        content = path.read_text(encoding="utf-8")
        return json.loads(content)

    @staticmethod
    def dump(file_path: Union[str, Path], data: dict[str, Any], indent: int = 2) -> None:
        """Save data to JSON file (local filesystem only).

        Args:
            file_path: Path to save
            data: Data to save
            indent: JSON indentation level
        """
        # Rewrite path for local mode (None means local filesystem)
        path = _rewrite_path_for_local(file_path, None)
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(data, indent=indent, ensure_ascii=False)
        path.write_text(content, encoding="utf-8")

    @staticmethod
    async def get(
        file_path: Union[str, Path],
        key_path: str,
        default: Any = None,
        device_fs: "DeviceFileSystem | None" = None
    ) -> Any:
        """Get value by dot notation key path.

        Args:
            file_path: JSON file path
            key_path: Dot notation path like "channels.dingtalk.xxx.client_id"
            default: Default value if key not found
            device_fs: DeviceFileSystem instance (optional)

        Returns:
            Value or default
        """
        data = await JsonConfigUtils.load(file_path, device_fs)
        keys = key_path.split(".")
        current = data
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return default
        return current

    @staticmethod
    async def set(
        file_path: Union[str, Path],
        key_path: str,
        value: Any,
        indent: int = 2,
        device_fs: "DeviceFileSystem | None" = None,
    ) -> None:
        """Set value by dot notation key path.

        Args:
            file_path: JSON file path
            key_path: Dot notation path
            value: Value to set
            indent: JSON indentation level
            device_fs: DeviceFileSystem instance (optional)
        """
        config = await JsonConfigFile.load(file_path, device_fs)
        config.set(key_path, value)
        await config.save(indent=indent)

    @staticmethod
    async def delete(
        file_path: Union[str, Path],
        key_path: str,
        indent: int = 2,
        device_fs: "DeviceFileSystem | None" = None
    ) -> bool:
        """Delete key by dot notation path.

        Args:
            file_path: JSON file path
            key_path: Dot notation path
            indent: JSON indentation level
            device_fs: DeviceFileSystem instance (optional)

        Returns:
            True if deleted, False if not found
        """
        config = await JsonConfigFile.load(file_path, device_fs)
        result = config.delete(key_path)
        if result:
            await config.save(indent=indent)
        return result

    @staticmethod
    async def exists(
        file_path: Union[str, Path],
        key_path: str,
        device_fs: "DeviceFileSystem | None" = None
    ) -> bool:
        """Check if key exists.

        Args:
            file_path: JSON file path
            key_path: Dot notation path
            device_fs: DeviceFileSystem instance (optional)

        Returns:
            True if exists
        """
        try:
            return await JsonConfigUtils.get(file_path, key_path, device_fs=device_fs) is not None
        except FileNotFoundError:
            return False

    @staticmethod
    async def batch_set(
        file_path: Union[str, Path],
        updates: dict[str, Any],
        indent: int = 2,
        device_fs: "DeviceFileSystem | None" = None,
    ) -> None:
        """Batch set values.

        Args:
            file_path: JSON file path
            updates: Dict of {key_path: value}
            indent: JSON indentation level
            device_fs: DeviceFileSystem instance (optional)
        """
        config = await JsonConfigFile.load(file_path, device_fs)
        for key_path, value in updates.items():
            config.set(key_path, value)
        await config.save(indent=indent)
