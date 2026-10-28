"""TaskArtifactObjectStoragePlugin 的列内实现。

本文件归 task 模块自有缝（``artifact_object_storage.py``）同处，避免
任务域的产物存储实现散落在平台 ``plugins/`` 下：

- test/singlebox: in-memory dict of real bytes for artifact roundtrips.
- community: filesystem-backed object store for community deployments.
- other profiles: composition root binds another module-owned implementation
  or fails closed at configuration validation time.
"""

from __future__ import annotations

from pathlib import Path

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl

from .artifact_object_storage import (
    TaskArtifactObjectReadResult,
    TaskArtifactObjectReadStatus,
    TaskArtifactObjectStoragePlugin,
)

logger = get_logger()


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.FAKE,
    rationale="in-process dict of real bytes for artifact roundtrips",
)
class InMemoryTaskArtifactObjectStoragePlugin(TaskArtifactObjectStoragePlugin):
    """Task-artifact object store kept in a per-instance dict."""

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}

    def put_object(self, key: str, content: bytes) -> bool:
        if not isinstance(content, bytes):
            return False
        self._objects[key] = content
        return True

    def get_object(self, key: str) -> TaskArtifactObjectReadResult:
        if key in self._objects:
            return TaskArtifactObjectReadResult(
                status=TaskArtifactObjectReadStatus.FOUND,
                content=self._objects[key],
            )
        return TaskArtifactObjectReadResult(
            status=TaskArtifactObjectReadStatus.NOT_FOUND
        )

    def delete_object(self, key: str) -> bool:
        self._objects.pop(key, None)
        return True


@plugin_impl(
    mode=Mode.PROD,
    rationale="zero-dependency filesystem object store for community deployments",
)
class CommunityFsTaskArtifactObjectStorage(TaskArtifactObjectStoragePlugin):
    """Task-artifact object store backed by the local filesystem."""

    def __init__(self, root: str) -> None:
        # Resolve the root but do not create it here — construction stays
        # side-effect-free; write paths create directories lazily.
        self._root = Path(root).resolve()

    def _safe_path(self, key: str) -> Path | None:
        """Resolve ``key`` under the root, or ``None`` if it escapes it."""
        candidate = (self._root / key).resolve()
        if candidate == self._root or self._root in candidate.parents:
            return candidate
        logger.error("TaskArtifactObjectStorage: key escapes storage root: %r", key)
        return None

    def put_object(self, key: str, content: bytes) -> bool:
        path = self._safe_path(key)
        if path is None:
            return False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            return True
        except OSError as exc:
            logger.error(
                "TaskArtifactObjectStorage put failed: key=%s, error=%s", key, exc
            )
            return False

    def get_object(self, key: str) -> TaskArtifactObjectReadResult:
        path = self._safe_path(key)
        if path is None:
            return TaskArtifactObjectReadResult(
                status=TaskArtifactObjectReadStatus.NOT_FOUND
            )
        try:
            if not path.is_file():
                return TaskArtifactObjectReadResult(
                    status=TaskArtifactObjectReadStatus.NOT_FOUND
                )
            return TaskArtifactObjectReadResult(
                status=TaskArtifactObjectReadStatus.FOUND,
                content=path.read_bytes(),
            )
        except OSError as exc:
            logger.error(
                "TaskArtifactObjectStorage get failed: key=%s, error=%s", key, exc
            )
            return TaskArtifactObjectReadResult(
                status=TaskArtifactObjectReadStatus.FAILED
            )

    def delete_object(self, key: str) -> bool:
        path = self._safe_path(key)
        if path is None:
            return False
        try:
            path.unlink(missing_ok=True)
            return True
        except OSError as exc:
            logger.error(
                "TaskArtifactObjectStorage delete failed: key=%s, error=%s", key, exc
            )
            return False
