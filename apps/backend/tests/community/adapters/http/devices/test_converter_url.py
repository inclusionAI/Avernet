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


def test_response_includes_a2a_base_url_issued_by_provider():
    info = DeviceConnectionInfo(
        type="remote",
        target="10.0.0.1:20010",
        token="t",
        engine_type="openclaw",
        a2a_base_url="https://gw.example.com/a2a",
    )
    resp = connection_info_to_response(info)
    assert resp.a2a_base_url == "https://gw.example.com/a2a"


def test_response_a2a_base_url_defaults_empty_when_not_delivered():
    # 空串语义：无可交付的 A2A 面——caller 按原行为回落，不得代填。
    info = DeviceConnectionInfo(
        type="local",
        target="127.0.0.1:20010",
        token="t",
        engine_type="openclaw",
    )
    resp = connection_info_to_response(info)
    assert resp.a2a_base_url == ""
