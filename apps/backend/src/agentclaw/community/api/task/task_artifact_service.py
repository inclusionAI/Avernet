"""``TaskArtifactApiServiceProtocol`` re-export(the artifact API Service surface).

Protocol 定义在其所属 core 模块
``core/task/task_context/task_artifact/artifact_api_service.py``,此处仅
re-export(repo pattern——见 ``api/README.md``"Where a Protocol is defined")。
``TaskArtifactApiService``(in ``core/``)实现之;DI composition root
(``di/modules/task_persistence_module``)绑定;HTTP adapter Inject 之。
"""
from agentclaw.community.core.task.task_context.task_artifact.artifact_api_service import (
    TaskArtifactApiServiceProtocol,
)

__all__ = ["TaskArtifactApiServiceProtocol"]