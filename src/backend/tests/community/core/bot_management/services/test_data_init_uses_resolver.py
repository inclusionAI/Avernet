"""Step 2.3 — DataInitService._get_engine_connection 走 DeviceContextResolver。

旧路径:
    data_init._get_engine_connection(binding_id, owner_id, nick_name)
        → device_service.get_device_connection_v2(binding_id, owner_id, ...)

新路径(本 task 落地):
    data_init._get_engine_connection(bot_id, owner_id)
        → resolver.resolve_for_bot(bot_id, owner_id).conn_info

测试只断言 *接线*: resolver.resolve_for_bot 被调用一次,
device_service.get_device_connection_v2 不再被调用。
"""
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_management.services.data_init_service import DataInitService


@pytest.fixture
def device_service_mock() -> MagicMock:
    """旧路径(get_device_connection_v2)的 mock 接收者。"""
    return MagicMock()


@pytest.fixture
def resolver_mock() -> MagicMock:
    """新路径(resolver.resolve_for_bot)的 mock 接收者。"""
    return MagicMock()


@pytest.fixture
def data_init_service(device_service_mock, resolver_mock) -> DataInitService:
    return DataInitService(
        resource_repo=MagicMock(),
        device_service=device_service_mock,
        skill_set_factory=MagicMock(),
        skill_set_activator_factory=MagicMock(),
        device_plugin=MagicMock(),
        bot_service_provider=lambda: MagicMock(),
        skill_md_path="/test/SKILL.md",
        resolver=resolver_mock,
    )


def test_get_engine_connection_calls_resolver_not_v2(
    data_init_service: DataInitService,
    device_service_mock: MagicMock,
    resolver_mock: MagicMock,
):
    """走 resolver,不再调 v2。

    新签名: _get_engine_connection(bot_id, owner_id) — binding_id / nick_name 入参移除。
    """
    resolver_mock.resolve_for_bot.return_value = MagicMock(
        provider="baas",
        conn_info={
            "url": "http://test",
            "headers": {},
            "use_proxy": True,
            "sandbox_id": None,
            "target": "x",
        },
        binding_id=42,
        bot_id="bot-1",
        user_id="owner-1",
    )

    conn_info = data_init_service._get_engine_connection(
        bot_id="bot-1", owner_id="owner-1"
    )

    device_service_mock.get_device_connection_v2.assert_not_called()
    resolver_mock.resolve_for_bot.assert_called_once_with("bot-1", "owner-1")
    assert conn_info == {
        "url": "http://test",
        "headers": {},
        "use_proxy": True,
        "sandbox_id": None,
        "target": "x",
    }
