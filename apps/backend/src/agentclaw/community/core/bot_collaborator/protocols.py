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

from inspect import signature
from typing import Any, Mapping, Protocol, runtime_checkable

from agentclaw.community.core.bot_collaborator.models import PermissionLevel


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

    def get_operable_permission_level(self, *args: Any, **kwargs: Any) -> Any:
        """获取叠加实时 Space 成员关系后的有效 Bot 权限."""
        ...

    def on_collaboration_changed(self, *args: Any, **kwargs: Any) -> Any:
        """Run best-effort downstream synchronization after a relation change."""
        ...


def resolve_operable_permission_level(
    collaborators: CollaboratorServiceProtocol,
    *,
    bot: Mapping[str, Any],
    user_id: str,
    owner_id: str,
    env: str | None = None,
) -> PermissionLevel:
    """Call the effective policy while legacy test doubles migrate in-place."""
    if user_id == owner_id:
        return PermissionLevel.OWNER
    method = getattr(type(collaborators), "get_operable_permission_level", None)
    if callable(method):
        return PermissionLevel(
            method(collaborators, bot=bot, user_id=user_id, env=env)
        )
    legacy_method = collaborators.get_permission_level
    legacy_args = (int(bot.get("id") or 0), user_id, owner_id)
    try:
        signature(legacy_method).bind(*legacy_args, env)
    except TypeError:
        return PermissionLevel(legacy_method(*legacy_args))
    return PermissionLevel(legacy_method(*legacy_args, env))
