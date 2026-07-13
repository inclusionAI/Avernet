"""BCN 上行客户端 Protocol 定义。"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class UplinkClient(Protocol):
    """BcnUplinkCallback 依赖的最小上行接口（由 BcnUplinkClient 实现）。"""

    async def send_event(
        self, event: Any, bot_id: str, event_id: str | None = ...
    ) -> Any: ...
