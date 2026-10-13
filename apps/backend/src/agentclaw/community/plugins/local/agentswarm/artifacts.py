from __future__ import annotations

import logging
import os
from pathlib import Path

from agentclaw.community.plugin_api.agentswarm.artifact import ArtifactStore

logger = logging.getLogger(__name__)


def _resolve_within(root: Path, key: str) -> Path:
    parts = key.split("/")
    resolved = root.joinpath(*parts).resolve()
    if not str(resolved).startswith(str(root.resolve())):
        raise ValueError(f"key escapes root: {key}")
    return resolved


class FilesystemArtifactStore:

    def __init__(self, root: str = "/tmp/agentswarm/artifacts") -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def put(self, key: str, content: bytes | str) -> bool:
        target = self._resolve(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
        return True

    def put_file(self, key: str, local_path: str) -> bool:
        source = Path(local_path)
        if not source.exists():
            return False
        target = self._resolve(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        return True

    def delete(self, key: str) -> bool:
        target = self._resolve(key)
        if target.exists():
            target.unlink()
            return True
        return False

    def get(self, key: str) -> bytes | None:
        target = self._resolve(key)
        if not target.exists():
            return None
        return target.read_bytes()

    def retention_scanner(self) -> "FilesystemRetentionScanner":
        return FilesystemRetentionScanner(self)

    def _resolve(self, key: str) -> Path:
        return _resolve_within(self._root, key)


class FilesystemRetentionScanner:
    """RetentionScanner over one store's root — store-level only, no DB (12.9).

    Keys are derived by walking the root itself, so every listed key is
    root-relative by construction; ``key_mtime`` still routes through the same
    escape guard as the store so a malformed key raises instead of stat-ing
    outside the root. Broken symlinks are listed but their mtime lookup fails
    (getmtime follows the link) — the sweeper treats that as a per-key error
    and continues, never deleting on unreadable age.
    """

    def __init__(self, store: FilesystemArtifactStore) -> None:
        self._store = store

    def list_keys(self) -> list[str]:
        root = self._store.root
        keys = [
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file() or path.is_symlink()
        ]
        return sorted(keys)

    def key_mtime(self, key: str) -> float:
        return os.path.getmtime(self._resolve(key))

    def _resolve(self, key: str) -> Path:
        return _resolve_within(self._store.root, key)