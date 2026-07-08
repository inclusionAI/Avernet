"""LocalConnInfoBuilder 单测 — plan §Task 1.6 Step 1。

2 个 case:
1. 返回 local conn_info(type == "local")
2. v2 抛异常时包装成 ConnInfoBuildError
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.devices.services.conn_info_builders.local_builder import (
    LocalConnInfoBuilder,
)
from agentclaw.community.core.devices.services.device_context import ConnInfoBuildError


@pytest.fixture
def fake_binding():
    binding = MagicMock()
    binding.id = 42
    binding.bot_id = "bot-local-1"
    binding.device_provider = "local"
    return binding


@pytest.fixture
def fake_device_service():
    svc = MagicMock()
    svc.get_device_connection_v2.return_value = {
        "url": "http://localhost:8890/...",
        "headers": {},
        "use_proxy": False,
        "type": "local",
        "binding_id": 42,
        "engine_port": 20003,
    }
    return svc


def test_build_returns_local_conn_info(fake_binding, fake_device_service):
    builder = LocalConnInfoBuilder(device_service=fake_device_service)

    conn_info = builder.build(fake_binding, user_id="user-1")

    assert conn_info["type"] == "local"


def test_build_raises_conn_info_build_error(fake_binding, fake_device_service):
    fake_device_service.get_device_connection_v2.side_effect = Exception("local down")
    builder = LocalConnInfoBuilder(device_service=fake_device_service)

    with pytest.raises(ConnInfoBuildError):
        builder.build(fake_binding, user_id="user-1")
