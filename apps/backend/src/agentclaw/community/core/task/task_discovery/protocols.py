"""task_discovery 模块内部依赖接口协议。

根据四层架构规范：
- core/ 层通过 Protocol 接口访问外部依赖，不直接 import api/ 层
- 具体实现由 DI 在 di/modules/task_discovery_module.py 注入

本模块依赖：
  - BotService — list_bots() 遍历所有用户 bot，get_bot() 校验 ownership
  - WorkOrderService — create_work_order_event() 投递工单通知

（2026-09-15 统一化: 原 ``CronRelayServiceProtocol``（forward_request 创建
session 的 Relay 链）已随 ``CronRelaySessionInitiator`` 废除而移除;session
创建统一走 ``OpenApiBotSessionInitiator`` → ``OpenApiBotPort``。）

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
class WorkOrderServiceProtocol(Protocol):
    """工单服务接口 — 供 task_discovery 投递 NOTICE 工单通知事件。

    DI 桥接 api/ 层的 WorkOrderServiceProtocol 到本协议（structurally
    satisfied），避免 core/ 直接 import api/。
    """

    def create_work_order_event(self, *args: Any, **kwargs: Any) -> Any: ...


__all__ = [
    "BotServiceProtocol",
    "WorkOrderServiceProtocol",
]