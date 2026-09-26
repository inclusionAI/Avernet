"""ArcaConnInfoBuilder 单测 — plan §Task 1.4 Step 1。

3 个 case:
1. 返回 arca proxy conn_info(含 /proxypass/ url)
2. 委托给 device_service.get_device_connection_v2,binding_id / user_id 正确透传
3. v2 抛异常时包装成 ConnInfoBuildError
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.devices.services.conn_info_builders.arca_builder import (
    ArcaConnInfoBuilder,
)
from agentclaw.community.core.devices.services.device_context import ConnInfoBuildError


@pytest.fixture
def fake_binding():
    binding = MagicMock()
    binding.id = 42
    binding.bot_id = "bot-1"
    binding.device_provider = "arca"
    return binding


@pytest.fixture
def fake_device_service():
    svc = MagicMock()
    svc.get_device_connection_v2.return_value = {
        "url": "http://arca-proxy/proxypass/ARCA_x@y:1",
        "headers": {"x-proxypass-token": "tok"},
        "use_proxy": True,
        "sandbox_id": "ARCA_x@y:1",
        "target": "ARCA_x@y:1",
        "token": "tok",
        "engine_type": "openclaw",
        "type": "arca",
    }
    return svc


def test_build_returns_arca_proxy_conn_info(fake_binding, fake_device_service):
    builder = ArcaConnInfoBuilder(device_service=fake_device_service)

    conn_info = builder.build(fake_binding, user_id="user-1")

    assert "/proxypass/" in conn_info["url"]
    assert conn_info["use_proxy"] is True
    assert "sandbox_id" in conn_info


def test_build_delegates_to_v2_with_binding_id(fake_binding, fake_device_service):
    builder = ArcaConnInfoBuilder(device_service=fake_device_service)

    builder.build(fake_binding, user_id="user-1")

    fake_device_service.get_device_connection_v2.assert_called_once()
    call_kwargs = fake_device_service.get_device_connection_v2.call_args.kwargs
    assert call_kwargs.get("binding_id") == 42
    assert call_kwargs.get("user_id") == "user-1"


def test_build_raises_conn_info_build_error_on_v2_failure(
    fake_binding, fake_device_service
):
    fake_device_service.get_device_connection_v2.side_effect = Exception("arca down")
    builder = ArcaConnInfoBuilder(device_service=fake_device_service)

    with pytest.raises(ConnInfoBuildError):
        builder.build(fake_binding, user_id="user-1")
