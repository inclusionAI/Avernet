"""``TaskArtifactServiceProtocol`` re-export (the task-artifact Service API surface).

The Protocol is **defined** in its owning core module
``core/task/task_context/task_artifact/artifact_service.py`` and merely re-exported
here so adapters Inject it by name from ``api/`` (repo pattern — see ``api/README.md``
"Where a Protocol is defined"). ``TaskArtifactService`` (in ``core/``) implements it
(the write-side publisher + the read-side Descriptor source); the DI composition root
binds it (``TaskPersistenceModule.task_artifact_service`` provider, singleton).

Authoritative: ``src/backend/specs/2026-09-23-task-artifact-manifest/spec.md``
(读侧条目:双轨并存、is_primary=latest_for_node、RuntimeInfo ids 只做读时富化不落库、
无新端点;dashboard handler Inject 本协议做读时富化,不新增路由)。
"""
from agentclaw.community.core.task.task_context.task_artifact.artifact_service import (
    TaskArtifactServiceProtocol,
)

__all__ = ["TaskArtifactServiceProtocol"]