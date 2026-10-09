"""任务认领 Bot 授权服务实现(stateless secbaas 透传中继)。

契约(Protocol / DTO / 状态常量)定义在 ``core/task/task_grant_service_protocol.py``,
由 ``api/task/task_grant_service.py`` 转出给 adapters。
"""

from __future__ import annotations

from agentclaw.community.core.task.task_grant_service_protocol import (
    GRANTED,
    REVOKED,
    GrantResult,
    RevokeResult,
    TaskClaimGrantServiceProtocol,
)
from agentclaw.community.core.task.task_runner.client.ports import OpenApiBotPort
from agentclaw.community.log import get_logger

logger = get_logger()


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
