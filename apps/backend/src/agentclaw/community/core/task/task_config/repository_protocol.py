"""Repository contract for ``task_config`` (spec 2026-11-02).

Self-contained within the ``core/task/task_config`` subpackage (the task
domain's convention for new tables), mirroring the ``@runtime_checkable`` +
``@abstractmethod`` + ``Protocol`` idiom of ``core/repository/protocols/config.py``.
Domain types are ``TYPE_CHECKING``-only to keep the contract layer free of the
implementation/import chain.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.task.task_config.models import TaskConfigRecord


@runtime_checkable
class TaskConfigRepositoryProtocol(Protocol):
    """配置存储仓库协议(config_name + env 作用域)。"""

    @abstractmethod
    def upsert(
        self,
        *,
        config_name: str,
        config_type: str,
        config_value: str,
        env: str,
        operator: str | None = None,
    ) -> int:
        """整体覆盖写入(upsert on (config_name, env));返回 row id。"""
        ...

    @abstractmethod
    def get_by_name(self, *, env: str, config_name: str) -> "TaskConfigRecord | None":
        """按 (env, config_name) 取单条记录;不存在返回 None。"""
        ...
