"""``TaskArtifactObjectFileServiceProtocol`` re-export(the OBJECT_FILE surface).

Protocol 定义在其所属 core 模块
``core/task/task_context/task_artifact/artifact_object_file_service.py``,此处仅
re-export(repo pattern——见 ``api/README.md``"Where a Protocol is defined")。
``TaskArtifactObjectFileService``(in ``core/``)实现之;DI composition root
(``di/modules/task_persistence_module``)绑定;HTTP adapter Inject 之。
其返回值 dataclass ``TaskArtifactObjectDownload`` 留在 core(非 Protocol,不进
本层——api/ 是纯 Protocols 面)。
"""
from agentclaw.community.core.task.task_context.task_artifact.artifact_object_file_service import (
    TaskArtifactObjectFileServiceProtocol,
)

__all__ = ["TaskArtifactObjectFileServiceProtocol"]