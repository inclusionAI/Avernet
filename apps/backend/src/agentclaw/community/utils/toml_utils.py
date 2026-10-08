"""
TOML file CRUD utilities with support for nested tables.
Supports formats like [channels.dingtalk.semanticflowali]

Usage:
    # For simple one-off operations (auto-save)
    TomlUtils.set("config.toml", "key", "value")

    # For multiple modifications (manual save)
    toml = TomlFile.load("config.toml")
    toml.set("key1", "value1")
    toml.set("key2", "value2")
    toml.save()  # Write all changes at once
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Union

import tomlkit
from tomlkit import TOMLDocument


class TomlFile:
    """TOML file editor with in-memory modifications and explicit save.

    Usage:
        toml = TomlFile.load("config.toml")
        toml.set("channels.dingtalk.xxx.client_id", "new_id")
        toml.set("channels.dingtalk.xxx.dm_policy", "disabled")
        toml.save()  # All changes written at once
    """

    def __init__(self, file_path: Union[str, Path], doc: TOMLDocument) -> None:
        """Initialize TomlFile.

        Args:
            file_path: Path to TOML file
            doc: TOMLDocument instance
        """
        self.file_path = Path(file_path)
        self._doc = doc
        self._modified = False

    @classmethod
    def load(cls, file_path: Union[str, Path]) -> "TomlFile":
        """Load TOML file into TomlFile instance.

        Args:
            file_path: Path to TOML file

        Returns:
            TomlFile instance
        """
        path = Path(file_path)
        content = path.read_text(encoding="utf-8")
        doc = tomlkit.loads(content)
        return cls(path, doc)

    def save(self) -> None:
        """Save all modifications to file."""
        if self._modified:
            content = tomlkit.dumps(self._doc)
            self.file_path.write_text(content, encoding="utf-8")
            self._modified = False

    @staticmethod
    def _parse_key_path(key_path: str) -> list[str]:
        """Parse dot notation key path."""
        return key_path.split(".")

    def _get_nested(self, keys: list[str]) -> Any:
        """Get nested value from TOMLDocument."""
        current = self._doc
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return None
        return current

    def _set_nested(self, keys: list[str], value: Any) -> None:
        """Set nested value in TOMLDocument, creating tables if needed."""
        current = self._doc
        for i, key in enumerate(keys[:-1]):
            if key not in current:
                current[key] = tomlkit.table()
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

    def set(self, key_path: str, value: Any, *, comment: str | None = None) -> None:
        """Set value by dot notation key path (in-memory, no save yet).

        Args:
            key_path: Dot notation path
            value: Value to set
            comment: Optional comment to add
        """
        keys = self._parse_key_path(key_path)
        self._set_nested(keys, value)

        if comment:
            parent_keys = keys[:-1]
            if parent_keys:
                parent = self._get_nested(parent_keys)
                if parent and hasattr(parent, "comment"):
                    parent.comment(comment)

    def delete(self, key_path: str) -> bool:
        """Delete key by dot notation path (in-memory, no save yet).

        Args:
            key_path: Dot notation path

        Returns:
            True if deleted, False if not found
        """
        keys = self._parse_key_path(key_path)
        current = self._doc

        for key in keys[:-1]:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return False

        last_key = keys[-1]
        if last_key in current:
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

    def append(self, key_path: str, value: Any) -> None:
        """Append value to array (in-memory, no save yet).

        Args:
            key_path: Path to array
            value: Value to append
        """
        keys = self._parse_key_path(key_path)
        arr = self._get_nested(keys)

        if arr is None:
            arr = tomlkit.array()
            self._set_nested(keys, arr)

        arr.append(value)
        self._modified = True

    def remove(self, key_path: str, value: Any) -> bool:
        """Remove value from array (in-memory, no save yet).

        Args:
            key_path: Path to array
            value: Value to remove

        Returns:
            True if removed
        """
        keys = self._parse_key_path(key_path)
        arr = self._get_nested(keys)

        if arr and value in arr:
            arr.remove(value)
            self._modified = True
            return True
        return False

    def batch_set(self, updates: dict[str, Any]) -> None:
        """Batch set values (in-memory, no save yet).

        Args:
            updates: Dict of {key_path: value}
        """
        for key_path, value in updates.items():
            keys = self._parse_key_path(key_path)
            self._set_nested(keys, value)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return dict(self._doc)

    def dumps(self) -> str:
        """Serialize to TOML string."""
        return tomlkit.dumps(self._doc)


class TomlUtils:
    """TOML file CRUD utility class (one-off operations with auto-save).

    For multiple modifications, use TomlFile instead:
        toml = TomlFile.load("config.toml")
        toml.set(...)
        toml.save()
    """

    @staticmethod
    def load(file_path: Union[str, Path], *, raw: bool = False) -> Union[dict, TOMLDocument]:
        """Load TOML file.

        Args:
            file_path: Path to TOML file
            raw: Return raw TOMLDocument to preserve formatting

        Returns:
            Parsed TOML as dict or TOMLDocument
        """
        path = Path(file_path)
        content = path.read_text(encoding="utf-8")
        doc = tomlkit.loads(content)
        return doc if raw else dict(doc)

    @staticmethod
    def loads(content: str, *, raw: bool = False) -> Union[dict, TOMLDocument]:
        """Load TOML from string.

        Args:
            content: TOML content string
            raw: Return raw TOMLDocument

        Returns:
            Parsed TOML
        """
        doc = tomlkit.loads(content)
        return doc if raw else dict(doc)

    @staticmethod
    def dump(file_path: Union[str, Path], data: Union[dict, TOMLDocument]) -> None:
        """Save data to TOML file.

        Args:
            file_path: Path to save
            data: Data to save
        """
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        if isinstance(data, TOMLDocument):
            content = tomlkit.dumps(data)
        else:
            doc = tomlkit.document()
            for key, value in data.items():
                doc[key] = value
            content = tomlkit.dumps(doc)

        path.write_text(content, encoding="utf-8")

    @staticmethod
    def dumps(data: Union[dict, TOMLDocument]) -> str:
        """Serialize data to TOML string.

        Args:
            data: Data to serialize

        Returns:
            TOML string
        """
        if isinstance(data, TOMLDocument):
            return tomlkit.dumps(data)

        doc = tomlkit.document()
        for key, value in data.items():
            doc[key] = value
        return tomlkit.dumps(doc)

    @staticmethod
    def _parse_key_path(key_path: str) -> list[str]:
        """Parse dot notation key path.

        'channels.dingtalk.semanticflowali.client_id'
        -> ['channels', 'dingtalk', 'semanticflowali', 'client_id']
        """
        return key_path.split(".")

    @staticmethod
    def _get_nested(doc: TOMLDocument, keys: list[str]) -> Any:
        """Get nested value from TOMLDocument."""
        current = doc
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return None
        return current

    @staticmethod
    def _set_nested(doc: TOMLDocument, keys: list[str], value: Any) -> None:
        """Set nested value in TOMLDocument, creating tables if needed."""
        current = doc
        for i, key in enumerate(keys[:-1]):
            if key not in current:
                # Create intermediate table
                current[key] = tomlkit.table()
            current = current[key]

        current[keys[-1]] = value

    @staticmethod
    def get(file_path: Union[str, Path], key_path: str, default: Any = None) -> Any:
        """Get value by dot notation key path.

        Args:
            file_path: TOML file path
            key_path: Dot notation path like "channels.dingtalk.xxx.client_id"
            default: Default value if key not found

        Returns:
            Value or default

        Example:
            TomlUtils.get("config.toml", "channels.dingtalk.semanticflowali.client_id")
        """
        doc = TomlUtils.load(file_path, raw=True)
        keys = TomlUtils._parse_key_path(key_path)
        value = TomlUtils._get_nested(doc, keys)
        return value if value is not None else default

    @staticmethod
    def set(
        file_path: Union[str, Path],
        key_path: str,
        value: Any,
        *,
        comment: str | None = None,
    ) -> None:
        """Set value by dot notation key path.

        Args:
            file_path: TOML file path
            key_path: Dot notation path
            value: Value to set
            comment: Optional comment to add

        Example:
            TomlUtils.set("config.toml", "channels.dingtalk.xxx.client_id", "xxx")
        """
        doc = TomlUtils.load(file_path, raw=True)
        keys = TomlUtils._parse_key_path(key_path)
        TomlUtils._set_nested(doc, keys, value)

        if comment:
            # Add comment to the table or item
            parent_keys = keys[:-1]
            if parent_keys:
                parent = TomlUtils._get_nested(doc, parent_keys)
                if parent and hasattr(parent, "comment"):
                    parent.comment(comment)

        TomlUtils.dump(file_path, doc)

    @staticmethod
    def delete(file_path: Union[str, Path], key_path: str) -> bool:
        """Delete key by dot notation path.

        Args:
            file_path: TOML file path
            key_path: Dot notation path

        Returns:
            True if deleted, False if not found
        """
        doc = TomlUtils.load(file_path, raw=True)
        keys = TomlUtils._parse_key_path(key_path)

        current = doc
        for key in keys[:-1]:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return False

        last_key = keys[-1]
        if last_key in current:
            del current[last_key]
            TomlUtils.dump(file_path, doc)
            return True
        return False

    @staticmethod
    def exists(file_path: Union[str, Path], key_path: str) -> bool:
        """Check if key exists.

        Args:
            file_path: TOML file path
            key_path: Dot notation path

        Returns:
            True if exists
        """
        return TomlUtils.get(file_path, key_path) is not None

    @staticmethod
    def append(file_path: Union[str, Path], key_path: str, value: Any) -> None:
        """Append value to array.

        Args:
            file_path: TOML file path
            key_path: Path to array
            value: Value to append

        Example:
            TomlUtils.append("config.toml", "channels.dingtalk.xxx.allowlist", "user123")
        """
        doc = TomlUtils.load(file_path, raw=True)
        keys = TomlUtils._parse_key_path(key_path)
        arr = TomlUtils._get_nested(doc, keys)

        if arr is None:
            arr = tomlkit.array()
            TomlUtils._set_nested(doc, keys, arr)

        arr.append(value)
        TomlUtils.dump(file_path, doc)

    @staticmethod
    def remove(file_path: Union[str, Path], key_path: str, value: Any) -> bool:
        """Remove value from array.

        Args:
            file_path: TOML file path
            key_path: Path to array
            value: Value to remove

        Returns:
            True if removed
        """
        doc = TomlUtils.load(file_path, raw=True)
        keys = TomlUtils._parse_key_path(key_path)
        arr = TomlUtils._get_nested(doc, keys)

        if arr and value in arr:
            arr.remove(value)
            TomlUtils.dump(file_path, doc)
            return True
        return False

    @staticmethod
    def batch_set(file_path: Union[str, Path], updates: dict[str, Any]) -> None:
        """Batch set values.

        Args:
            file_path: TOML file path
            updates: Dict of {key_path: value}

        Example:
            TomlUtils.batch_set("config.toml", {
                "channels.dingtalk.xxx.client_id": "new_id",
                "channels.dingtalk.xxx.dm_policy": "disabled",
            })
        """
        doc = TomlUtils.load(file_path, raw=True)

        for key_path, value in updates.items():
            keys = TomlUtils._parse_key_path(key_path)
            TomlUtils._set_nested(doc, keys, value)

        TomlUtils.dump(file_path, doc)
