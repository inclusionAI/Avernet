"""Service API contract for ``task_config`` (spec 2026-11-02).

``config_value`` (set_config) / ``value`` (match) are intentionally untyped:
their shape is governed by ``config_type`` (single_string -> str, bool -> bool,
set -> list[str] on write / str on match). The concrete service validates
shape and raises the task surface's existing 400-mapped bad-request leaf
(``CallbackCorrelationError``) on mismatch, so ``@envelope_errors`` maps it to
400 rather than letting it escape to the generic 500 handler; the router relies
on pydantic for the request-body layer.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TaskConfigServiceProtocol(Protocol):
    """配置存储 + 类型驱动匹配服务协议。"""

    @abstractmethod
    def set_config(
        self,
        *,
        config_name: str,
        config_type: str,
        config_value: Any,
        env: str,
        operator: str | None = None,
    ) -> int:
        """整体覆盖写入(upsert);``config_value`` 是已反序列化的 Python 值,
        服务按 ``config_type`` 校验形态并序列化为 JSON 文本存储。返回 row id。"""
        ...

    @abstractmethod
    def match(self, *, config_name: str, value: Any, env: str) -> bool:
        """按存储的 ``config_type`` 判定 ``value`` 是否匹配该配置;未知配置名
        返回 ``False``;形态不符抛 ``CallbackCorrelationError``。只回布尔,不回存储值。"""
        ...
