"""transport binding-routed 解析的终态语义（RuntimeEndpointRegistry 移除后）。

#2435 判定的 CONFIRMED 缺陷：binding-routed conn_info（有设备身份、无
url/target）落到全局 ``SINGLEBOX_ENGINE_ADAPTER_URL`` —— 跨 bot 串数据。

长期修法在 k8s 终局下由 BaaS 侧承担：local_k8s 插件按设备解析出 NodePort
后，经 binding 的显式 ``url``/``target`` 下发，本进程无需持有任何
per-device 地址（后端进程内也没有能生产它的事件源——bot 拉起发生在
BaaS 侧）。因此 transport 的终态契约收敛为：

* 显式 ``url``/``target`` 按生产契约优先（含 target 为裸 loopback 时的
  本地改写）；
* binding-routed（``bot_uuid`` 在场、无地址）**decline（``None``）**——
  宁可拒绝也不借全局地址，后者正是 #2435 的跨 bot 泄露路径；
* 完全无设备身份的调用保留 ``default_adapter_url`` 兜底（既有 test
  部署的契约）。
"""

from __future__ import annotations

from agentclaw.community.plugins.local.device_adapter_transport import (
    InMemoryDeviceAdapterTransport,
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


def _transport() -> InMemoryDeviceAdapterTransport:
    # 不再接受（也不需要）任何注册表注入 —— 进程内没有 per-device 来源。
    return InMemoryDeviceAdapterTransport(default_adapter_url=_GLOBAL)


def test_binding_routed_declines_instead_of_global_fallback():
    # 有身份、无地址：拒绝（None → "unhandled path" 哨兵）而不是借全局。
    # 全局兜底对 binding-routed 的每一次放行都是一次潜在跨 bot 串数据。
    assert _transport()._resolve_adapter_url(
        _binding_routed_conn_info("device-404")
    ) is None


def test_binding_routed_declines_even_with_default_set():
    # default 只服务无身份调用：配置了 SINGLEBOX_ENGINE_ADAPTER_URL 也
    # 不让有身份的调用借道 —— 那正是 #2441 review 确认的 k8s 误路由面。
    assert _transport()._resolve_adapter_url(
        _binding_routed_conn_info("device-live")
    ) is None


def test_explicit_target_wins_over_identity_decline():
    # 生产契约：设备带着显式 target（远端 BaaS / NodePort 解析产物）时按它走。
    conn = _binding_routed_conn_info("device-a")
    conn["target"] = "10.0.0.5:20010"
    assert _transport()._resolve_adapter_url(conn) == "http://10.0.0.5:20010"


def test_explicit_url_wins_over_identity_decline():
    # 同上：url 是显式传输契约，身份在场不改变它。
    conn = _binding_routed_conn_info("device-a")
    conn["url"] = "https://baas.example.com/invoke-http"
    assert _transport()._resolve_adapter_url(conn) == (
        "https://baas.example.com/invoke-http"
    )


def test_identityless_conn_info_keeps_default_fallback():
    # 无身份调用（测试部署既有的哨兵行为）保持 default 兜底。
    assert _transport()._resolve_adapter_url({}) == _GLOBAL
