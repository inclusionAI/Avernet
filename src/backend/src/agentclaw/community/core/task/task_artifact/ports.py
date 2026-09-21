"""Plugin API ports for the task-artifact application service (Rule 10 依赖方向
显式化:core 调用外部能力必须经此契约,实现由 DI 组合根装配,core/task 不 import
core/session_resources)。

先例:``task_runner/client/ports.py`` 的 ``BcsBotIdentityResolver``。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SessionFileReadinessPort(Protocol):
    """文件就绪校验能力:File 分支产物引用的 ``file_id``(SessionResource
    ``resource_id``)当前是否处于 READY 状态。

    权威不变量(语雀《BCN 产物领域对象设计》§9):只有 Ready 状态的 SessionFile
    才能发布为正式 Artifact;Artifact 不得引用 Pending/Deleting/Failed 或不存在的
    文件。``is_ready`` 返回 False 或能力未装配时,ArtifactService 一律拒绝发布
    (fail-closed)。
    """

    def is_ready(self, file_id: str) -> bool:
        """``file_id`` 对应的会话资源存在且 READY → True;否则 False。"""
        ...