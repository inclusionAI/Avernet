"""task_discovery 模块内部依赖接口协议。

根据四层架构规范：
- core/ 层通过 Protocol 接口访问外部依赖，不直接 import api/ 层
- 具体实现由 DI 在 di/modules/task_discovery_module.py 注入

本模块依赖 BotService 的 list_bots 方法（遍历所有用户 bot 执行发现），
通过本地定义的 BotServiceProtocol 接入，避免直接 import
agentclaw.community.api.bot_service。

参考：core/bot_dormant/protocols.py
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class BotServiceProtocol(Protocol):
    """Bot 服务接口 —— 供 task_discovery 模块遍历所有用户 bot。

    实现类需提供：
      - list_bots(page, page_size)  分页查询 bot 列表
    """

    def list_bots(self, *args: Any, **kwargs: Any) -> Any: ...


__all__ = ["BotServiceProtocol"]
