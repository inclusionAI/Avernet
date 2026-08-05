"""Dormant 模块内部依赖接口协议。

根据 README.md 四层架构规范：
- core/ 层通过 Protocol 接口访问外部依赖，不直接 import api/ 层
- 具体实现由 DI 在 di/modules/bot_dormant_module.py 注入

本模块依赖 BotService 的几个方法（get_bot / update_status / start_bot / stop_bot），
通过本地定义的 BotServiceProtocol 接入，避免直接 import agentclaw.community.api.bot_service。

参考：core/bot_collaborator/protocols.py
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class BotServiceProtocol(Protocol):
    """Bot 服务接口 —— 供 dormant 模块查询 / 操作 Bot。

    实现类需提供这几个方法（签名宽松，便于跨实现复用）：
      - get_bot(bot_id, user_id)        查询单个 bot；不存在时返回 None
      - update_status(bot_id, user_id, status)   修改 status 字段
      - stop_bot(bot_id, user_id, release_reason)  释放容器 + binding 置 PENDING
      - start_bot(bot_id, user_id, nick_name)      重新分配容器
    """

    def get_bot(self, *args: Any, **kwargs: Any) -> Any: ...
    def update_status(self, *args: Any, **kwargs: Any) -> Any: ...
    def stop_bot(self, *args: Any, **kwargs: Any) -> Any: ...
    def start_bot(self, *args: Any, **kwargs: Any) -> Any: ...
