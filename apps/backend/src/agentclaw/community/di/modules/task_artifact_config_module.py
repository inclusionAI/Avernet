"""DI provider for ``TaskArtifactStorageConfig``.

Lives OUT of ``di/modules/config_module.py`` (which sits at zero headroom
under the 1000-line architecture cap), mirroring the
``TaskTrajectoryConfigModule`` pattern: a small ``Module`` with one
``@singleton @provider`` that reads the ``task_artifact_storage`` block
of ``user_config``. Registered in ``di/container.py`` alongside
``TaskTrajectoryConfigModule``, so every deploy profile resolves the
task module's own object-store configuration.

YAML shape under ``user_config.task_artifact_storage``::

    task_artifact_storage:
      backend: fs               # community: fs; corp (consumer repo): oss
      fs_root: ./data/task-artifact-objects
      key_prefix: task-artifacts
      max_upload_mb: 64
      # corp-consumed (neutral placeholders in community):
      endpoint: ""
      bucket_name: ""
      secret_name: ""           # SecretResolver key, never the credential
"""
from __future__ import annotations

from injector import Module, provider, singleton

from agentclaw.community.core.task.task_context.task_artifact.artifact_object_storage_config import (
    TaskArtifactStorageConfig,
)
from agentclaw.community.di.modules import config_module


class TaskArtifactConfigModule(Module):
    """Bind ``TaskArtifactStorageConfig`` to its deployment config."""

    @singleton
    @provider
    def task_artifact_storage(self) -> TaskArtifactStorageConfig:
        raw = config_module.read_user_config().get("task_artifact_storage") or {}
        block = dict(raw) if isinstance(raw, dict) else {}
        defaults = TaskArtifactStorageConfig()
        return TaskArtifactStorageConfig(
            backend=config_module._coerce(
                block, "backend", str, defaults.backend, "task_artifact_storage"
            ),
            fs_root=config_module._coerce(
                block, "fs_root", str, defaults.fs_root, "task_artifact_storage"
            ),
            key_prefix=config_module._coerce(
                block, "key_prefix", str, defaults.key_prefix, "task_artifact_storage"
            ),
            max_upload_mb=config_module._coerce(
                block, "max_upload_mb", int, defaults.max_upload_mb,
                "task_artifact_storage",
            ),
            endpoint=config_module._coerce(
                block, "endpoint", str, defaults.endpoint, "task_artifact_storage"
            ),
            bucket_name=config_module._coerce(
                block, "bucket_name", str, defaults.bucket_name,
                "task_artifact_storage",
            ),
            secret_name=config_module._coerce(
                block, "secret_name", str, defaults.secret_name,
                "task_artifact_storage",
            ),
        )


__all__ = ["TaskArtifactConfigModule"]