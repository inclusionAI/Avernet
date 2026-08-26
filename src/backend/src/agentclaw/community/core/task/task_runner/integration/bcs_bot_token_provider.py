"""BcsBotTokenProvider:driver-bot 的 BCS session_token 取数端口(参考 ocb ``ZdasBotTokenProvider``)。

为什么需要:BCS 建群(``POST /groups``)在带 ``event_subscriptions`` 时走 ``require_human``,拒 Bot token;
去掉订阅后虽可 HMAC-only 匿名建群,但参考 ocb,把 driver-bot 的 session_token 作为 ``Authorization: Bearer``
携带,让 BCS ``resolve_group_create_caller`` 把 caller 解析成 driver/originator bot(带归属、统一个 caller 身份)。

token 来源:BCS 没有"取 bot token"的 HTTP 接口 —— token 在 bot 经 ``/ws/bot`` 首连时由服务端
``new_session_token()`` 签发(``provider_core.rs``),写库表 ``bcs_bots.session_token``,只在 connect 帧回给该 bot。
唯一既存取法 = 直读 ``bcs_bots.session_token`` 库表(ocb 同款,经 ZDAS ``agentclawdb_ds``)。

本模块只提供端口 + TTL 缓存包装 + 空实现;真实 ``SELECT session_token FROM bcs_bots WHERE bot_uuid=%s``
的 ZDAS resolver 由 corp 覆写注入(prod 经 ``agentclawdb_ds``),本地/singlebox/double 用 ``NullBcsBotTokenProvider``。
"""
from __future__ import annotations

import time
from typing import Callable, Protocol, runtime_checkable


@runtime_checkable
class BcsBotTokenProvider(Protocol):
    """``bcs_bot_uuid -> session_token`` 解析端口(带缓存由实现负责)。"""

    def get_token(self, bcs_bot_uuid: str) -> str | None: ...


class NullBcsBotTokenProvider:
    """无 token 实现(本地/singlebox/double/未配置):恒返回 None。

    搭配"去掉 event_subscriptions"建群走 no-sub 分支,无需 token;本实现下 ``caller_bot_token`` 不发,
    行为同未配置 provider(向后兼容)。
    """

    def get_token(self, bcs_bot_uuid: str) -> str | None:
        return None


# 未命中时短缓存(秒):避免对 DB 反复打同一条不存在的 bot。对齐 ocb ZdasBotTokenProvider("查失败也缓存短 TTL")。
_DEFAULT_FAIL_TTL_S: float = 60.0


class CachingBcsBotTokenProvider:
    """对 ``resolver`` 包一层进程内 TTL 缓存,命中/未命中分别缓存。

    ``resolver`` 是真实查数闭包(``bcs_bot_uuid -> session_token 或 None``);prod 由 corp 覆写注入
    直读 ``bcs_bots.session_token``(ZDAS ``agentclawdb_ds``),测试/本地注入桩。不把 token 明文写日志。

    Args:
        resolver: 真实查数闭包。
        ttl_s: 命中缓存有效期(秒),默认 300(5 分钟,对齐 ocb)。
        clock: 可注入的单调时钟(默认 ``time.monotonic``),便于测试不依赖真睡。
    """

    def __init__(
        self,
        resolver: Callable[[str], str | None],
        *,
        ttl_s: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._resolver = resolver
        self._ttl_s = ttl_s
        self._clock = clock
        # _cache: bot_uuid -> (token or "", expire_at)。空串哨兵表示一次未命中的短缓存。
        self._cache: dict[str, tuple[str, float]] = {}

    def get_token(self, bcs_bot_uuid: str) -> str | None:
        now = self._clock()
        cached = self._cache.get(bcs_bot_uuid)
        if cached is not None:
            token, expire_at = cached
            if now < expire_at:
                return token or None
        token = self._resolver(bcs_bot_uuid)
        if token:
            self._cache[bcs_bot_uuid] = (token, now + self._ttl_s)
            return token
        # 未命中也短缓存,避免反复查库打爆 DB。
        self._cache[bcs_bot_uuid] = ("", now + min(self._ttl_s, _DEFAULT_FAIL_TTL_S))
        return None
