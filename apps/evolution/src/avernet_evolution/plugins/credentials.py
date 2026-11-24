"""服务凭证与 Bot 作用域身份（06 §7.1；Genome / Experience 调用共用）。

这里只回答一个问题：**这次调用以谁的身份发给 Backend**。授权本身不在本地判断——
Backend 会按服务凭证 + `acting_user_id` 逐请求裁定，越权仍是 403/404。

静态表只用于例外（服务要以**非 owner** 的身份代表某个 Bot）。表中没有的 Bot
回落到 `bot.owner_id`：这样新建 Bot 不需要改部署配置再重启，也不会出现
"bot scope has no deployment delegation" 这种部署期错误。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .platform_http import PlatformCredential, PlatformHttpError

__all__ = ["MIN_TOKEN_LENGTH", "Delegation", "FileDelegations"]

#: 服务 token 的最小长度；更短视为未下发。
MIN_TOKEN_LENGTH = 16


@dataclass(frozen=True)
class Delegation:
    """一条显式委派：服务以 ``acting_user_id`` 的身份代表该 Bot。"""

    entity_id: str
    bot_id: str
    owner_id: str
    acting_user_id: str


class FileDelegations:
    """从文件读服务 token，并按 Bot 解析 ``acting_user_id``。

    ``delegations`` 接受任何带 ``entity_id`` / ``bot_id`` / ``owner_id`` /
    ``acting_user_id`` 属性的对象（部署配置的 pydantic 模型即可）。
    """

    def __init__(
        self,
        token_file: str | Path,
        delegations: Iterable[Any] = (),
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self.token_file = Path(token_file)
        self._acting = {
            (item.entity_id, item.bot_id, item.owner_id): item.acting_user_id for item in delegations
        }
        self._logger = logger or logging.getLogger(__name__)
        self._announced: set[tuple[str, str, str]] = set()

    async def __call__(self, bot: Any) -> PlatformCredential:
        key = (bot.entity_id, bot.bot_id, bot.owner_id)
        acting = self._acting.get(key)
        if acting is None:
            acting = bot.owner_id
            # 只提示一次：这是回落到 owner 的正常路径，不是每个请求都刷日志。
            if key not in self._announced:
                self._announced.add(key)
                self._logger.warning(
                    "evolution.delegation.fallback bot_id=%s owner_id=%s reason=no_explicit_delegation",
                    bot.bot_id,
                    bot.owner_id,
                )
        if not acting:
            raise PlatformHttpError("bot has no owner to act as")
        try:
            token = self.token_file.read_text().strip()
        except OSError:
            # Secret 是 optional 挂载：服务可以先起来，Run 再以领域错误失败。
            raise PlatformHttpError("app credential is not provisioned") from None
        if len(token) < MIN_TOKEN_LENGTH:
            raise PlatformHttpError("app credential is not provisioned")
        return PlatformCredential({"Authorization": f"Bearer {token}"}, acting)


#: 组合根注入的类型别名（与 ``platform_http.Credentials`` 一致）。
CredentialSource = Callable[[Any], Awaitable[PlatformCredential]]
