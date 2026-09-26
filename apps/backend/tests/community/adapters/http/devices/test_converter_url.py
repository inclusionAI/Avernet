"""Tests for converter url passthrough (plan-01)."""
from agentclaw.community.adapters.http.devices.converter import connection_info_to_response
from agentclaw.community.core.devices.models import DeviceConnectionInfo


def test_response_includes_url_field():
    info = DeviceConnectionInfo(
        type="local",
        target="127.0.0.1:20010",
        token="t",
        engine_type="openclaw",
        url="http://10.0.0.1:20010",
    )
    resp = connection_info_to_response(info)
    assert resp.url == "http://10.0.0.1:20010"


def test_response_url_defaults_empty_when_info_empty():
    info = DeviceConnectionInfo(
        type="local",
        target="127.0.0.1:20010",
        token="t",
        engine_type="openclaw",
    )
    resp = connection_info_to_response(info)
    assert resp.url == ""
