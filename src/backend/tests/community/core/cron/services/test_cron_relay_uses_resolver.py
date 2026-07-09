"""Task 2.2 — CronRelayService._fetch_bot_crons 走 resolver + 删 nick_name 入参.

改造前签名:
    _fetch_bot_crons(bot, user_id, nick_name)
内部:走 ``device_service.get_device_connection_v2(binding_id, user_id, nick_name)``.

改造后签名(本 task 落):
    _fetch_bot_crons(bot, user_id)
内部:走 ``self._resolver.resolve_for_bot(bot_id, user_id)`` → DeviceContext →
``self._transport.invoke(ctx.conn_info, "GET", "/api/cron")``.

测试覆盖:
    1. resolver 路径替代 v2:不再调 device_service.get_device_connection_v2,
       只调 resolver.resolve_for_bot(bot_id, user_id).
    2. ctx.conn_info 透传到 transport.invoke 第一参数.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.cron.protocols import DeviceBindingStatus
from agentclaw.community.core.cron.errors import CronRelayError
from agentclaw.community.core.cron.services.cron_relay import CronRelayService
from agentclaw.community.core.devices.services.device_context import DeviceContext
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.service_bot.repository.models import PublishStatus


def _make_service(
    *,
    bot_provider=None,
    device_provider=None,
    transport=None,
    resolver=None,
    publish_repo=None,
):
    """构造 CronRelayService — 注入 resolver。"""
    return CronRelayService(
        bot_provider=bot_provider or MagicMock(),
        device_provider=device_provider or MagicMock(),
        transport=transport or MagicMock(),
        resolver=resolver or MagicMock(),
        template_repo=MagicMock(),
        publish_repo=publish_repo or MagicMock(),
    )


def _make_ctx(
    provider="baas",
    binding_id=42,
    bot_id="bot-1",
    user_id="user-1",
    device_uuid: str | None = None,
):
    conn_info = {
        "url": "http://test",
        "headers": {},
        "use_proxy": True,
        "sandbox_id": None,
        "target": "x",
        "binding_id": binding_id,
    }
    if device_uuid:
        conn_info["device_uuid"] = device_uuid
    return DeviceContext(
        provider=provider,
        conn_info=conn_info,
        binding_id=binding_id,
        bot_id=bot_id,
        user_id=user_id,
    )


def _make_publish_record(*, publish_id: int, status: str, binding_key: str, binding_id: int):
    return SimpleNamespace(
        id=publish_id,
        status=status,
        ext={"binding": {binding_key: binding_id}},
    )


def _make_binding(*, binding_id: int, device_provider: str):
    return SimpleNamespace(id=binding_id, device_provider=device_provider)


def _make_device(
    *,
    status: str = DeviceBindingStatus.ACTIVE,
    device_provider: str = "arca",
):
    return SimpleNamespace(status=status, device_provider=device_provider)


def _make_instance(
    device_uuid: str,
    *,
    provider_device_id: str | None = None,
    bot_uuid: str | None = None,
    status: str = "ACTIVE",
    health: str = "true",
    health_status: str = "ACTIVE",
):
    instance = {
        "device_uuid": device_uuid,
        "status": status,
        "health": health,
        "provider_device_id": provider_device_id or f"provider-{device_uuid}",
        "health_status": health_status,
    }
    if bot_uuid:
        instance["bot_uuid"] = bot_uuid
    return instance


class TestFetchBotCronsUsesResolver:
    @pytest.mark.asyncio
    async def test_fetch_bot_crons_calls_resolver_not_v2(self):
        """改造后:只调 resolver.resolve_for_bot(bot_id, user_id),
        不再调 device_service.get_device_connection_v2.
        """
        # device_provider.get_device 返回 ACTIVE,使前置健康检查通过
        device_provider = MagicMock()
        device_provider.get_device.return_value = MagicMock(
            status=DeviceBindingStatus.ACTIVE
        )

        resolver = MagicMock()
        ctx = _make_ctx()
        resolver.resolve_for_bot.return_value = ctx

        transport = MagicMock()
        transport.invoke = AsyncMock(return_value={"success": True, "data": []})

        svc = _make_service(
            device_provider=device_provider,
            transport=transport,
            resolver=resolver,
        )

        bot = {"bot_id": "bot-1", "owner_id": "user-1", "binding_id": 42}
        result = await svc._fetch_bot_crons(bot, "user-1")

        # 1. resolver 被调,入参 (bot_id, user_id)
        resolver.resolve_for_bot.assert_called_once_with("bot-1", "user-1")
        # 2. v2 完全不被触达
        device_provider.get_device_connection_v2.assert_not_called()
        # 3. ctx.conn_info 透传到 transport.invoke
        transport.invoke.assert_awaited_once_with(
            ctx.conn_info, "GET", "/api/cron"
        )
        # 4. 调用结果透传
        assert result == {"success": True, "data": []}


class TestListAllCronsOwnerId:
    @pytest.mark.asyncio
    async def test_list_all_crons_adds_owner_id_to_each_task(self):
        """Cron rows can share bot_id=default across service bot owners, so the
        aggregated API must surface owner_id for frontend ownership checks.
        """
        bot_provider = MagicMock()
        bot_list = {
            "items": [
                {
                    "bot_id": "default",
                    "owner_id": "owner-1",
                    "binding_id": 42,
                    "bot_name": "Service Bot",
                }
            ]
        }
        bot_provider.list_bots_by_owner.return_value = bot_list
        bot_provider.list_bots_by_owner_or_collaborator.return_value = bot_list

        device_provider = MagicMock()
        device_provider.get_device.return_value = MagicMock(
            status=DeviceBindingStatus.ACTIVE
        )

        resolver = MagicMock()
        ctx = _make_ctx(bot_id="default", user_id="owner-1")
        resolver.resolve_for_bot.return_value = ctx

        transport = MagicMock()
        transport.invoke = AsyncMock(return_value={
            "success": True,
            "data": [{"id": "task-1", "name": "daily"}],
        })

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            transport=transport,
            resolver=resolver,
        )

        result = await svc.list_all_crons(
            user_id="owner-1", nick_name="Owner", bot_id="all"
        )

        assert result["data"][0]["bot_id"] == "default"
        assert result["data"][0]["owner_id"] == "owner-1"

    @pytest.mark.asyncio
    async def test_list_all_crons_all_scope_includes_collaborator_bots(self):
        """bot_id=all uses the same visible-bot scope as the bot list:
        owned bots plus bots where the current user is a collaborator.
        """
        bot_provider = MagicMock()
        bot_provider.list_bots_by_owner.return_value = {
            "items": [
                {
                    "bot_id": "owned-bot",
                    "owner_id": "viewer-1",
                    "binding_id": 42,
                    "bot_name": "Owned Bot",
                }
            ]
        }
        bot_provider.list_bots_by_owner_or_collaborator.return_value = {
            "items": [
                {
                    "bot_id": "owned-bot",
                    "owner_id": "viewer-1",
                    "binding_id": 42,
                    "bot_name": "Owned Bot",
                },
                {
                    "bot_id": "collab-bot",
                    "owner_id": "owner-2",
                    "binding_id": 84,
                    "bot_name": "Collaborator Bot",
                },
            ]
        }

        device_provider = MagicMock()
        device_provider.get_device.return_value = MagicMock(
            status=DeviceBindingStatus.ACTIVE
        )

        resolver = MagicMock()
        resolver.resolve_for_bot.side_effect = (
            lambda bot_id, user_id: _make_ctx(
                binding_id=42 if bot_id == "owned-bot" else 84,
                bot_id=bot_id,
                user_id=user_id,
            )
        )

        async def invoke(conn_info, method, path, body=None, params=None):
            binding_id = conn_info["binding_id"]
            return {
                "success": True,
                "data": [{"id": f"task-{binding_id}", "name": f"cron-{binding_id}"}],
            }

        transport = MagicMock()
        transport.invoke = AsyncMock(side_effect=invoke)

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            transport=transport,
            resolver=resolver,
        )

        result = await svc.list_all_crons(
            user_id="viewer-1", nick_name="Viewer", bot_id="all"
        )

        rows_by_bot = {row["bot_id"]: row for row in result["data"]}
        assert set(rows_by_bot) == {"owned-bot", "collab-bot"}
        assert rows_by_bot["collab-bot"]["owner_id"] == "owner-2"
        resolver.resolve_for_bot.assert_any_call("owned-bot", "viewer-1")
        resolver.resolve_for_bot.assert_any_call("collab-bot", "owner-2")
        bot_provider.list_bots_by_owner_or_collaborator.assert_called_once_with(
            "viewer-1", page=1, page_size=100
        )


class TestListServiceBotRuntimeStages:
    @pytest.mark.asyncio
    async def test_service_bot_list_expands_draft_verify_online_targets(self):
        bot_provider = MagicMock()
        bot_list = {
            "items": [
                {
                    "bot_id": "service-bot",
                    "owner_id": "owner-1",
                    "binding_id": 10,
                    "bot_name": "Service Bot",
                    "bot_type": "service",
                }
            ]
        }
        bot_provider.list_bots_by_owner.return_value = bot_list
        bot_provider.list_bots_by_owner_or_collaborator.return_value = bot_list

        publish_repo = MagicMock()
        publish_repo.get_latest_by_source_bot_id_and_owner_and_status.side_effect = (
            lambda source_bot_id, owner_id, status, env: {
                PublishStatus.VALIDATING.value: _make_publish_record(
                    publish_id=101,
                    status=PublishStatus.VALIDATING.value,
                    binding_key="verify",
                    binding_id=20,
                ),
                PublishStatus.SUCCESS.value: _make_publish_record(
                    publish_id=202,
                    status=PublishStatus.SUCCESS.value,
                    binding_key="online",
                    binding_id=30,
                ),
            }.get(status)
        )

        device_provider = MagicMock()
        device_provider.get_device.return_value = MagicMock(status=DeviceBindingStatus.ACTIVE)

        resolver = MagicMock()
        resolver.resolve_for_bot.return_value = _make_ctx(
            binding_id=10, bot_id="service-bot", user_id="owner-1"
        )
        resolver.resolve_for_binding.side_effect = (
            lambda binding_id, operator_id, *, bot_id: _make_ctx(
                binding_id=binding_id, bot_id=bot_id, user_id=operator_id
            )
        )

        async def invoke(conn_info, method, path, body=None, params=None):
            binding_id = conn_info["binding_id"]
            return {
                "success": True,
                "data": [{"id": f"task-{binding_id}", "name": f"cron-{binding_id}"}],
            }

        transport = MagicMock()
        transport.invoke = AsyncMock(side_effect=invoke)

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            resolver=resolver,
            transport=transport,
            publish_repo=publish_repo,
        )

        result = await svc.list_all_crons(
            user_id="owner-1", nick_name="Owner", bot_id="all"
        )

        assert result["success"] is True
        assert result["failed_targets"] == []
        by_stage = {item["runtime_stage"]: item for item in result["data"]}
        assert by_stage["draft"]["id"] == "task-10"
        assert by_stage["verify"]["id"] == "task-20"
        assert by_stage["verify"]["publish_id"] == 101
        assert by_stage["online"]["id"] == "task-30"
        assert by_stage["online"]["publish_id"] == 202
        resolver.resolve_for_bot.assert_called_once_with("service-bot", "owner-1")
        resolver.resolve_for_binding.assert_any_call(20, "owner-1", bot_id="service-bot")
        resolver.resolve_for_binding.assert_any_call(30, "owner-1", bot_id="service-bot")

    @pytest.mark.asyncio
    async def test_service_bot_online_list_expands_each_runtime_instance(self):
        bot_provider = MagicMock()
        bot_list = {
            "items": [
                {
                    "bot_id": "service-bot",
                    "owner_id": "owner-1",
                    "binding_id": 10,
                    "bot_name": "Service Bot",
                    "bot_type": "service",
                }
            ]
        }
        bot_provider.list_bots_by_owner_or_collaborator.return_value = bot_list

        publish_repo = MagicMock()
        publish_repo.get_latest_by_source_bot_id_and_owner_and_status.side_effect = (
            lambda source_bot_id, owner_id, status, env: {
                PublishStatus.SUCCESS.value: _make_publish_record(
                    publish_id=202,
                    status=PublishStatus.SUCCESS.value,
                    binding_key="online",
                    binding_id=30,
                ),
            }.get(status)
        )

        device_provider = MagicMock()
        device_provider.get_device.side_effect = (
            lambda *, binding_id: {
                10: _make_device(device_provider="arca"),
                30: _make_device(device_provider="baas"),
            }[binding_id]
        )
        device_provider.get_instances.return_value = {
            "bot_uuid": "bot-uuid-online",
            "devices": [
                _make_instance("DEVICE-001", provider_device_id="PDS-001"),
                _make_instance("DEVICE-002", provider_device_id="PDS-002"),
            ],
        }

        resolver = MagicMock()
        resolver.resolve_for_bot.return_value = _make_ctx(
            binding_id=10, bot_id="service-bot", user_id="owner-1"
        )
        resolver.resolve_for_binding.side_effect = (
            lambda binding_id, operator_id, *, bot_id, device_uuid=None: _make_ctx(
                binding_id=binding_id,
                bot_id=bot_id,
                user_id=operator_id,
                device_uuid=device_uuid,
            )
        )

        async def invoke(conn_info, method, path, body=None, params=None):
            if conn_info["binding_id"] == 30:
                return {
                    "success": True,
                    "data": [{"id": "shared-task", "name": "online cron"}],
                }
            return {
                "success": True,
                "data": [{"id": "draft-task", "name": "draft cron"}],
            }

        transport = MagicMock()
        transport.invoke = AsyncMock(side_effect=invoke)

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            resolver=resolver,
            transport=transport,
            publish_repo=publish_repo,
        )

        result = await svc.list_all_crons(
            user_id="owner-1", nick_name="Owner", bot_id="all"
        )

        online_rows = [
            item for item in result["data"]
            if item["runtime_stage"] == "online"
        ]
        assert len(online_rows) == 2
        assert {item["device_uuid"] for item in online_rows} == {
            "DEVICE-001",
            "DEVICE-002",
        }
        assert {item["id"] for item in online_rows} == {"shared-task"}
        assert {item["provider_device_id"] for item in online_rows} == {
            "PDS-001",
            "PDS-002",
        }
        assert {item["bot_uuid"] for item in online_rows} == {"bot-uuid-online"}
        assert {item["instance_health_status"] for item in online_rows} == {"ACTIVE"}
        assert result["failed_targets"] == []
        resolver.resolve_for_binding.assert_any_call(
            30, "owner-1", bot_id="service-bot", device_uuid="DEVICE-001"
        )
        resolver.resolve_for_binding.assert_any_call(
            30, "owner-1", bot_id="service-bot", device_uuid="DEVICE-002"
        )

    @pytest.mark.asyncio
    async def test_service_bot_instance_query_failure_is_recorded_not_raised(self):
        bot_provider = MagicMock()
        bot_provider.get_bot.return_value = {
            "bot_id": "service-bot",
            "owner_id": "owner-1",
            "binding_id": 10,
            "bot_name": "Service Bot",
            "bot_type": "service",
        }

        publish_repo = MagicMock()
        publish_repo.get_latest_by_source_bot_id_and_owner_and_status.side_effect = (
            lambda source_bot_id, owner_id, status, env: {
                PublishStatus.SUCCESS.value: _make_publish_record(
                    publish_id=202,
                    status=PublishStatus.SUCCESS.value,
                    binding_key="online",
                    binding_id=30,
                ),
            }.get(status)
        )

        device_provider = MagicMock()
        device_provider.get_device.side_effect = (
            lambda *, binding_id: {
                10: _make_device(device_provider="arca"),
                30: _make_device(device_provider="baas"),
            }[binding_id]
        )
        device_provider.get_instances.side_effect = RuntimeError("baas timeout")

        resolver = MagicMock()
        resolver.resolve_for_bot.return_value = _make_ctx(
            binding_id=10, bot_id="service-bot", user_id="owner-1"
        )

        transport = MagicMock()
        transport.invoke = AsyncMock(return_value={
            "success": True,
            "data": [{"id": "draft-task"}],
        })

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            resolver=resolver,
            transport=transport,
            publish_repo=publish_repo,
        )

        result = await svc.list_all_crons(
            user_id="owner-1", nick_name="Owner", bot_id="service-bot"
        )

        assert result["success"] is True
        assert [item["id"] for item in result["data"]] == ["draft-task"]
        assert result["failed_targets"] == [
            {
                "bot_id": "service-bot",
                "bot_name": "Service Bot",
                "owner_id": "owner-1",
                "runtime_stage": "online",
                "publish_id": 202,
                "reason": "instances_query_failed",
                "message": "baas timeout",
            }
        ]

    @pytest.mark.asyncio
    async def test_service_bot_list_records_failed_runtime_without_failing_result(self):
        bot_provider = MagicMock()
        bot_list = {
            "items": [
                {
                    "bot_id": "service-bot",
                    "owner_id": "owner-1",
                    "binding_id": 10,
                    "bot_name": "Service Bot",
                    "bot_type": "service",
                }
            ]
        }
        bot_provider.list_bots_by_owner.return_value = bot_list
        bot_provider.list_bots_by_owner_or_collaborator.return_value = bot_list

        publish_repo = MagicMock()
        publish_repo.get_latest_by_source_bot_id_and_owner_and_status.side_effect = (
            lambda source_bot_id, owner_id, status, env: {
                PublishStatus.VALIDATING.value: _make_publish_record(
                    publish_id=101,
                    status=PublishStatus.VALIDATING.value,
                    binding_key="verify",
                    binding_id=20,
                ),
                PublishStatus.SUCCESS.value: _make_publish_record(
                    publish_id=202,
                    status=PublishStatus.SUCCESS.value,
                    binding_key="online",
                    binding_id=30,
                ),
            }.get(status)
        )

        device_provider = MagicMock()
        device_provider.get_device.return_value = MagicMock(status=DeviceBindingStatus.ACTIVE)

        resolver = MagicMock()
        resolver.resolve_for_bot.return_value = _make_ctx(
            binding_id=10, bot_id="service-bot", user_id="owner-1"
        )
        resolver.resolve_for_binding.side_effect = (
            lambda binding_id, operator_id, *, bot_id: _make_ctx(
                binding_id=binding_id, bot_id=bot_id, user_id=operator_id
            )
        )

        async def invoke(conn_info, method, path, body=None, params=None):
            binding_id = conn_info["binding_id"]
            if binding_id == 20:
                raise TimeoutError("verify timeout")
            return {
                "success": True,
                "data": [{"id": f"task-{binding_id}", "name": f"cron-{binding_id}"}],
            }

        transport = MagicMock()
        transport.invoke = AsyncMock(side_effect=invoke)

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            resolver=resolver,
            transport=transport,
            publish_repo=publish_repo,
        )

        result = await svc.list_all_crons(
            user_id="owner-1", nick_name="Owner", bot_id="all"
        )

        assert result["success"] is True
        assert {item["runtime_stage"] for item in result["data"]} == {
            "draft",
            "online",
        }
        assert result["failed_targets"] == [
            {
                "bot_id": "service-bot",
                "bot_name": "Service Bot",
                "owner_id": "owner-1",
                "runtime_stage": "verify",
                "publish_id": 101,
                "reason": "cron_api_failed",
                "message": "verify timeout",
            }
        ]

    @pytest.mark.asyncio
    async def test_service_bot_list_records_all_runtime_failures_for_single_bot(self):
        bot_provider = MagicMock()
        bot_provider.get_bot.return_value = {
            "bot_id": "service-bot",
            "owner_id": "owner-1",
            "binding_id": 10,
            "bot_name": "Service Bot",
            "bot_type": "service",
        }

        publish_repo = MagicMock()
        publish_repo.get_latest_by_source_bot_id_and_owner_and_status.side_effect = (
            lambda source_bot_id, owner_id, status, env: {
                PublishStatus.VALIDATING.value: _make_publish_record(
                    publish_id=101,
                    status=PublishStatus.VALIDATING.value,
                    binding_key="verify",
                    binding_id=20,
                ),
                PublishStatus.SUCCESS.value: _make_publish_record(
                    publish_id=202,
                    status=PublishStatus.SUCCESS.value,
                    binding_key="online",
                    binding_id=30,
                ),
            }.get(status)
        )

        device_provider = MagicMock()
        device_provider.get_device.side_effect = RuntimeError("device unavailable")

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            publish_repo=publish_repo,
        )

        result = await svc.list_all_crons(
            user_id="owner-1", nick_name="Owner", bot_id="service-bot"
        )

        assert result["success"] is True
        assert result["data"] == []
        assert {item["runtime_stage"] for item in result["failed_targets"]} == {
            "draft",
            "verify",
            "online",
        }
        assert {item["reason"] for item in result["failed_targets"]} == {
            "device_unavailable"
        }


class TestListRunningCronsOwnerId:
    @pytest.mark.asyncio
    async def test_list_running_crons_adds_owner_id_for_specific_bot(self):
        bot_provider = MagicMock()
        bot_provider.get_bot.return_value = {
            "bot_id": "default",
            "owner_id": "owner-1",
            "binding_id": 42,
            "bot_name": "Service Bot",
        }

        device_provider = MagicMock()
        device_provider.get_device.return_value = MagicMock(
            status=DeviceBindingStatus.ACTIVE
        )

        resolver = MagicMock()
        ctx = _make_ctx(bot_id="default", user_id="owner-1")
        resolver.resolve_for_bot.return_value = ctx

        transport = MagicMock()
        transport.invoke = AsyncMock(return_value={
            "success": True,
            "data": {"running": [{"id": "task-1", "name": "daily"}]},
        })

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            transport=transport,
            resolver=resolver,
        )

        result = await svc.list_running_crons(
            user_id="owner-1", nick_name="Owner", bot_id="default"
        )

        assert result["data"][0]["bot_id"] == "default"
        assert result["data"][0]["bot_name"] == "Service Bot"
        assert result["data"][0]["owner_id"] == "owner-1"

    @pytest.mark.asyncio
    async def test_list_running_crons_uses_initial_bot_metadata_without_second_lookup(self):
        bot_provider = MagicMock()
        bot_provider.get_bot.side_effect = [
            {
                "bot_id": "default",
                "owner_id": "owner-1",
                "binding_id": 42,
                "bot_name": "Service Bot",
            },
            Exception("owner lookup failed"),
        ]

        device_provider = MagicMock()
        device_provider.get_device.return_value = MagicMock(
            status=DeviceBindingStatus.ACTIVE
        )

        resolver = MagicMock()
        ctx = _make_ctx(bot_id="default", user_id="owner-1")
        resolver.resolve_for_bot.return_value = ctx

        transport = MagicMock()
        transport.invoke = AsyncMock(return_value={
            "success": True,
            "data": {"running": [{"id": "task-1", "name": "daily"}]},
        })

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            transport=transport,
            resolver=resolver,
        )

        result = await svc.list_running_crons(
            user_id="owner-1", nick_name="Owner", bot_id="default"
        )

        assert result["data"][0]["bot_id"] == "default"
        assert result["data"][0]["bot_name"] == "Service Bot"
        assert result["data"][0]["owner_id"] == "owner-1"

    @pytest.mark.asyncio
    async def test_list_running_crons_adds_owner_id_for_all_bots(self):
        bot_provider = MagicMock()
        bot_list = {
            "items": [
                {
                    "bot_id": "default",
                    "owner_id": "owner-1",
                    "binding_id": 42,
                    "bot_name": "Service Bot",
                }
            ]
        }
        bot_provider.list_bots_by_owner.return_value = bot_list
        bot_provider.list_bots_by_owner_or_collaborator.return_value = bot_list
        bot_provider.get_bot.return_value = {
            "bot_id": "default",
            "owner_id": "owner-1",
            "binding_id": 42,
            "bot_name": "Service Bot",
        }

        device_provider = MagicMock()
        device_provider.get_device.return_value = MagicMock(
            status=DeviceBindingStatus.ACTIVE
        )

        resolver = MagicMock()
        ctx = _make_ctx(bot_id="default", user_id="owner-1")
        resolver.resolve_for_bot.return_value = ctx

        transport = MagicMock()
        transport.invoke = AsyncMock(return_value={
            "success": True,
            "data": {"running": [{"id": "task-1", "name": "daily"}]},
        })

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            transport=transport,
            resolver=resolver,
        )

        result = await svc.list_running_crons(
            user_id="owner-1", nick_name="Owner", bot_id="all"
        )

        assert result["data"][0]["bot_id"] == "default"
        assert result["data"][0]["bot_name"] == "Service Bot"
        assert result["data"][0]["owner_id"] == "owner-1"

    @pytest.mark.asyncio
    async def test_list_running_crons_all_scope_includes_collaborator_bots(self):
        bot_provider = MagicMock()
        bot_provider.list_bots_by_owner.return_value = {
            "items": [
                {
                    "bot_id": "owned-bot",
                    "owner_id": "viewer-1",
                    "binding_id": 42,
                    "bot_name": "Owned Bot",
                }
            ]
        }
        bot_provider.list_bots_by_owner_or_collaborator.return_value = {
            "items": [
                {
                    "bot_id": "owned-bot",
                    "owner_id": "viewer-1",
                    "binding_id": 42,
                    "bot_name": "Owned Bot",
                },
                {
                    "bot_id": "collab-bot",
                    "owner_id": "owner-2",
                    "binding_id": 84,
                    "bot_name": "Collaborator Bot",
                },
            ]
        }

        device_provider = MagicMock()
        device_provider.get_device.return_value = MagicMock(
            status=DeviceBindingStatus.ACTIVE
        )

        resolver = MagicMock()
        resolver.resolve_for_bot.side_effect = (
            lambda bot_id, user_id: _make_ctx(
                binding_id=42 if bot_id == "owned-bot" else 84,
                bot_id=bot_id,
                user_id=user_id,
            )
        )

        async def invoke(conn_info, method, path, body=None, params=None):
            binding_id = conn_info["binding_id"]
            return {
                "success": True,
                "data": {"running": [{"id": f"task-{binding_id}"}]},
            }

        transport = MagicMock()
        transport.invoke = AsyncMock(side_effect=invoke)

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            transport=transport,
            resolver=resolver,
        )

        result = await svc.list_running_crons(
            user_id="viewer-1", nick_name="Viewer", bot_id="all"
        )

        rows_by_bot = {row["bot_id"]: row for row in result["data"]}
        assert set(rows_by_bot) == {"owned-bot", "collab-bot"}
        assert rows_by_bot["collab-bot"]["owner_id"] == "owner-2"
        resolver.resolve_for_bot.assert_any_call("owned-bot", "viewer-1")
        resolver.resolve_for_bot.assert_any_call("collab-bot", "owner-2")
        bot_provider.list_bots_by_owner_or_collaborator.assert_called_once_with(
            "viewer-1", page=1, page_size=100
        )

    @pytest.mark.asyncio
    async def test_list_running_crons_expands_service_bot_runtime_instances(self):
        bot_provider = MagicMock()
        bot_provider.get_bot.return_value = {
            "bot_id": "service-bot",
            "owner_id": "owner-1",
            "binding_id": 10,
            "bot_name": "Service Bot",
            "bot_type": "service",
        }

        publish_repo = MagicMock()
        publish_repo.get_latest_by_source_bot_id_and_owner_and_status.side_effect = (
            lambda source_bot_id, owner_id, status, env: {
                PublishStatus.SUCCESS.value: _make_publish_record(
                    publish_id=202,
                    status=PublishStatus.SUCCESS.value,
                    binding_key="online",
                    binding_id=30,
                ),
            }.get(status)
        )

        device_provider = MagicMock()
        device_provider.get_device.side_effect = (
            lambda *, binding_id: {
                10: _make_device(device_provider="arca"),
                30: _make_device(device_provider="baas"),
            }[binding_id]
        )
        device_provider.get_instances.return_value = {
            "bot_uuid": "bot-uuid-online",
            "devices": [
                _make_instance("DEVICE-001", provider_device_id="PDS-001"),
                _make_instance("DEVICE-002", provider_device_id="PDS-002"),
            ],
        }

        resolver = MagicMock()
        resolver.resolve_for_bot.return_value = _make_ctx(
            binding_id=10, bot_id="service-bot", user_id="owner-1"
        )
        resolver.resolve_for_binding.side_effect = (
            lambda binding_id, operator_id, *, bot_id, device_uuid=None: _make_ctx(
                binding_id=binding_id,
                bot_id=bot_id,
                user_id=operator_id,
                device_uuid=device_uuid,
            )
        )

        async def invoke(conn_info, method, path, body=None, params=None):
            if conn_info["binding_id"] == 30:
                return {
                    "success": True,
                    "data": {"running": [{"id": "shared-run", "task_id": "task-o"}]},
                }
            return {"success": True, "data": {"running": []}}

        transport = MagicMock()
        transport.invoke = AsyncMock(side_effect=invoke)

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            resolver=resolver,
            transport=transport,
            publish_repo=publish_repo,
        )

        result = await svc.list_running_crons(
            user_id="owner-1", nick_name="Owner", bot_id="service-bot"
        )

        assert len(result["data"]) == 2
        assert {item["device_uuid"] for item in result["data"]} == {
            "DEVICE-001",
            "DEVICE-002",
        }
        assert {item["provider_device_id"] for item in result["data"]} == {
            "PDS-001",
            "PDS-002",
        }
        assert {item["bot_uuid"] for item in result["data"]} == {"bot-uuid-online"}
        assert {item["instance_health_status"] for item in result["data"]} == {
            "ACTIVE"
        }
        assert result["failed_targets"] == []


class TestForwardRequestUsesResolver:
    @pytest.mark.asyncio
    async def test_forward_request_calls_resolver_not_v2(self):
        """forward_request(create/update/delete cron)走 resolver,不再调 v2 —
        否则 baas service bot 落 v2 direct 分支丢 binding_id → fallback 裸 httpx 直发
        ARCA-SANDBOX 内网 target → 500。走 resolver 才永填 binding_id → proxypass。
        """
        bot_provider = MagicMock()
        bot_provider.get_bot.return_value = {
            "bot_id": "bot-1", "owner_id": "user-1", "binding_id": 42,
            "bot_name": "测试",
        }

        device_provider = MagicMock()
        device_provider.get_device.return_value = MagicMock(
            status=DeviceBindingStatus.ACTIVE
        )

        resolver = MagicMock()
        ctx = _make_ctx()
        resolver.resolve_for_bot.return_value = ctx

        transport = MagicMock()
        transport.invoke = AsyncMock(return_value={"success": True, "data": {}})

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            transport=transport,
            resolver=resolver,
        )

        await svc.forward_request(
            bot_id="bot-1", user_id="user-1", nick_name="测试",
            method="POST", path="/api/cron", body={"name": "x"},
        )

        resolver.resolve_for_bot.assert_called_once_with("bot-1", "user-1")
        device_provider.get_device_connection_v2.assert_not_called()
        # conn_info(含 binding_id)透传到 transport,transport 才走 baas proxypass
        sent_conn_info = transport.invoke.await_args.args[0]
        assert sent_conn_info["binding_id"] == 42

    @pytest.mark.asyncio
    async def test_forward_request_adds_owner_id_to_dict_data(self):
        """Single-task/detail responses must include owner_id alongside bot_id."""
        bot_provider = MagicMock()
        bot_provider.get_bot.return_value = {
            "bot_id": "default",
            "owner_id": "owner-1",
            "binding_id": 42,
            "bot_name": "Service Bot",
        }

        device_provider = MagicMock()
        device_provider.get_device.return_value = MagicMock(
            status=DeviceBindingStatus.ACTIVE
        )

        resolver = MagicMock()
        ctx = _make_ctx(bot_id="default", user_id="owner-1")
        resolver.resolve_for_bot.return_value = ctx

        transport = MagicMock()
        transport.invoke = AsyncMock(return_value={
            "success": True,
            "data": {"id": "task-1", "name": "daily"},
        })

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            transport=transport,
            resolver=resolver,
        )

        result = await svc.forward_request(
            bot_id="default",
            user_id="owner-1",
            nick_name="Owner",
            method="GET",
            path="/api/cron/task-1",
        )

        assert result["data"]["bot_id"] == "default"
        assert result["data"]["owner_id"] == "owner-1"

    @pytest.mark.asyncio
    async def test_forward_request_routes_verify_stage_by_publish_binding_and_keeps_task_id(self):
        bot_provider = MagicMock()
        bot_provider.get_bot.return_value = {
            "bot_id": "service-bot",
            "owner_id": "owner-1",
            "binding_id": 10,
            "bot_name": "Service Bot",
            "bot_type": "service",
        }

        publish_repo = MagicMock()
        publish_repo.get_latest_by_source_bot_id_and_owner_and_status.return_value = (
            _make_publish_record(
                publish_id=101,
                status=PublishStatus.VALIDATING.value,
                binding_key="verify",
                binding_id=20,
            )
        )

        device_provider = MagicMock()
        device_provider.get_device.return_value = MagicMock(status=DeviceBindingStatus.ACTIVE)

        resolver = MagicMock()
        resolver.resolve_for_binding.return_value = _make_ctx(
            binding_id=20, bot_id="service-bot", user_id="owner-1"
        )

        transport = MagicMock()
        transport.invoke = AsyncMock(return_value={"success": True, "data": {"id": "task-v"}})

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            resolver=resolver,
            transport=transport,
            publish_repo=publish_repo,
        )

        result = await svc.forward_request(
            bot_id="service-bot",
            user_id="owner-1",
            nick_name="Owner",
            method="POST",
            path="/api/cron/task-v/run",
            runtime_stage="verify",
        )

        assert result["success"] is True
        resolver.resolve_for_bot.assert_not_called()
        resolver.resolve_for_binding.assert_called_once_with(
            20, "owner-1", bot_id="service-bot"
        )
        transport.invoke.assert_awaited_once()
        assert transport.invoke.await_args.args[2] == "/api/cron/task-v/run"

    @pytest.mark.asyncio
    async def test_forward_request_allows_enabled_toggle_for_published_stage(self):
        bot_provider = MagicMock()
        bot_provider.get_bot.return_value = {
            "bot_id": "service-bot",
            "owner_id": "owner-1",
            "binding_id": 10,
            "bot_name": "Service Bot",
            "bot_type": "service",
        }

        publish_repo = MagicMock()
        publish_repo.get_latest_by_source_bot_id_and_owner_and_status.return_value = (
            _make_publish_record(
                publish_id=202,
                status=PublishStatus.SUCCESS.value,
                binding_key="online",
                binding_id=30,
            )
        )

        device_provider = MagicMock()
        device_provider.get_device.return_value = MagicMock(status=DeviceBindingStatus.ACTIVE)

        resolver = MagicMock()
        resolver.resolve_for_binding.return_value = _make_ctx(
            binding_id=30, bot_id="service-bot", user_id="owner-1"
        )

        transport = MagicMock()
        transport.invoke = AsyncMock(return_value={"success": True, "data": {"id": "task-o"}})

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            resolver=resolver,
            transport=transport,
            publish_repo=publish_repo,
        )

        result = await svc.forward_request(
            bot_id="service-bot",
            user_id="owner-1",
            nick_name="Owner",
            method="PUT",
            path="/api/cron/task-o",
            body={"enabled": False},
            runtime_stage="online",
        )

        assert result["success"] is True
        resolver.resolve_for_binding.assert_called_once_with(
            30, "owner-1", bot_id="service-bot"
        )
        transport.invoke.assert_awaited_once_with(
            resolver.resolve_for_binding.return_value.conn_info,
            "PUT",
            "/api/cron/task-o",
            {"enabled": False},
            None,
        )

    @pytest.mark.asyncio
    async def test_forward_request_fans_out_enabled_toggle_to_runtime_instances(self):
        bot_provider = MagicMock()
        bot_provider.get_bot.return_value = {
            "bot_id": "service-bot",
            "owner_id": "owner-1",
            "binding_id": 10,
            "bot_name": "Service Bot",
            "bot_type": "service",
        }

        publish_repo = MagicMock()
        publish_repo.get_latest_by_source_bot_id_and_owner_and_status.return_value = (
            _make_publish_record(
                publish_id=202,
                status=PublishStatus.SUCCESS.value,
                binding_key="online",
                binding_id=30,
            )
        )

        device_provider = MagicMock()
        device_provider.get_device.return_value = _make_device(device_provider="baas")
        device_provider.get_instances.return_value = {
            "bot_uuid": "bot-uuid-online",
            "devices": [
                _make_instance("DEVICE-001", provider_device_id="PDS-001"),
                _make_instance("DEVICE-002", provider_device_id="PDS-002"),
            ],
        }

        resolver = MagicMock()
        resolver.resolve_for_binding.side_effect = (
            lambda binding_id, operator_id, *, bot_id, device_uuid=None: _make_ctx(
                binding_id=binding_id,
                bot_id=bot_id,
                user_id=operator_id,
                device_uuid=device_uuid,
            )
        )

        async def invoke(conn_info, method, path, body=None, params=None):
            if conn_info.get("device_uuid") == "DEVICE-002":
                raise TimeoutError("DEVICE-002 timeout")
            return {"success": True, "data": {"id": "task-o", "enabled": False}}

        transport = MagicMock()
        transport.invoke = AsyncMock(side_effect=invoke)

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            resolver=resolver,
            transport=transport,
            publish_repo=publish_repo,
        )

        result = await svc.forward_request(
            bot_id="service-bot",
            user_id="owner-1",
            nick_name="Owner",
            method="PUT",
            path="/api/cron/task-o",
            body={"enabled": False},
            runtime_stage="online",
        )

        assert result["success"] is True
        assert result["data"]["succeeded"] == 1
        assert result["data"]["failed"] == 1
        assert result["data"]["results"][0]["device_uuid"] == "DEVICE-001"
        assert result["failed_targets"] == [
            {
                "bot_id": "service-bot",
                "bot_name": "Service Bot",
                "owner_id": "owner-1",
                "runtime_stage": "online",
                "publish_id": 202,
                "reason": "cron_api_failed",
                "message": "DEVICE-002 timeout",
                "device_uuid": "DEVICE-002",
                "provider_device_id": "PDS-002",
                "bot_uuid": "bot-uuid-online",
                "instance_status": "ACTIVE",
                "instance_health": "true",
                "instance_health_status": "ACTIVE",
            }
        ]
        resolver.resolve_for_binding.assert_any_call(
            30, "owner-1", bot_id="service-bot", device_uuid="DEVICE-001"
        )
        resolver.resolve_for_binding.assert_any_call(
            30, "owner-1", bot_id="service-bot", device_uuid="DEVICE-002"
        )
        assert transport.invoke.await_count == 2

    @pytest.mark.asyncio
    async def test_forward_request_fan_out_manual_run_reports_all_failed(self):
        bot_provider = MagicMock()
        bot_provider.get_bot.return_value = {
            "bot_id": "service-bot",
            "owner_id": "owner-1",
            "binding_id": 10,
            "bot_name": "Service Bot",
            "bot_type": "service",
        }

        publish_repo = MagicMock()
        publish_repo.get_latest_by_source_bot_id_and_owner_and_status.return_value = (
            _make_publish_record(
                publish_id=202,
                status=PublishStatus.SUCCESS.value,
                binding_key="online",
                binding_id=30,
            )
        )

        device_provider = MagicMock()
        device_provider.get_device.return_value = _make_device(device_provider="baas")
        device_provider.get_instances.return_value = {
            "bot_uuid": "bot-uuid-online",
            "devices": [
                _make_instance("DEVICE-001", provider_device_id="PDS-001"),
                _make_instance("DEVICE-002", provider_device_id="PDS-002"),
            ],
        }

        resolver = MagicMock()
        resolver.resolve_for_binding.side_effect = (
            lambda binding_id, operator_id, *, bot_id, device_uuid=None: _make_ctx(
                binding_id=binding_id,
                bot_id=bot_id,
                user_id=operator_id,
                device_uuid=device_uuid,
            )
        )

        transport = MagicMock()
        transport.invoke = AsyncMock(return_value={
            "success": False,
            "message": "adapter rejected",
        })

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            resolver=resolver,
            transport=transport,
            publish_repo=publish_repo,
        )

        result = await svc.forward_request(
            bot_id="service-bot",
            user_id="owner-1",
            nick_name="Owner",
            method="POST",
            path="/api/cron/task-o/run",
            params={"force": True},
            runtime_stage="online",
        )

        assert result["success"] is False
        assert result["data"]["succeeded"] == 0
        assert result["data"]["failed"] == 2
        assert [item["reason"] for item in result["failed_targets"]] == [
            "cron_api_failed",
            "cron_api_failed",
        ]
        assert transport.invoke.await_count == 2

    @pytest.mark.asyncio
    async def test_forward_request_online_stage_routes_teclaw_binding_via_real_resolver(self):
        bot_provider = MagicMock()
        bot_provider.get_bot.return_value = {
            "bot_id": "service-bot",
            "owner_id": "owner-1",
            "binding_id": 10,
            "bot_name": "Service Bot",
            "bot_type": "service",
        }

        publish_repo = MagicMock()
        publish_repo.get_latest_by_source_bot_id_and_owner_and_status.return_value = (
            _make_publish_record(
                publish_id=202,
                status=PublishStatus.SUCCESS.value,
                binding_key="online",
                binding_id=30,
            )
        )

        device_provider = MagicMock()
        device_provider.get_device.return_value = MagicMock(status=DeviceBindingStatus.ACTIVE)

        binding_repo = MagicMock()
        binding_repo.get_by_id.return_value = _make_binding(
            binding_id=30,
            device_provider="teclaw",
        )
        bot_repo = MagicMock()
        bot_repo.get_by_binding_id.return_value = {"bot_type": "service"}
        teclaw_builder = MagicMock()
        teclaw_builder.build.return_value = {
            "bind_id": 30,
            "engine_type": "teclaw",
            "target": "teclaw-runtime",
        }
        resolver = DeviceContextResolver(
            binding_repository=binding_repo,
            bot_repository=bot_repo,
            arca_builder=MagicMock(),
            baas_builder=MagicMock(),
            teclaw_builder=teclaw_builder,
            local_builder=MagicMock(),
        )

        transport = MagicMock()
        transport.invoke = AsyncMock(return_value={"success": True, "data": {"id": "task-o"}})

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            resolver=resolver,
            transport=transport,
            publish_repo=publish_repo,
        )

        await svc.forward_request(
            bot_id="service-bot",
            user_id="owner-1",
            nick_name="Owner",
            method="POST",
            path="/api/cron/task-o/run",
            runtime_stage="online",
        )

        binding_repo.get_by_id.assert_called_once_with(30)
        teclaw_builder.build.assert_called_once()
        sent_conn_info = transport.invoke.await_args.args[0]
        assert sent_conn_info["engine_type"] == "teclaw"
        assert sent_conn_info["bind_id"] == 30
        assert sent_conn_info["binding_id"] == 30
        assert transport.invoke.await_args.args[2] == "/api/cron/task-o/run"

    @pytest.mark.asyncio
    async def test_forward_request_rejects_delete_for_published_stage(self):
        bot_provider = MagicMock()
        svc = _make_service(bot_provider=bot_provider)

        with pytest.raises(CronRelayError) as exc_info:
            await svc.forward_request(
                bot_id="service-bot",
                user_id="owner-1",
                nick_name="Owner",
                method="DELETE",
                path="/api/cron/task-v",
                runtime_stage="verify",
            )

        assert exc_info.value.error_code == 403
        bot_provider.get_bot.assert_not_called()

    @pytest.mark.asyncio
    async def test_forward_request_rejects_publish_stage_edit_except_enabled(self):
        bot_provider = MagicMock()
        svc = _make_service(bot_provider=bot_provider)

        with pytest.raises(CronRelayError) as exc_info:
            await svc.forward_request(
                bot_id="service-bot",
                user_id="owner-1",
                nick_name="Owner",
                method="PUT",
                path="/api/cron/task-v",
                body={"name": "new-name"},
                runtime_stage="online",
            )

        assert exc_info.value.error_code == 403
        bot_provider.get_bot.assert_not_called()
