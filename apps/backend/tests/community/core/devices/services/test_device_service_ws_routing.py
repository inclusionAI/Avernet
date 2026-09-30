"""Tests for WebSocket routing in ``DeviceService.get_device_connection_v2``.

Verifies that 4 bot types route to the correct backend:
- Desktop bot → BaaS invoke-http
- Service bot (ARCA_ target) → ARCA proxypass
- Poolab BaaS bot (non-ARCA target) → BaaS invoke-http
- Arca personal bot (sandbox_id) → ARCA proxypass
"""
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.devices.models import DeviceConnectionInfo


@dataclass
class _FakeDeviceResult:
    device_props: dict
    device_provider: str = "local"


def _build_service():
    """Build a minimal DeviceService with mocked dependencies."""
    from agentclaw.community.core.devices.services.device_service import DeviceService

    svc = DeviceService.__new__(DeviceService)
    svc._repo = MagicMock()
    svc._logger = MagicMock()
    # ARCA-proxy branch of get_device_connection_v2 builds the URL via the
    # injected SandboxRuntimeClient (proxy base/target).
    svc._sandbox_client = MagicMock()
    svc._sandbox_client.proxy_base_url.return_value = "http://arca-proxy"
    svc._sandbox_client.proxy_target.side_effect = lambda sid, **kw: f"ARCA_{sid}:20003"
    return svc


def _call_v2(
    svc,
    conn_info: DeviceConnectionInfo,
    sandbox_id: str | None = None,
    device_provider: str = "local",
):
    """Call get_device_connection_v2 with mocked internals."""
    device_result = _FakeDeviceResult(
        device_props={"sandbox_id": sandbox_id},
        device_provider=device_provider,
    )
    svc.get_device = MagicMock(return_value=device_result)
    svc.get_device_connection = MagicMock(return_value=conn_info)

    return svc.get_device_connection_v2(
        user_id="u001",
        nick_name="test",
        binding_id=42,
    )


class TestWsRoutingServiceBot:
    """Service bot (ARCA_ target) → ARCA proxypass.

    The ARCA proxy base/target come from the injected ``SandboxRuntimeClient``
    (mocked in ``_build_service``), not from corp ``arca_io`` — the B6 devendoring
    moved that behind the seam — so this routing test is corp-free.
    """

    def test_service_bot_routes_to_proxypass(self):
        svc = _build_service()
        conn = DeviceConnectionInfo(
            type="baas",
            target="ARCA_svc@alt:9999",
            token="tok",
            engine_type="openclaw",
        )

        result = _call_v2(svc, conn, sandbox_id=None, device_provider="arca")

        assert result["use_proxy"] is True
        assert "/proxypass/" in result["url"]
        assert result["type"] == "baas"


class TestWsRoutingDesktop:
    """Desktop bot → BaaS invoke-http (existing, unchanged)."""

    def test_desktop_routes_to_invoke_http(self):
        svc = _build_service()
        conn = DeviceConnectionInfo(
            type="desktop",
            target="desktop-target",
            token="tok",
            engine_type="aicoding",
            baas_base_url="http://baas.local",
            bot_uuid="BOT-DESKTOP-1",
            tenant="default",
            engine_port=20003,
        )

        result = _call_v2(svc, conn)

        assert result["use_proxy"] is True
        assert "/invoke-http/" in result["url"]
        assert "BOT-DESKTOP-1" in result["url"]
        assert result["type"] == "desktop"


class TestWsRoutingArcaPersonal:
    """Arca personal bot (sandbox_id present) → ARCA proxypass.

    Corp-free: the ARCA proxy base/target are served by the mocked
    ``SandboxRuntimeClient`` seam, not corp ``arca_io``.
    """

    def test_arca_personal_routes_to_proxypass(self):
        svc = _build_service()
        conn = DeviceConnectionInfo(
            type="proxy",
            target="192.168.1.1:20003",
            token="tok",
            engine_type="aicoding",
        )

        result = _call_v2(svc, conn, sandbox_id="sandbox-abc", device_provider="arca")

        assert result["use_proxy"] is True
        assert "/proxypass/" in result["url"]
