"""transport binding-routed 解析改道 ``RuntimeEndpointRegistry`` 的单测。

#2435 终审判定的 CONFIRMED 缺陷：binding-routed conn_info（只有设备身份、
无 url/target）落到全局 ``SINGLEBOX_ENGINE_ADAPTER_URL`` —— 跨 bot 串
数据。长期修法即本组断言的行为：

* 设备在注册表登记后，binding-routed 调用按 ``bot_uuid`` 解析到该设备
  自己的 adapter 地址；
* runtime 重启换端口（重新 register）后，下一次解析立即拿到新值；
* 两个设备并发存在时各解析各的地址（不串台）；
* 设备身份在场但注册表无记录时**宁可拒绝代理也不落全局默认**；
* conn_info 带显式 url/target 时仍按生产契约优先（注册表只补位，不越权）；
* 完全没有设备身份的调用保留原 default 兜底（兼容既有调用方）。
"""
from __future__ import annotations

from agentclaw.community.plugins.local.device_adapter_transport import (
    InMemoryDeviceAdapterTransport,
)
from agentclaw.community.plugins.local.runtime_endpoints import (
    RuntimeEndpointRegistry,
)

_GLOBAL = "http://global-fallback.example.com"


def _binding_routed_conn_info(device_id: str) -> dict:
    """Mirror ``resolve_for_binding_invoke`` 的 binding-routed 形状。"""
    return {
        "bind_id": 101,
        "bot_uuid": device_id,
        "engine_port": 8317,
        "engine_type": "openclaw",
        "type": "baas",
        "headers": {},
        "device_affinity": "user-1",
    }


def _transport(registry: RuntimeEndpointRegistry) -> InMemoryDeviceAdapterTransport:
    return InMemoryDeviceAdapterTransport(
        default_adapter_url=_GLOBAL,
        endpoint_registry=registry,
    )


def test_binding_routed_resolves_per_device_not_global():
    registry = RuntimeEndpointRegistry()
    registry.register("device-a", adapter_url="http://127.0.0.1:20010")

    resolved = _transport(registry)._resolve_adapter_url(
        _binding_routed_conn_info("device-a")
    )
    assert resolved == "http://127.0.0.1:20010"


def test_registry_hot_reload_new_port_is_visible_immediately():
    registry = RuntimeEndpointRegistry()
    transport = _transport(registry)
    conn = _binding_routed_conn_info("device-a")

    registry.register("device-a", adapter_url="http://127.0.0.1:20010")
    registry.register("device-a", adapter_url="http://127.0.0.1:20042")

    assert transport._resolve_adapter_url(conn) == "http://127.0.0.1:20042"


def test_two_devices_resolve_their_own_addresses():
    registry = RuntimeEndpointRegistry()
    transport = _transport(registry)

    registry.register("device-a", adapter_url="http://127.0.0.1:20010")
    registry.register("device-b", adapter_url="http://127.0.0.1:20011")

    assert transport._resolve_adapter_url(
        _binding_routed_conn_info("device-a")
    ) == "http://127.0.0.1:20010"
    assert transport._resolve_adapter_url(
        _binding_routed_conn_info("device-b")
    ) == "http://127.0.0.1:20011"


def test_absent_runtime_declines_instead_of_global_fallback():
    # 设备身份在场但 runtime 不在注册表：宁可拒绝（None → unhandled），
    # 也不落全局地址 —— 全局兜底正是跨 bot 串数据的路径。
    registry = RuntimeEndpointRegistry()
    transport = _transport(registry)

    assert (
        transport._resolve_adapter_url(_binding_routed_conn_info("device-404"))
        is None
    )


def test_unregister_stops_resolution():
    registry = RuntimeEndpointRegistry()
    transport = _transport(registry)
    registry.register("device-a", adapter_url="http://127.0.0.1:20010")
    registry.unregister("device-a")

    assert transport._resolve_adapter_url(_binding_routed_conn_info("device-a")) is None


def test_explicit_url_wins_over_registry():
    # 生产契约：conn_info 自带完整 url 时按它走（注册表只补 binding-routed
    # 的缺位，不越权覆写显式地址）。
    registry = RuntimeEndpointRegistry()
    registry.register("device-a", adapter_url="http://127.0.0.1:20010")

    conn = _binding_routed_conn_info("device-a")
    conn["url"] = "https://baas.example.com/invoke-http"

    assert (
        _transport(registry)._resolve_adapter_url(conn)
        == "https://baas.example.com/invoke-http"
    )


def test_identityless_conn_info_keeps_default_fallback():
    registry = RuntimeEndpointRegistry()
    registry.register("device-a", adapter_url="http://127.0.0.1:20010")

    assert (
        _transport(registry)._resolve_adapter_url({})
        == _GLOBAL
    )