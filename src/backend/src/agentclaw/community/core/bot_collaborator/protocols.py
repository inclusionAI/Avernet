"""协作模块内部依赖接口协议.

根据 README.md 分层规范：
- core/ 层通过 Protocol 接口访问外部依赖，不直接 import api/ 层
- 具体实现通过 dependencies/ 注入

这些 Protocol 接口描述协作模块对其他服务的依赖，
而非纯基础设施接口（DatabasePlugin 等），因此放在 core/bot_collaborator/
而非 plugins/。

参考：core/devices/protocols.py
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class BotServiceProtocol(Protocol):
    """Bot 服务接口 — 供协作服务查询 Bot 信息."""

    def get_bot(self, *args: Any, **kwargs: Any) -> Any:
        """获取 Bot 信息."""
        ...


@runtime_checkable
class CollaboratorServiceProtocol(Protocol):
    """协作者服务接口 — 供协作锁服务查询协作者."""

    def list_collaborators(self, *args: Any, **kwargs: Any) -> Any:
        """查询协作者列表."""
        ...

    def check_collaborator_permission(self, *args: Any, **kwargs: Any) -> Any:
        """检查协作者权限."""
        ...

    def get_permission_level(self, *args: Any, **kwargs: Any) -> Any:
        """获取用户在 Bot 中的权限级别（bot_pk 定位，无额外 Bot 查询）."""
        ...
