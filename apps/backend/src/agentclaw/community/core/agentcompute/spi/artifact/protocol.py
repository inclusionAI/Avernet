from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ArtifactStore(Protocol):

    def put(self, key: str, content: bytes | str) -> bool: ...
    def put_file(self, key: str, local_path: str) -> bool: ...
    def delete(self, key: str) -> bool: ...
    def get(self, key: str) -> bytes | None: ...


@runtime_checkable
class RetentionScanner(Protocol):
    """Store-level listing/aging seam the sweeper drives (task 12.9, D38).

    Kept separate from ``ArtifactStore`` on purpose: existing stores and test
    fakes stay structurally valid without gaining scanning methods, while the
    retention sweeper never acquires filesystem knowledge itself — it only
    sees a listing and an mtime per key, plus deletion through the store's
    own guard-raised ``delete``.
    """

    def list_keys(self) -> list[str]: ...
    def key_mtime(self, key: str) -> float: ...