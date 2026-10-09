from __future__ import annotations

import logging
from pathlib import Path

from agentclaw.community.core.agentcompute.spi.artifact_store import ArtifactStore

logger = logging.getLogger(__name__)


class FilesystemArtifactStore:

    def __init__(self, root: str = "/tmp/agentcompute/artifacts") -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

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

    def _resolve(self, key: str) -> Path:
        parts = key.split("/")
        target = self._root.joinpath(*parts)
        resolved = target.resolve()
        if not str(resolved).startswith(str(self._root.resolve())):
            raise ValueError(f"key escapes root: {key}")
        return resolved