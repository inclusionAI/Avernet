"""Task-artifact object storage — test / singlebox binding.

Binds the in-memory impl so contract / service / endpoint tests exercise
genuine upload→download roundtrips without filesystem writes or a
remote object store.
"""
from __future__ import annotations

from injector import Module, provider, singleton

from agentclaw.community.log import get_logger
from agentclaw.community.core.task.task_context.task_artifact.artifact_object_storage import (
    TaskArtifactObjectStoragePlugin,
)
from agentclaw.community.plugins.local.task_artifact_object_storage import (
    InMemoryTaskArtifactObjectStoragePlugin,
)


logger = get_logger()


class TestTaskArtifactObjectStorageModule(Module):
    """test / singlebox: in-memory task-artifact object store."""

    @singleton
    @provider
    def task_artifact_object_storage(self) -> TaskArtifactObjectStoragePlugin:
        logger.info("TaskArtifactObjectStoragePlugin: InMemory (test)")
        return InMemoryTaskArtifactObjectStoragePlugin()