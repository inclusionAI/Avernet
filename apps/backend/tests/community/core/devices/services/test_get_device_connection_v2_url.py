"""Tests for DeviceService.get_device_connection_v2 url field (plan-01).

Direct-connection branch must prefer DeviceConnectionInfo.url (BaaS http-info
provided) over the legacy `f"http://{target}"` fallback. expert_chat / cron_relay
already read `conn.get("url") or f"http://{conn['target']}"` so the new url
field is end-to-end visible without caller changes.
"""
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.devices.models import DeviceConnectionInfo


def _make_service_with_direct_conn(url: str, target: str):
    """Build a minimal DeviceService stub whose get_device_connection returns
    a DeviceConnectionInfo that hits the *direct* branch in get_device_connection_v2.

    type="remote" ensures it does NOT enter the BaaS invoke-http branch
    (which matches 'desktop'|'local') and does NOT enter the ARCA proxy branch
    (no sandbox_id, no 'baas' type, target doesn't start with 'ARCA_').
    """
    from agentclaw.community.core.devices.services.device_service import DeviceService

    svc = DeviceService.__new__(DeviceService)  # 跳过 __init__
    svc.get_device = MagicMock(
        return_value=MagicMock(device_props={})  # 无 sandbox_id → 走直连分支
    )
    svc.get_device_connection = MagicMock(
        return_value=DeviceConnectionInfo(
            type="remote",
            target=target,
            token="t",
            engine_type="openclaw",
            available=True,
            url=url,
        )
    )
    return svc


def test_v2_direct_branch_uses_url_when_present():
    svc = _make_service_with_direct_conn(
        url="http://10.0.0.1:20010", target="127.0.0.1:20010"
    )
    out = svc.get_device_connection_v2(
        user_id="u", nick_name="u", binding_id=42
    )
    assert out["url"] == "http://10.0.0.1:20010"
    assert out["use_proxy"] is False


def test_v2_direct_branch_falls_back_to_target_when_url_empty():
    svc = _make_service_with_direct_conn(url="", target="127.0.0.1:20010")
    out = svc.get_device_connection_v2(
        user_id="u", nick_name="u", binding_id=42
    )
    # Fallback: `f"http://{target}"`
    assert out["url"] == "http://127.0.0.1:20010"
    assert out["use_proxy"] is False
