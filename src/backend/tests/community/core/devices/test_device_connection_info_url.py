"""Tests for DeviceConnectionInfo.url field (plan-01 added)."""
from agentclaw.community.core.devices.models import DeviceConnectionInfo


def test_device_connection_info_url_defaults_empty():
    """url 字段默认空字符串——保兼容（5 处既有构造都不传它）。"""
    info = DeviceConnectionInfo(
        type="local", target="127.0.0.1:20010", token="t", engine_type="openclaw"
    )
    assert info.url == ""


def test_device_connection_info_url_can_be_set():
    """url 字段可显式赋值（BaaS http-info 路径）。"""
    info = DeviceConnectionInfo(
        type="local",
        target="ARCA_xxx@alt:20010",
        token="t",
        engine_type="openclaw",
        url="http://10.0.0.1:20010",
    )
    assert info.url == "http://10.0.0.1:20010"
