"""community profile 默认的 BcsBotTokenProvider:经 prod ``DatabasePlugin`` 直读
``bcs_bots.session_token`` 作建群 driver-bot 的 ``Authorization: Bearer`` caller 身份。

core 只留中性端口 + 缓存包装 + 空实现(见 ``core/task/task_runner/integration/bcs_bot_token_provider``);
本处放 corp/数据源的具体读法(具体读哪张库表)。BCS 无"取 token"HTTP 接口——token 在 bot 经 ``/ws/bot`` 首连时由服务端
``new_session_token()`` 签发、写库表 ``bcs_bots.session_token``、只在 connect 帧回该 bot;
corp prod ``DatabasePlugin.orm_session()`` 连到 ``bcs_bots`` 所在数据源 → 可查,
本地/singlebox 无该表 → 查询抛错被吞 → None(不发 Bearer,本地 BCS 忽略鉴权,无害,不阻断建群)。
本类不提厂商数据基建名(仅描述能力:经 DatabasePlugin 读 bcs_bots.session_token)。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

from sqlalchemy import text

from agentclaw.community.core.task.task_runner.integration.bcs_bot_token_provider import (
    CachingBcsBotTokenProvider,
)
from agentclaw.community.plugin_api.database import DatabasePlugin

logger = logging.getLogger(__name__)


class DbBcsBotTokenProvider:
    """经 prod ``DatabasePlugin`` 直读 ``bcs_bots.session_token``,套 ``CachingBcsBotTokenProvider`` 做 TTL 缓存。

    corp prod ``DatabasePlugin.orm_session()`` 连到 ``bcs_bots`` 所在数据源 → 可查;
    本地 SQLite 无 ``bcs_bots`` 表 → 查询抛错被吞 → 返 None(不发 Bearer,本地 BCS 忽略鉴权,无害)。

    Args:
        database_plugin: ``DatabasePlugin``(DI 注入;corp=真实数据源,本地=SQLite)。
            ``orm_session()`` 出 SQLAlchemy Session。
        env: 可选环境列过滤(``bcs_bots.env``);省略只按 ``bot_uuid`` 查。
        ttl_s: 命中缓存有效期(秒),默认 300。
        clock: 可注入单调时钟(默认 ``time.monotonic``),便于测试。
    """

    def __init__(
        self,
        database_plugin: DatabasePlugin,
        *,
        env: str | None = None,
        ttl_s: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._db = database_plugin
        self._env = env
        self._caching = CachingBcsBotTokenProvider(self._query_token, ttl_s=ttl_s, clock=clock)

    def get_token(self, bcs_bot_uuid: str) -> str | None:
        return self._caching.get_token(bcs_bot_uuid)

    def _query_token(self, bcs_bot_uuid: str) -> str | None:
        sql = "SELECT session_token FROM bcs_bots WHERE bot_uuid = :uuid"
        params: dict[str, str] = {"uuid": bcs_bot_uuid}
        if self._env:
            sql += " AND env = :env"
            params["env"] = self._env
        sql += " LIMIT 1"
        try:
            with self._db.orm_session() as session:
                row: Any = session.execute(text(sql), params).first()
        except Exception:  # noqa: BLE001 本地无 bcs_bots 表 / 查询失败 → None(不发 Bearer,降级不阻断建群)
            logger.warning(
                "[task][bcs_bot_token] 读 bcs_bots.session_token 失败 bot_uuid=%s(本地无此表属正常)",
                bcs_bot_uuid, exc_info=True,
            )
            return None
        if row is None:
            return None
        token = row[0]
        return str(token) if token else None
