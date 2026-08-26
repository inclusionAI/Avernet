"""BcsBotTokenProvider:driver-bot 的 BCS session_token 取数(参考 ocb ZdasBotTokenProvider)。

prod 经 ZDAS ``agentclawdb_ds`` 直读 ``bcs_bots.session_token WHERE bot_uuid=?``(corp 覆写注入 resolver);
本测只验证 TTL 缓存与降级行为(resolver 可替换为桩),不触真实 DB。
"""
from __future__ import annotations

from agentclaw.community.core.task.task_runner.integration.bcs_bot_token_provider import (
    BcsBotTokenProvider, CachingBcsBotTokenProvider, NullBcsBotTokenProvider,
)


def test_null_provider_returns_none():
    p = NullBcsBotTokenProvider()
    assert p.get_token("any_bot") is None
    assert isinstance(p, BcsBotTokenProvider)  # 结构化满足端口


def test_caching_provider_returns_and_caches_within_ttl():
    calls = {"n": 0}

    def resolver(bot_uuid: str) -> str | None:
        calls["n"] += 1
        return "tok-" + bot_uuid

    now = [0.0]
    p = CachingBcsBotTokenProvider(resolver=resolver, ttl_s=300, clock=lambda: now[0])
    assert p.get_token("botA") == "tok-botA"
    assert p.get_token("botA") == "tok-botA"   # 命中缓存
    assert calls["n"] == 1                     # resolver 只调一次
    now[0] = 301                               # 超过 TTL
    assert p.get_token("botA") == "tok-botA"
    assert calls["n"] == 2                     # 过期后重新查询


def test_caching_provider_none_result_short_caches_to_avoid_db_hammer():
    calls = {"n": 0}

    def resolver(bot_uuid: str) -> str | None:
        calls["n"] += 1
        return None

    now = [0.0]
    p = CachingBcsBotTokenProvider(resolver=resolver, ttl_s=300, clock=lambda: now[0])
    assert p.get_token("ghost") is None
    assert p.get_token("ghost") is None        # 未命中结果短缓存
    assert calls["n"] == 1                      # 不重复打 DB
    now[0] = 61                                  # 超过 fail-TTL(≤60)
    assert p.get_token("ghost") is None
    assert calls["n"] == 2                       # 过期后重新查


def test_caching_provider_is_a_bcs_bot_token_provider():
    p = CachingBcsBotTokenProvider(resolver=lambda _u: None)
    assert isinstance(p, BcsBotTokenProvider)
