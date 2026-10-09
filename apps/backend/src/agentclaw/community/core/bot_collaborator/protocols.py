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
from agentclaw.community.log import get_logger

logger = get_logger()


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

    def get_explicit_permission_level(self, *args: Any, **kwargs: Any) -> Any:
        """显式阶梯的 Bot 权限等级（一次协作者表读）.

        只认所有权与显式协作者行：Space 成员身份合成的 MEMBER 在这里
        没有发言权（编辑/操作域的行 ``Check … explicit=True`` 就发布在这
        上面）。行级答案仍过 COSEC 复核——被移出空间的编辑者即时吊销。
        """
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


def resolve_explicit_permission_level(
    collaborators: CollaboratorServiceProtocol,
    *,
    bot: Mapping[str, Any],
    user_id: str,
    owner_id: str,
    env: str | None = None,
) -> PermissionLevel:
    """The level with the Space synthesis vetoed, as an explicit row demands.

    Delegates to the service's own explicit ladder
    (``get_explicit_permission_level``: ownership → one row read → COSEC),
    so a request answers with one collaborator query rather than a standing
    probe plus a level read. The refusal is fail-closed: a service that has
    not grown the explicit ladder is an unresolvable authority, and a gate
    must not quietly fall back to the synthesized answer — that direction
    re-opens exactly the hole this resolver exists to close. The absence is
    logged, because a silently missing capability is a wiring fault to fix
    and not a state to serve.
    """
    if user_id == owner_id:
        # The same owner short-circuit the operable resolver answers: the
        # two ladders agree on ownership, and doubles that key their rows off
        # the wire pair keep working on either side.
        return PermissionLevel.OWNER
    method = getattr(type(collaborators), "get_explicit_permission_level", None)
    if not callable(method):
        logger.warning(
            "[resolve_explicit] collaborator service has no explicit ladder "
            "(bot_pk=%r); refusing rather than falling back to the "
            "Space-synthesized answer",
            bot.get("id"),
        )
        return PermissionLevel.NONE
    return PermissionLevel(method(collaborators, bot=bot, user_id=user_id, env=env))
