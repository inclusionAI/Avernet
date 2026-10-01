"""task integration 内部依赖接口协议。

根据 README.md 四层架构规范：
- core/ 层通过 Protocol 接口访问外部依赖，不直接 import api/ 层
- 具体实现由 DI 在 di/modules 注入

task integration 解析 BCS 身份 / singlebox 公开 bot 检索时依赖 BotService /
BotPublicService 的个别方法，通过本地定义的 Protocol 接入，避免直接 import
agentclaw.community.api.bot_service / api.bot_public_service。

参考：core/bot_dormant/protocols.py、core/bot_collaborator/protocols.py
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class BotServiceProtocol(Protocol):
    """Bot 服务接口 —— 供 BCS 身份解析按产品 bot_id 反查权威记录。

    实现类需提供：
      - list_bots_by_conditions(bot_ids=..., page=..., page_size=...) 按条件分页查 bot，
        返回形如 ``{"items": [...]}`` 的分页结果（含 ``bot_id`` / ``owner_id`` 字段）。
    """

    def list_bots_by_conditions(self, *args: Any, **kwargs: Any) -> Any: ...


@runtime_checkable
class BotPublicServiceProtocol(Protocol):
    """公开 Bot 服务接口 —— 供 singlebox 链路按关键字预查公开 bot。

    实现类需提供：
      - search_public_bots_by_keyword(...)  按关键字检索公开 bot（DB LIKE）。
    """

    def search_public_bots_by_keyword(self, *args: Any, **kwargs: Any) -> Any: ...