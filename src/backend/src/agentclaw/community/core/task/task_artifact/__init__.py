"""task_artifact — 任务产物应用子包(应用服务 + Plugin API ports)。

阶段一契约(语雀《BCN 产物领域对象设计》§12):引入 Artifact 契约与仓储/服务,
继续以 ``run_info.output`` dict 为兼容投影双写,下游逻辑阶段二迁移。
"""
from agentclaw.community.core.task.task_artifact.ports import SessionFileReadinessPort
from agentclaw.community.core.task.task_artifact.service import (
    ArtifactService,
    derive_artifact_id,
)

__all__ = ["ArtifactService", "SessionFileReadinessPort", "derive_artifact_id"]