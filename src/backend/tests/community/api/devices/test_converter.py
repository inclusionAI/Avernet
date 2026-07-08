"""Tests for device API converter."""
from agentclaw.community.adapters.http.devices.converter import connection_info_to_response
from agentclaw.community.core.devices.models import DeviceConnectionInfo


class TestConnectionInfoToResponse:
    def test_normalizes_tunnel_to_remote(self):
        info = DeviceConnectionInfo(
            type="tunnel", target="TUNNEL_xxx:20003", token="tok", engine_type="openclaw",
        )
        resp = connection_info_to_response(info)
        assert resp.type == "remote"  # normalized

    def test_keeps_local_as_local(self):
        info = DeviceConnectionInfo(
            type="local", target="127.0.0.1:20003", token="tok", engine_type="openclaw",
        )
        resp = connection_info_to_response(info)
        assert resp.type == "local"

    def test_normalizes_arca_to_remote(self):
        info = DeviceConnectionInfo(
            type="arca", target="ARCA_xxx", token="tok", engine_type="openclaw",
        )
        resp = connection_info_to_response(info)
        assert resp.type == "remote"

    def test_passes_available_and_message(self):
        info = DeviceConnectionInfo(
            type="tunnel", target="", token="", engine_type="openclaw",
            available=False, message="本地引擎未连接",
        )
        resp = connection_info_to_response(info)
        assert resp.available is False
        assert resp.message == "本地引擎未连接"
