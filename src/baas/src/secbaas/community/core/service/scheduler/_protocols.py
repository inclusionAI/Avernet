"""Scheduler 内部协议定义"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ScheduledTask(Protocol):
    """定时 task 协议

    所有注册到 AppScheduler 的 task 必须实现此协议：
    - ``name``: task 名称，用作 APScheduler job id 和日志标识
    - ``interval_seconds``: 执行间隔（秒）
    - ``run()``: 异步执行方法
    """

    @property
    def name(self) -> str: ...

    @property
    def interval_seconds(self) -> int: ...

    async def run(self) -> None: ...
