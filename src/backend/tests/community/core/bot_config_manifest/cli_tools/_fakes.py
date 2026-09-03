"""Fakes shared by the CLI-tools tests (W9)."""
from __future__ import annotations

from typing import Optional


class FakeObjectStorage:
    """A dict-backed ``ObjectStoragePlugin`` with **no** server-side copy.

    Deliberately without ``copy_object``: this is the shape of an overlay that
    has not shipped :class:`ObjectCopyCapability`, and it is what exercises the
    store's read-through staging path.
    """

    def __init__(self, *, fail_puts: bool = False, fail_deletes: bool = False) -> None:
        self.objects: dict[str, bytes] = {}
        self.puts: list[str] = []
        self.deletes: list[str] = []
        self.reads: list[str] = []
        self.fail_puts = fail_puts
        self.fail_deletes = fail_deletes

    def put_object(self, key: str, content) -> bool:
        if self.fail_puts:
            return False
        self.puts.append(key)
        self.objects[key] = content if isinstance(content, bytes) else content.encode()
        return True

    def get_object(self, key: str) -> Optional[bytes]:
        self.reads.append(key)
        return self.objects.get(key)

    def delete_object(self, key: str) -> bool:
        if self.fail_deletes:
            return False
        # The plugin contract: an already-absent object deletes successfully.
        self.deletes.append(key)
        self.objects.pop(key, None)
        return True

    def list_objects(self, prefix: str, max_keys: int = 1000) -> list[str]:
        return sorted(k for k in self.objects if k.startswith(prefix))[:max_keys]


class FakeCopyingObjectStorage(FakeObjectStorage):
    """The same store, plus :class:`ObjectCopyCapability`."""

    def __init__(self, *, fail_copies: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        self.copies: list[tuple[str, str]] = []
        self.fail_copies = fail_copies

    def copy_object(self, source_key: str, dest_key: str) -> bool:
        if self.fail_copies or source_key not in self.objects:
            return False
        self.copies.append((source_key, dest_key))
        self.objects[dest_key] = self.objects[source_key]
        return True
