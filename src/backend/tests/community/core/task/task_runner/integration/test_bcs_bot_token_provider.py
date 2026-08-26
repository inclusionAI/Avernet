"""BcsBotTokenProvider:driver-bot 的 BCS session_token 取数(参考 ocb ZdasBotTokenProvider)。

prod 经 ZDAS ``agentclawdb_ds``(本仓 prod DatabasePlugin,即 bcs_bots 所在库)直读
``bcs_bots.session_token WHERE bot_uuid=?``;本测用伪 DatabasePlugin 验证 SQL/缓存/降级,不触真实 DB。
"""
from __future__ import annotations

from agentclaw.community.core.task.task_runner.integration.bcs_bot_token_provider import (
    BcsBotTokenProvider, CachingBcsBotTokenProvider, NullBcsBotTokenProvider,
    ZdasBcsBotTokenProvider,
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


# ===== ZdasBcsBotTokenProvider:经 DatabasePlugin.orm_session() 直读 bcs_bots.session_token + TTL 缓存 =====

class _FakeRow:  # 模拟 sqlalchemy Row:row[0] = session_token
    def __init__(self, value):
        self._value = value

    def __getitem__(self, idx):
        return self._value


class _FakeResult:
    def __init__(self, row):
        self._row = row  # Row 或 None(未命中)

    def first(self):
        return self._row


class _FakeSession:
    def __init__(self, value=None, *, raises=False, execute_calls=None):
        self._value = value
        self._raises = raises
        self._execute_calls = execute_calls

    def execute(self, sql, params):
        if self._execute_calls is not None:
            self._execute_calls.append(params.get("uuid"))
            assert "bcs_bots" in str(sql), "应直读 bcs_bots 表"
        if self._raises:
            raise RuntimeError("no such table: bcs_bots")
        return _FakeResult(_FakeRow(self._value) if self._value is not None else None)


class _FakeCm:
    def __init__(self, session):
        self._session = session

    def __enter__(self):
        return self._session

    def __exit__(self, *exc):
        return False


class _FakeDbPlugin:
    def __init__(self, session):
        self._session = session

    def orm_session(self):
        return _FakeCm(self._session)


def test_zdas_provider_reads_session_token_and_caches():
    calls = []
    now = [0.0]
    p = ZdasBcsBotTokenProvider(
        _FakeDbPlugin(_FakeSession("tok-drv", execute_calls=calls)),
        ttl_s=300, clock=lambda: now[0],
    )
    assert p.get_token("drv:35983") == "tok-drv"
    assert p.get_token("drv:35983") == "tok-drv"   # 命中缓存
    assert calls == ["drv:35983"]                   # 只查一次
    now[0] = 301                                    # 超过 TTL
    assert p.get_token("drv:35983") == "tok-drv"
    assert calls == ["drv:35983", "drv:35983"]      # 过期重查


def test_zdas_provider_returns_none_when_not_found():
    calls = []
    p = ZdasBcsBotTokenProvider(_FakeDbPlugin(_FakeSession(None, execute_calls=calls)))
    assert p.get_token("ghost") is None             # .first() 返 None → None
    assert calls == ["ghost"]


def test_zdas_provider_returns_none_when_db_errors():
    p = ZdasBcsBotTokenProvider(_FakeDbPlugin(_FakeSession(raises=True)))  # 本地 SQLite 无 bcs_bots 表 → 抛错
    assert p.get_token("drv:35983") is None         # 吞错降级,不发 Bearer


def test_zdas_provider_is_a_bcs_bot_token_provider():
    p = ZdasBcsBotTokenProvider(_FakeDbPlugin(_FakeSession(None)))
    assert isinstance(p, BcsBotTokenProvider)

