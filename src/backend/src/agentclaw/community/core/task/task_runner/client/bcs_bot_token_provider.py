"""BcsBotTokenProvider:driver-bot 的 BCS session_token 取数端口(core 只含中性端口 + 缓存包装 + 空实现)。

为什么需要:BCS 建群(``POST /groups``)带 ``event_subscriptions`` 时走 ``require_human``,拒 Bot token;
参考 ocb 把 driver-bot 的 session_token 作为 ``Authorization: Bearer`` 携带,让 BCS 把 caller 解析成
driver/originator bot(带归属、统一个 caller 身份)。core 不挂厂商数据基建名;具体读法(DB 直读
``bcs_bots.session_token`` 等)属 corp/数据源具体实现,由 corp 经 DI bind ``BcsBotTokenProvider`` 注入
(见 task_module);未注入时默认 ``NullBcsBotTokenProvider``(不发 Bearer,去 event_subscriptions 后
no-sub 分支 HMAC 匿名建群亦成,降级不阻断)。

core 只暴露中性 ``BcsBotTokenProvider`` 端口 + ``CachingBcsBotTokenProvider`` 缓存包装 +
``NullBcsBotTokenProvider``。
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

    搭配建群不挂订阅/无 token 时走 no-sub 分支,无需 token;本实现下 ``caller_bot_token`` 不发,
    行为同未配置 provider(向后兼容)。
    """

    def get_token(self, bcs_bot_uuid: str) -> str | None:
        return None


# 未命中时短缓存(秒):避免对 DB 反复打同一条不存在的 bot。对齐 ocb("查失败也缓存短 TTL")。
_DEFAULT_FAIL_TTL_S: float = 60.0


class CachingBcsBotTokenProvider:
    """对 ``resolver`` 包一层进程内 TTL 缓存,命中/未命中分别缓存。

    ``resolver`` 是真实查数闭包(``bcs_bot_uuid -> session_token 或 None``);prod 由 corp 覆写注入
    直读 ``bcs_bots.session_token`` 的 resolver(放在 community/plugins),测试/本地注入桩。不把 token 明文写日志。

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
