"""InMemoryTaskArtifactObjectStoragePlugin — local impl of the task-artifact object-store Protocol.

A real in-memory dict store (not a MagicMock seam): put/get/delete hold
actual bytes with three-state read outcomes, so endpoint and service
tests exercise genuine upload→download roundtrips without touching the
filesystem or a remote store. Then-also the bound singleton of the test
double column per ``profile_modules._common_test_doubles()``.
"""
from __future__ import annotations

from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.core.task.task_context.task_artifact.artifact_object_storage import (
    TaskArtifactObjectReadResult,
    TaskArtifactObjectReadStatus,
    TaskArtifactObjectStoragePlugin,
)


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