"""Fakes shared by the managed-files tests (W8)."""
from __future__ import annotations

from typing import Optional


class FakeObjectStorage:
    """A dict-backed ``ObjectStoragePlugin``: enough for put/get/delete/list."""

    def __init__(self, *, fail_puts: bool = False) -> None:
        self.objects: dict[str, bytes] = {}
        self.puts: list[str] = []
        self.deletes: list[str] = []
        self.fail_puts = fail_puts

    def put_object(self, key: str, content) -> bool:
        if self.fail_puts:
            return False
        self.puts.append(key)
        self.objects[key] = content if isinstance(content, bytes) else content.encode()
        return True

    def get_object(self, key: str) -> Optional[bytes]:
        return self.objects.get(key)

    def delete_object(self, key: str) -> bool:
        # The plugin contract: an already-absent object deletes successfully.
        self.deletes.append(key)
        self.objects.pop(key, None)
        return True

    def list_objects(self, prefix: str, max_keys: int = 1000) -> list[str]:
        return sorted(k for k in self.objects if k.startswith(prefix))[:max_keys]
