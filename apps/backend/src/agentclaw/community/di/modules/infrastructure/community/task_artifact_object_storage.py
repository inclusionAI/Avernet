"""Task-artifact object storage — community binding.

Binds the task module's own object-store Protocol from the
``task_artifact_storage`` config block. Community supports the
filesystem backend only; anything else is a boot-time configuration
error (fail fast — a mis-deployed artifact store must not silently
fall back to writing artifact bytes to local disk).
"""
from __future__ import annotations

from injector import Module, inject, provider, singleton

from agentclaw.community.core.task.task_context.task_artifact.artifact_object_storage_config import (
    TaskArtifactStorageConfig,
)
from agentclaw.community.log import get_logger
from agentclaw.community.core.task.task_context.task_artifact.artifact_object_storage import (
    TaskArtifactObjectStoragePlugin,
)


logger = get_logger()


class CommunityTaskArtifactObjectStorageModule(Module):
    """community: filesystem-backed task-artifact object store."""

    @singleton
    @provider
    @inject
    def task_artifact_object_storage(
        self, config: TaskArtifactStorageConfig
    ) -> TaskArtifactObjectStoragePlugin:
        from agentclaw.community.plugins.community.task_artifact_object_storage import (
            CommunityFsTaskArtifactObjectStorage,
        )

        backend = config.backend.strip().lower()
        if backend == "fs":
            logger.info(
                "TaskArtifactObjectStoragePlugin: CommunityFsTaskArtifactObjectStorage "
                "(root=%s)",
                config.fs_root,
            )
            return CommunityFsTaskArtifactObjectStorage(config.fs_root)
        # Fail fast: an unrecognized backend must not silently fall back to the
        # filesystem and write artifact bytes to local disk instead of the
        # configured store (mirrors the object_storage module's posture).
        raise ValueError(
            f"Unknown task_artifact_storage.backend {config.backend!r} "
            "(community supports 'fs'; corp overlays provide 'oss')."
        )