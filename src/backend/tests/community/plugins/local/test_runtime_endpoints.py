"""``RuntimeEndpointRegistry`` — runtime 地址与生命周期解耦的单测。

Runtime 进程重启会换端口（openclaw ``gateway.port`` 变更必须是全进程
重启）。长期修法：调用方不把 URL 烤进配置/env 长期持有，而是按设备身份
在调用时从注册表现场解析；``LocalProcessManager`` 在 spawn/stop 时同
步登记。这里钉住注册表的四条核心语义：

* register → resolve 立即可用（归一化无尾斜杠）；
* 重启换端口 = 同 device 重新 register，下一次 resolve 立即拿到新值
  （热加载，不返回旧地址）——这是"存活 runtime 迁移"的关键；
* unregister / stop_all 对应清空，resolve 返回 None（缺省不猜）；
* 未登记的 device 一律 None —— 绝不回落到任何全局默认（#2435 的
  binding-routed 跨 bot 泄露正是这样产生的）。
"""
from __future__ import annotations

import pytest

from agentclaw.community.plugins.local.runtime_endpoints import (
    RuntimeEndpointRegistry,
)


@pytest.fixture
def registry() -> RuntimeEndpointRegistry:
    return RuntimeEndpointRegistry()


def test_register_then_resolve_roundtrip(registry):
    registry.register("device-1", adapter_url="http://127.0.0.1:20010/")
    resolved = registry.resolve("device-1")
    assert resolved is not None
    assert resolved.adapter_url == "http://127.0.0.1:20010"  # 尾斜杠归一


def test_reregister_hot_swaps_the_address(registry):
    # 全进程网关重启换端口：同一 device 重新登记，解析立即切到新地址。
    registry.register("device-1", adapter_url="http://127.0.0.1:20010")
    registry.register("device-1", adapter_url="http://127.0.0.1:20042")

    assert registry.resolve("device-1").adapter_url == "http://127.0.0.1:20042"


def test_unregister_makes_device_unresolvable(registry):
    registry.register("device-1", adapter_url="http://127.0.0.1:20010")
    assert registry.unregister("device-1") is True
    assert registry.resolve("device-1") is None


def test_unregister_unknown_device_is_tolerated(registry):
    assert registry.unregister("device-404") is False


def test_unregistered_device_never_resolves(registry):
    assert registry.resolve("device-404") is None


def test_reset_instance_returns_fresh_registry():
    RuntimeEndpointRegistry.reset_instance()
    first = RuntimeEndpointRegistry.instance()
    first.register("device-1", adapter_url="http://127.0.0.1:20010")

    RuntimeEndpointRegistry.reset_instance()
    second = RuntimeEndpointRegistry.instance()
    assert second is not first
    assert second.resolve("device-1") is None
    RuntimeEndpointRegistry.reset_instance()