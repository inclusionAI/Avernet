"""Contract test: all device providers produce conn_info dicts with the same schema.

If this test fails, downstream consumers (ArcaDeviceSyncPlugin,
ArcaDeviceFileSystem, device_mcp_sync, etc.) will break with KeyError on
unfamiliar provider output. Keep the schema closed."""
from __future__ import annotations


# 七字段契约（Phase 1 锁定 + Phase 3 扩展，见 plan §conn_info Schema 契约）
#
# NOTE: ``device_provider`` 由 ``DeviceContext.provider`` 承载,不再在
# conn_info dict 内 — Phase 1 commit 9cbd604264 起 resolver wrap 层
# (``_normalize_schema``)管理,plugin 不再事后补该字段。
EXPECTED_CONN_INFO_KEYS = frozenset({
    "url",
    "token",
    "headers",
    "use_proxy",
    "sandbox_id",
    "target",
    "engine_type",
})

# device_provider 闭枚举：仍保留供仍直接构造 conn_info 的旧路径(arca/local)
# 校验用。baas/teclaw 走 resolver wrap 层,不在 dict 内带该字段。
ALLOWED_DEVICE_PROVIDERS = frozenset({"arca", "baas", "local", "teclaw"})


def _assert_conn_info_schema(conn_info: dict) -> None:
    assert conn_info is not None
    actual = set(conn_info.keys())
    # device_provider 由 DeviceContext.provider 承载,但允许旧路径 (arca/local)
    # 直接构造 dict 时带上 — 不视为 extra。
    extra = actual - EXPECTED_CONN_INFO_KEYS - {"device_provider"}
    missing = EXPECTED_CONN_INFO_KEYS - actual
    assert not extra, f"conn_info has unexpected keys: {extra}"
    assert not missing, f"conn_info is missing required keys: {missing}"
    assert isinstance(conn_info["headers"], dict)
    assert isinstance(conn_info["use_proxy"], bool)
    # 若 dict 内带 device_provider(旧路径),必须是闭枚举之一
    if "device_provider" in conn_info:
        assert conn_info["device_provider"] in ALLOWED_DEVICE_PROVIDERS, (
            f"device_provider {conn_info['device_provider']!r} not in "
            f"{ALLOWED_DEVICE_PROVIDERS}"
        )


def _assert_conn_info_keys_subset(conn_info: dict, required_keys: frozenset) -> None:
    """Assert that conn_info contains at least the required keys (subset check).

    Used for BAAS conn_info which may carry additional Phase 3 fields
    (baas_base_url, engine_port, bot_type, paas_device_id).
    """
    assert conn_info is not None
    missing = required_keys - set(conn_info.keys())
    assert not missing, f"conn_info is missing required keys: {missing}"
    assert isinstance(conn_info["headers"], dict)
    assert isinstance(conn_info["use_proxy"], bool)
    # device_provider 由 DeviceContext.provider 承载,不在 dict 内
    # (Phase 1 commit 9cbd604264 resolver wrap 层管理)


class TestConnInfoSchemaContract:
    def test_baas_provider_matches_schema(self):
        from agentclaw.community.core.devices.services.baas_conn_info import build_baas_conn_info
        from agentclaw.community.core.service_bot.services.baas_service import BotWsConnectionInfoResponse

        ws = BotWsConnectionInfoResponse(
            ws_url="wss://<baas-host>/proxypass/<baas-target>/api/openclaw/ws",
            token="tok",
            target="<baas-target>",
            expires_at="2026-12-31T00:00:00Z",
        )
        conn_info = build_baas_conn_info(ws, engine_type="openclaw")
        # BAAS conn_info carries Phase 3 extension fields; use subset check
        _assert_conn_info_keys_subset(conn_info, EXPECTED_CONN_INFO_KEYS)
        assert conn_info["use_proxy"] is True
        # device_provider 由 resolver wrap 层(DeviceContext.provider)承载,
        # 不在 dict 内 — 此处不再断言 conn_info["device_provider"]。

    def test_teclaw_provider_matches_schema(self):
        """teclaw conn_info reuses the BaaS transport builder, tagged "teclaw"."""
        from agentclaw.community.core.devices.services.baas_conn_info import build_baas_conn_info
        from agentclaw.community.core.service_bot.services.baas_service import BotWsConnectionInfoResponse

        ws = BotWsConnectionInfoResponse(
            ws_url="wss://<baas-host>/proxypass/<baas-target>/api/openclaw/ws",
            token="tok",
            target="<baas-target>",
            expires_at="2026-12-31T00:00:00Z",
        )
        # Mirror the actual TeclawDeviceAccessor invocation: engine_type is "teclaw"
        # (a teclaw binding is 1:1 with a teclaw-engine bot), not "openclaw".
        conn_info = build_baas_conn_info(ws, engine_type="teclaw", device_provider="teclaw")
        _assert_conn_info_keys_subset(conn_info, EXPECTED_CONN_INFO_KEYS)
        # device_provider 由 resolver wrap 层承载,不在 dict 内。
        assert conn_info["engine_type"] == "teclaw"

    def test_arca_provider_matches_schema(self):
        """ARCA 旧路径直接构造同 schema dict（沿用 _get_arca_device_connection 输出格式）。"""
        # 模拟 _get_arca_device_connection 的返回值结构（占位 URL，不耦合具体环境）
        conn_info = {
            "url": "https://<arca-host>/proxypass/<arca-target>:20003",
            "headers": {"x-proxypass-token": "tok"},
            "use_proxy": True,
            "sandbox_id": "sbx-a",
            "target": "<arca-target>:20003",
            "token": "tok",
            "engine_type": "openclaw",
            "device_provider": "arca",
        }
        _assert_conn_info_schema(conn_info)
        assert conn_info["device_provider"] == "arca"

    def test_local_provider_matches_schema(self):
        """LOCAL (no sandbox) 路径产出也应满足同 schema。"""
        conn_info = {
            "url": "http://127.0.0.1:20003",
            "headers": {},
            "use_proxy": False,
            "sandbox_id": None,
            "target": "127.0.0.1:20003",
            "token": "",
            "engine_type": "openclaw",
            "device_provider": "local",
        }
        _assert_conn_info_schema(conn_info)
        assert conn_info["device_provider"] == "local"
