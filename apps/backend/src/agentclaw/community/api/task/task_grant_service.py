"""任务认领 Bot 授权服务契约 + 实现(stateless secbaas 透传中继)。

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
from agentclaw.community.log import get_logger

logger = get_logger()

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


class TaskClaimGrantService(TaskClaimGrantServiceProtocol):
    """默认实现:复用 corp 注入的 ``OpenApiBotPort``(api_key/prefix/base_url,服务端持有)。

    不涉及 corp 绑定的 ocb 改动:api_key/prefix/base_url 来自 corp overlay 绑定的 ``OpenApiBotAdapter``
    (源 ocb ``openapi_bot`` config),cookie/referer 取自入站请求头(per-request)。无本地表落库。"""

    def __init__(self, *, bot: OpenApiBotPort | None = None) -> None:
        self._bot = bot

    async def grant(
        self, *, bcs_bot_id: str, cookie: str, referer: str, operator: str
    ) -> GrantResult:
        if self._bot is None:
            raise RuntimeError(
                "TaskClaimGrantService 未装配 OpenApiBotPort(corp overlay 缺失,无法 grant)"
            )
        logger.info("[task][grant] >>> bcs_bot_id=%s operator=%s", bcs_bot_id, operator)
        # secbaas grant(透传人类 Cookie/Referer;失败 401/403/4xx/5xx → OpenApiAuth/BadRequest/ServerError 上抛)
        await self._bot.grant(bcs_bot_id=bcs_bot_id, cookie=cookie, referer=referer)
        logger.info(
            "[task][grant] <<< secbaas grant ok prefix=%s bcs_bot_id=%s",
            self._bot.api_key_prefix,
            bcs_bot_id,
        )
        return GrantResult(
            bcs_bot_id=bcs_bot_id,
            api_key_prefix=self._bot.api_key_prefix,
            grant_status=GRANTED,
            operator=operator,
        )

    async def revoke(
        self, *, bcs_bot_id: str, cookie: str, referer: str, operator: str
    ) -> RevokeResult:
        if self._bot is None:
            raise RuntimeError(
                "TaskClaimGrantService 未装配 OpenApiBotPort(corp overlay 缺失,无法 revoke)"
            )
        logger.info("[task][revoke] >>> bcs_bot_id=%s operator=%s", bcs_bot_id, operator)
        await self._bot.revoke(bcs_bot_id=bcs_bot_id, cookie=cookie, referer=referer)
        logger.info("[task][revoke] <<< secbaas revoke ok bcs_bot_id=%s", bcs_bot_id)
        return RevokeResult(bcs_bot_id=bcs_bot_id, grant_status=REVOKED)
