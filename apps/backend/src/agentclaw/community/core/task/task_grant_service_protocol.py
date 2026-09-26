"""任务认领 Bot 授权服务契约(stateless secbaas 透传中继)。

前端开启「任务认领」时调本服务:grant/revoke 经 ``OpenApiBotPort`` 透传人类 Cookie/Referer
到 secbaas admin 端点;api-key 由服务端持有(``OpenApiBotPort`` 内部),不暴露给前端,亦不落本地表。
派发侧的 claim_on JOIN 改用 BCS ``list_bots_by_task_modes`` 名单(不查 secbaas、不读本地表)。

命名(api-contract §0):``bcs_bot_id``=real:entity ``bot_id:owner_user_id``(/mine 的 bot.id 原值,
secbaas body 字段仍叫 ``bot_id``);``bot_id``=无冒号产品 ID(派发 assignee / JOIN key)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from agentclaw.community.core.task.task_runner.client.ports import OpenApiBotPort
GRANTED = "granted"
REVOKED = "revoked"


@dataclass(frozen=True)
class GrantResult:
    bcs_bot_id: str
    api_key_prefix: str
    grant_status: str
    operator: str


@dataclass(frozen=True)
class RevokeResult:
    bcs_bot_id: str
    grant_status: str


@runtime_checkable
class TaskClaimGrantServiceProtocol(Protocol):
    """任务认领 Bot 授权:stateless 透传浏览器 Cookie/Referer → secbaas(api-key 服务端持有,不落本地表)。"""

    async def grant(
        self, *, bcs_bot_id: str, cookie: str, referer: str, operator: str
    ) -> GrantResult:
        """grant 公共 api-key 给某 Bot:secbaas grant(透传人类 Cookie/Referer)。幂等(secbaas 跳过已 granted)。"""
        ...

    async def revoke(
        self, *, bcs_bot_id: str, cookie: str, referer: str, operator: str
    ) -> RevokeResult:
        """撤销授权:secbaas revoke。幂等。"""
        ...


__all__ = [
    "GRANTED",
    "GrantResult",
    "REVOKED",
    "RevokeResult",
    "TaskClaimGrantServiceProtocol",
]
