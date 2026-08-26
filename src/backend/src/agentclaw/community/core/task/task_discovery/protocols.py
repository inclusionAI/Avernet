"""task_discovery 模块内部依赖接口协议。

根据四层架构规范：
- core/ 层通过 Protocol 接口访问外部依赖，不直接 import api/ 层
- 具体实现由 DI 在 di/modules/task_discovery_module.py 注入

本模块依赖：
  - BotService — list_bots() 遍历所有用户 bot，get_bot() 校验 ownership
  - CronRelayService — forward_request() 创建 session，list_all_crons() 查 cron
  - WorkOrderService — create_work_order_event() 投递工单通知

参考：core/bot_dormant/protocols.py
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class BotServiceProtocol(Protocol):
    """Bot 服务接口 —— 供 task_discovery 模块遍历所有用户 bot + 校验 ownership。

    实现类需提供：
      - list_bots(page, page_size)  分页查询 bot 列表
      - get_bot(bot_id, owner_id)   查单个 bot（权限校验）
    """

    def list_bots(self, *args: Any, **kwargs: Any) -> Any: ...

    def get_bot(self, *args: Any, **kwargs: Any) -> Any: ...


@runtime_checkable
class CronRelayServiceProtocol(Protocol):
    """Cron relay 服务接口 — 用于 forward_request 创建 session。"""

    async def forward_request(self, *args: Any, **kwargs: Any) -> Any: ...

    async def list_all_crons(self, *args: Any, **kwargs: Any) -> Any: ...


@runtime_checkable
class WorkOrderServiceProtocol(Protocol):
    """工单服务接口 — 供 task_discovery 投递 NOTICE 工单通知事件。

    DI 桥接 api/ 层的 WorkOrderServiceProtocol 到本协议（structurally
    satisfied），避免 core/ 直接 import api/。
    """

    def create_work_order_event(self, *args: Any, **kwargs: Any) -> Any: ...


__all__ = [
    "BotServiceProtocol",
    "CronRelayServiceProtocol",
    "WorkOrderServiceProtocol",
]