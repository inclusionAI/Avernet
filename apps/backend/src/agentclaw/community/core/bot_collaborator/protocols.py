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

    def has_explicit_standing(self, *args: Any, **kwargs: Any) -> Any:
        """该用户对 Bot 的等级是否立足于显式协作者行或所有权.

        Space 成员身份合成的 MEMBER 不算显式：编辑/操作域的行
        (Check … explicit=True) 只认 Owner 与被授权/审批过的共同编辑者。
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
    """The effective level with the Space synthesis vetoed, as an explicit row demands.

    First the same owner short-circuit :func:`resolve_operable_permission_level`
    answers: ownership is the most explicit standing there is. Otherwise the
    answer requires explicit standing — a real collaborator row — and is then
    read through the **effective** policy rather than the raw one, so a revoked
    Space membership still revokes even an explicit editor's operations (the
    COSEC rule the single resolve's own comment records). Without explicit
    standing the answer is ``NONE``: this is the whole refusal the edit/
    operations rows (``Check … explicit=True``) publish, so a Space member's
    synthesized MEMBER carries no further here.

    A collaborator service that has not grown ``has_explicit_standing`` yet
    falls back to the effective answer: the legacy doubles this resolver was
    built for predate the split, and their members were all explicit — the
    production service carries the real judgment.
    """
    if user_id == owner_id:
        return PermissionLevel.OWNER
    method = getattr(type(collaborators), "has_explicit_standing", None)
    if not callable(method):
        return resolve_operable_permission_level(
            collaborators, bot=bot, user_id=user_id, owner_id=owner_id, env=env
        )
    if not method(collaborators, bot=bot, user_id=user_id, env=env):
        return PermissionLevel.NONE
    return resolve_operable_permission_level(
        collaborators, bot=bot, user_id=user_id, owner_id=owner_id, env=env
    )
