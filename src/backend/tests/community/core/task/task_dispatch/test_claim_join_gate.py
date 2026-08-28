"""``TaskClaimJoinGate`` 真实实现契约测试。

回归点: ``set_enabled`` 经 ``_SystemConfigStore.set_config`` 落库,关键字必须与
``SystemConfigService.set_config`` 签名(``operator=``)一致;此前误用 ``creator=``
导致 POST /claim-join-filter 500。本测试用最小 fake store(签名 ``operator=``)
锁定该契约,并覆盖 fail-open(config 未装配)路径。
"""

from __future__ import annotations

from agentclaw.community.core.task.task_dispatch.claim_join_gate import (
    CATEGORY,
    KEY,
    TaskClaimJoinGate,
)


class _FakeStore:
    """最小系统配置 KV fake:签名对齐 SystemConfigService.set_config(operator=)。"""

    def __init__(self, value=None, exc=None) -> None:
        self._value = value
        self._exc = exc
        self.set_calls: list[dict] = []

    def get_config(self, *, category, config_key, env):
        if self._exc is not None:
            raise self._exc
        return self._value

    def set_config(
        self,
        *,
        category,
        config_key,
        config_value,
        env,
        description=None,
        operator=None,
    ) -> int:
        self.set_calls.append(
            {
                "category": category,
                "config_key": config_key,
                "config_value": config_value,
                "env": env,
                "description": description,
                "operator": operator,
            }
        )
        self._value = config_value
        return 1


def test_set_enabled_writes_with_operator_keyword():
    store = _FakeStore()
    gate = TaskClaimJoinGate(config=store)
    result = gate.set_enabled(enabled=True, env="pre", operator="146836")

    assert result is True
    assert len(store.set_calls) == 1
    call = store.set_calls[0]
    assert call["category"] == CATEGORY
    assert call["config_key"] == KEY
    assert call["config_value"] is True
    assert call["env"] == "pre"
    assert call["operator"] == "146836"  # 关键字契约:必须是 operator=,不是 creator=
    # 写穿本地缓存:set_enabled 后 is_enabled 立即 True(无需等 KV 读)
    assert gate.is_enabled() is True


def test_get_enabled_reads_store_value():
    store = _FakeStore(value=True)
    gate = TaskClaimJoinGate(config=store)
    assert gate.get_enabled(env="pre") is True


def test_config_none_fail_open():
    gate = TaskClaimJoinGate(config=None)
    # 未装配配置子系统:set 不落库返回 False(get_enabled 也恒 False),不抛异常
    assert gate.set_enabled(enabled=True, env="pre", operator="u") is False
    assert gate.is_enabled() is False
    assert gate.get_enabled(env="pre") is False


def test_get_config_raises_fail_open():
    store = _FakeStore(exc=RuntimeError("db down"))
    gate = TaskClaimJoinGate(config=store)
    assert gate.is_enabled() is False
    assert gate.get_enabled(env="pre") is False
