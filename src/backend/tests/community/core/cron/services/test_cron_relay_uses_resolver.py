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

import asyncio
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.cron.protocols import DeviceBindingStatus
from agentclaw.community.core.cron.errors import CronRelayError
from agentclaw.community.core.cron.services.cron_relay import CronRelayService
from agentclaw.community.core.cron.services import cron_runtime_targets
from agentclaw.community.core.cron.services.cron_runtime_targets import (
    CronRuntimeTarget,
)
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
    async def test_list_all_crons_skips_hermes_bots(self):
        bot_provider = MagicMock()
        bot_provider.list_bots_by_owner_or_collaborator.return_value = {
            "items": [
                {
                    "bot_id": "desktop-hermes",
                    "owner_id": "owner-1",
                    "binding_id": 42,
                    "bot_name": "Desktop Hermes",
                    "bot_type": "personal",
                    "active_engine": "hermes",
                },
                {
                    "bot_id": "service-hermes",
                    "owner_id": "owner-1",
                    "binding_id": 84,
                    "bot_name": "Service Hermes",
                    "bot_type": "service",
                    "active_engine": "HERMES",
                },
            ]
        }
        device_provider = MagicMock()
        resolver = MagicMock()
        transport = MagicMock()
        transport.invoke = AsyncMock()
        publish_repo = MagicMock()
        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            resolver=resolver,
            transport=transport,
            publish_repo=publish_repo,
        )

        result = await svc.list_all_crons(
            user_id="owner-1",
            nick_name="Owner",
            bot_id="all",
        )

        assert result == {
            "success": True,
            "data": [],
            "total": 0,
            "failed_targets": [],
        }
        device_provider.get_device.assert_not_called()
        resolver.resolve_for_bot.assert_not_called()
        resolver.resolve_for_binding.assert_not_called()
        transport.invoke.assert_not_awaited()
        publish_repo.get_latest_by_source_bot_id_and_owner_and_status.assert_not_called()

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
    async def test_list_all_crons_does_not_expose_runtime_stage_for_personal_bot(self):
        """Personal bots do not have draft/verify/online runtime stages."""
        bot_provider = MagicMock()
        bot_provider.get_bot.return_value = {
            "bot_id": "personal-bot",
            "owner_id": "owner-1",
            "binding_id": 42,
            "bot_name": "Personal Bot",
            "bot_type": "personal",
        }

        device_provider = MagicMock()
        device_provider.get_device.return_value = MagicMock(
            status=DeviceBindingStatus.ACTIVE
        )

        resolver = MagicMock()
        resolver.resolve_for_bot.return_value = _make_ctx(
            binding_id=42,
            bot_id="personal-bot",
            user_id="owner-1",
        )

        transport = MagicMock()
        transport.invoke = AsyncMock(return_value={
            "success": True,
            "data": [{"id": "task-1", "name": "daily"}],
        })

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            resolver=resolver,
            transport=transport,
        )

        result = await svc.list_all_crons(
            user_id="owner-1",
            nick_name="Owner",
            bot_id="personal-bot",
        )

        assert result["success"] is True
        assert result["data"][0]["bot_id"] == "personal-bot"
        assert "runtime_stage" not in result["data"][0]

    @pytest.mark.asyncio
    async def test_personal_bot_failure_does_not_expose_runtime_stage(self):
        bot_provider = MagicMock()
        bot_provider.get_bot.return_value = {
            "bot_id": "personal-bot",
            "owner_id": "owner-1",
            "binding_id": 42,
            "bot_name": "Personal Bot",
            "bot_type": "personal",
        }

        device_provider = MagicMock()
        device_provider.get_device.return_value = _make_device()

        resolver = MagicMock()
        resolver.resolve_for_bot.return_value = _make_ctx(
            binding_id=42,
            bot_id="personal-bot",
            user_id="owner-1",
        )

        transport = MagicMock()
        transport.invoke = AsyncMock(side_effect=ValueError("adapter unavailable"))

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            resolver=resolver,
            transport=transport,
        )
        result = await svc.list_all_crons(
            user_id="owner-1",
            nick_name="Owner",
            bot_id="personal-bot",
        )

        assert result["data"] == []
        assert result["failed_targets"][0]["reason"] == "cron_api_failed"
        assert "runtime_stage" not in result["failed_targets"][0]

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

        async def invoke(conn_info, method, path, body=None, params=None, *, timeout=None):
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
    def test_success_publish_record_supplies_verify_and_online_targets(self):
        bot = {
            "bot_id": "service-bot",
            "owner_id": "owner-1",
            "binding_id": 10,
            "bot_name": "Service Bot",
            "bot_type": "service",
        }
        success_record = SimpleNamespace(
            id=202,
            status=PublishStatus.SUCCESS.value,
            ext={"binding": {"verify": 20, "online": 30}},
        )
        publish_repo = MagicMock()
        publish_repo.get_latest_by_source_bot_id_and_owner_and_status.side_effect = (
            lambda source_bot_id, owner_id, status, env: (
                success_record if status == PublishStatus.SUCCESS.value else None
            )
        )
        svc = _make_service(publish_repo=publish_repo)

        targets, failed_targets = svc._build_runtime_targets(bot, "owner-1")

        assert failed_targets == []
        by_stage = {target.runtime_stage: target for target in targets}
        assert by_stage["draft"].binding_id == 10
        assert by_stage["verify"].binding_id == 20
        assert by_stage["verify"].publish_id == 202
        assert by_stage["verify"].publish_status == PublishStatus.SUCCESS.value
        assert by_stage["online"].binding_id == 30
        assert by_stage["online"].publish_id == 202

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
        device_provider.get_device.return_value = _make_device(device_provider="arca")

        resolver = MagicMock()
        resolver.resolve_for_bot.return_value = _make_ctx(
            binding_id=10, bot_id="service-bot", user_id="owner-1"
        )
        resolver.resolve_for_binding.side_effect = (
            lambda binding_id, operator_id, *, bot_id: _make_ctx(
                binding_id=binding_id, bot_id=bot_id, user_id=operator_id
            )
        )

        async def invoke(conn_info, method, path, body=None, params=None, *, timeout=None):
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
    async def test_service_bot_online_list_stays_at_stage_level(self):
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
                PublishStatus.SUCCESS.value: SimpleNamespace(
                    id=202,
                    status=PublishStatus.SUCCESS.value,
                    ext={"binding": {"verify": 20, "online": 30}},
                ),
            }.get(status)
        )

        device_provider = MagicMock()
        device_provider.get_device.side_effect = (
            lambda *, binding_id: {
                10: _make_device(device_provider="arca"),
                20: _make_device(device_provider="arca"),
                30: _make_device(device_provider="baas"),
            }[binding_id]
        )
        device_provider.list_devices_by_runtime_binding.return_value = [
            "DEVICE-001",
            "DEVICE-002",
        ]

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

        async def invoke(conn_info, method, path, body=None, params=None, *, timeout=None):
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
        assert len(online_rows) == 1
        assert "device_uuid" not in online_rows[0]
        assert "provider_device_id" not in online_rows[0]
        assert "bot_uuid" not in online_rows[0]
        assert "instance_health_status" not in online_rows[0]
        assert {item["id"] for item in online_rows} == {"shared-task"}
        assert result["failed_targets"] == []
        device_provider.get_instances.assert_not_called()
        device_provider.list_devices_by_runtime_binding.assert_not_called()
        resolver.resolve_for_binding.assert_any_call(
            30, "owner-1", bot_id="service-bot"
        )

    @pytest.mark.asyncio
    async def test_service_bot_list_does_not_query_instances(self):
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
                PublishStatus.SUCCESS.value: SimpleNamespace(
                    id=202,
                    status=PublishStatus.SUCCESS.value,
                    ext={"binding": {"verify": 20, "online": 30}},
                ),
            }.get(status)
        )

        device_provider = MagicMock()
        device_provider.get_device.side_effect = (
            lambda *, binding_id: {
                10: _make_device(device_provider="arca"),
                20: _make_device(device_provider="arca"),
                30: _make_device(device_provider="baas"),
            }[binding_id]
        )
        device_provider.list_devices_by_runtime_binding.side_effect = RuntimeError("baas timeout")

        resolver = MagicMock()
        resolver.resolve_for_bot.return_value = _make_ctx(
            binding_id=10, bot_id="service-bot", user_id="owner-1"
        )
        resolver.resolve_for_binding.side_effect = (
            lambda binding_id, operator_id, *, bot_id: _make_ctx(
                binding_id=binding_id,
                bot_id=bot_id,
                user_id=operator_id,
            )
        )

        async def invoke(conn_info, method, path, body=None, params=None, *, timeout=None):
            if conn_info["binding_id"] == 30:
                return {
                    "success": True,
                    "data": [{"id": "online-task"}],
                }
            if conn_info["binding_id"] == 20:
                return {
                    "success": True,
                    "data": [{"id": "verify-task"}],
                }
            return {
                "success": True,
                "data": [{"id": "draft-task"}],
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
            user_id="owner-1", nick_name="Owner", bot_id="service-bot"
        )

        assert result["success"] is True
        assert [item["id"] for item in result["data"]] == [
            "draft-task",
            "verify-task",
            "online-task",
        ]
        assert result["failed_targets"] == []
        device_provider.get_instances.assert_not_called()
        device_provider.list_devices_by_runtime_binding.assert_not_called()

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
        device_provider.get_device.return_value = _make_device(device_provider="arca")

        resolver = MagicMock()
        resolver.resolve_for_bot.return_value = _make_ctx(
            binding_id=10, bot_id="service-bot", user_id="owner-1"
        )
        resolver.resolve_for_binding.side_effect = (
            lambda binding_id, operator_id, *, bot_id: _make_ctx(
                binding_id=binding_id, bot_id=bot_id, user_id=operator_id
            )
        )

        async def invoke(conn_info, method, path, body=None, params=None, *, timeout=None):
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
                "reason": "cron_api_timeout",
                "message": "cron_api_timeout: no response within 8s for /api/cron",
            }
        ]

    @pytest.mark.asyncio
    async def test_list_all_crons_enforces_read_timeout(self, monkeypatch):
        monkeypatch.setattr(
            cron_runtime_targets,
            "CRON_READ_TIMEOUT_SECONDS",
            0.01,
        )
        bot_provider = MagicMock()
        bot_provider.get_bot.return_value = {
            "bot_id": "personal-bot",
            "owner_id": "owner-1",
            "binding_id": 42,
            "bot_name": "Personal Bot",
            "bot_type": "personal",
        }

        device_provider = MagicMock()
        device_provider.get_device.return_value = _make_device()

        resolver = MagicMock()
        resolver.resolve_for_bot.return_value = _make_ctx(
            binding_id=42,
            bot_id="personal-bot",
            user_id="owner-1",
        )

        observed_timeouts: list[float | None] = []

        async def invoke(
            conn_info,
            method,
            path,
            body=None,
            params=None,
            *,
            timeout=None,
        ):
            observed_timeouts.append(timeout)
            await asyncio.sleep(0.05)
            return {"success": True, "data": []}

        transport = MagicMock()
        transport.invoke = AsyncMock(side_effect=invoke)
        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            resolver=resolver,
            transport=transport,
        )

        result = await svc.list_all_crons(
            user_id="owner-1",
            nick_name="Owner",
            bot_id="personal-bot",
        )

        assert observed_timeouts == [0.01]
        assert result["data"] == []
        assert result["failed_targets"][0]["reason"] == "cron_api_timeout"
        assert "runtime_stage" not in result["failed_targets"][0]

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


class TestRuntimeTargetExpansion:
    @pytest.mark.asyncio
    async def test_runtime_device_queries_are_bounded(self, monkeypatch):
        monkeypatch.setattr(
            cron_runtime_targets,
            "RUNTIME_DEVICE_QUERY_CONCURRENCY",
            3,
        )
        active = 0
        max_active = 0

        lock = threading.Lock()

        def list_devices(*, binding_id, timeout):
            nonlocal active, max_active
            assert timeout == cron_runtime_targets.RUNTIME_DEVICE_QUERY_TIMEOUT_SECONDS
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.02)
            with lock:
                active -= 1
            return [f"DEVICE-{binding_id}"]

        device_provider = MagicMock()
        device_provider.get_device.return_value = _make_device(device_provider="baas")
        device_provider.list_devices_by_runtime_binding.side_effect = list_devices
        svc = _make_service(device_provider=device_provider)
        targets = [
            CronRuntimeTarget(
                bot_id=f"service-bot-{binding_id}",
                bot_name="Service Bot",
                owner_id="owner-1",
                bot_type="service",
                runtime_stage="online",
                binding_id=binding_id,
            )
            for binding_id in range(12)
        ]

        expanded, failed = await svc._expand_runtime_targets(targets)

        assert failed == []
        assert len(expanded) == 12
        assert 1 < max_active <= 3


class TestListRunningCronsOwnerId:
    @pytest.mark.asyncio
    async def test_list_running_crons_skips_hermes_service_bot(self):
        bot_provider = MagicMock()
        bot_provider.get_bot.return_value = {
            "bot_id": "service-hermes",
            "owner_id": "owner-1",
            "binding_id": 42,
            "bot_name": "Service Hermes",
            "bot_type": "service",
            "active_engine": " hermes ",
        }
        device_provider = MagicMock()
        resolver = MagicMock()
        transport = MagicMock()
        transport.invoke = AsyncMock()
        publish_repo = MagicMock()
        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            resolver=resolver,
            transport=transport,
            publish_repo=publish_repo,
        )

        result = await svc.list_running_crons(
            user_id="owner-1",
            nick_name="Owner",
            bot_id="service-hermes",
            runtime_stage="online",
        )

        assert result == {"success": True, "data": [], "failed_targets": []}
        device_provider.get_device.assert_not_called()
        device_provider.list_devices_by_runtime_binding.assert_not_called()
        resolver.resolve_for_binding.assert_not_called()
        transport.invoke.assert_not_awaited()
        publish_repo.get_latest_by_source_bot_id_and_owner_and_status.assert_not_called()

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
    async def test_list_running_crons_rejects_all_bot_id(self):
        bot_provider = MagicMock()
        svc = _make_service(bot_provider=bot_provider)

        with pytest.raises(CronRelayError) as exc_info:
            await svc.list_running_crons(
                user_id="owner-1",
                nick_name="Owner",
                bot_id="all",
            )

        assert exc_info.value.error_code == 400
        bot_provider.get_bot.assert_not_called()
        bot_provider.list_bots_by_owner_or_collaborator.assert_not_called()

    @pytest.mark.asyncio
    async def test_list_running_crons_requires_stage_for_service_bot(self):
        bot_provider = MagicMock()
        bot_provider.get_bot.return_value = {
            "bot_id": "service-bot",
            "owner_id": "owner-1",
            "binding_id": 42,
            "bot_name": "Service Bot",
            "bot_type": "service",
        }
        svc = _make_service(bot_provider=bot_provider)

        with pytest.raises(CronRelayError) as exc_info:
            await svc.list_running_crons(
                user_id="owner-1",
                nick_name="Owner",
                bot_id="service-bot",
            )

        assert exc_info.value.error_code == 400

    @pytest.mark.asyncio
    async def test_list_running_crons_rejects_stage_for_personal_bot(self):
        bot_provider = MagicMock()
        bot_provider.get_bot.return_value = {
            "bot_id": "personal-bot",
            "owner_id": "owner-1",
            "binding_id": 42,
            "bot_name": "Personal Bot",
            "bot_type": "personal",
        }
        svc = _make_service(bot_provider=bot_provider)

        with pytest.raises(CronRelayError) as exc_info:
            await svc.list_running_crons(
                user_id="owner-1",
                nick_name="Owner",
                bot_id="personal-bot",
                runtime_stage="draft",
            )

        assert exc_info.value.error_code == 400

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
        device_provider.list_devices_by_runtime_binding.return_value = [
            "DEVICE-001",
            "DEVICE-002",
        ]

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

        async def invoke(conn_info, method, path, body=None, params=None, *, timeout=None):
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
            user_id="owner-1",
            nick_name="Owner",
            bot_id="service-bot",
            runtime_stage="online",
        )

        assert len(result["data"]) == 2
        assert {item["device_uuid"] for item in result["data"]} == {
            "DEVICE-001",
            "DEVICE-002",
        }
        for item in result["data"]:
            assert "provider_device_id" not in item
            assert "bot_uuid" not in item
            assert "instance_status" not in item
            assert "instance_health" not in item
            assert "instance_health_status" not in item
        assert result["failed_targets"] == []

    @pytest.mark.asyncio
    async def test_list_running_crons_filters_runtime_stage(self):
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
        device_provider.get_device.side_effect = (
            lambda *, binding_id: {
                30: _make_device(device_provider="baas"),
            }[binding_id]
        )
        device_provider.list_devices_by_runtime_binding.return_value = [
            "DEVICE-001",
            "DEVICE-002",
        ]

        resolver = MagicMock()
        resolver.resolve_for_binding.side_effect = (
            lambda binding_id, operator_id, *, bot_id, device_uuid=None: _make_ctx(
                binding_id=binding_id,
                bot_id=bot_id,
                user_id=operator_id,
                device_uuid=device_uuid,
            )
        )

        async def invoke(conn_info, method, path, body=None, params=None, *, timeout=None):
            return {
                "success": True,
                "data": {
                    "running": [
                        {
                            "id": f"run-{conn_info['device_uuid']}",
                            "task_id": "task-o",
                        }
                    ]
                },
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

        result = await svc.list_running_crons(
            user_id="owner-1",
            nick_name="Owner",
            bot_id="service-bot",
            runtime_stage="online",
        )

        assert len(result["data"]) == 2
        assert {item["runtime_stage"] for item in result["data"]} == {"online"}
        assert {item["device_uuid"] for item in result["data"]} == {
            "DEVICE-001",
            "DEVICE-002",
        }
        resolver.resolve_for_binding.assert_any_call(
            30, "owner-1", bot_id="service-bot", device_uuid="DEVICE-001"
        )
        resolver.resolve_for_binding.assert_any_call(
            30, "owner-1", bot_id="service-bot", device_uuid="DEVICE-002"
        )
        resolver.resolve_for_bot.assert_not_called()
        device_provider.get_device.assert_any_call(binding_id=30)
        device_provider.get_device.assert_any_call(binding_id=30)
        assert all(call.kwargs["binding_id"] == 30 for call in device_provider.get_device.mock_calls)

    @pytest.mark.asyncio
    async def test_list_running_crons_filters_device_uuid_with_stage(self):
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
        device_provider.list_devices_by_runtime_binding.return_value = [
            "DEVICE-001",
            "DEVICE-002",
        ]

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
            "success": True,
            "data": {"running": [{"id": "run-002", "task_id": "task-o"}]},
        })

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            resolver=resolver,
            transport=transport,
            publish_repo=publish_repo,
        )

        result = await svc.list_running_crons(
            user_id="owner-1",
            nick_name="Owner",
            bot_id="service-bot",
            runtime_stage="online",
            device_uuid="DEVICE-002",
        )

        assert len(result["data"]) == 1
        assert result["data"][0]["device_uuid"] == "DEVICE-002"
        resolver.resolve_for_binding.assert_called_once_with(
            30, "owner-1", bot_id="service-bot", device_uuid="DEVICE-002"
        )
        transport.invoke.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_list_running_crons_rejects_device_uuid_without_stage(self):
        svc = _make_service()

        with pytest.raises(CronRelayError) as exc_info:
            await svc.list_running_crons(
                user_id="owner-1",
                nick_name="Owner",
                bot_id="service-bot",
                device_uuid="DEVICE-002",
            )

        assert exc_info.value.error_code == 400

    @pytest.mark.asyncio
    async def test_list_running_crons_records_adapter_failure_with_dict_data(self):
        bot_provider = MagicMock()
        bot_provider.get_bot.return_value = {
            "bot_id": "service-bot",
            "owner_id": "owner-1",
            "binding_id": 10,
            "bot_name": "Service Bot",
            "bot_type": "service",
        }

        device_provider = MagicMock()
        device_provider.get_device.return_value = _make_device(device_provider="arca")

        resolver = MagicMock()
        resolver.resolve_for_bot.return_value = _make_ctx(
            binding_id=10,
            bot_id="service-bot",
            user_id="owner-1",
        )

        transport = MagicMock()
        transport.invoke = AsyncMock(return_value={
            "success": False,
            "message": "adapter rejected",
            "data": {},
        })
        publish_repo = MagicMock()
        publish_repo.get_latest_by_source_bot_id_and_owner_and_status.return_value = None

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            resolver=resolver,
            transport=transport,
            publish_repo=publish_repo,
        )

        result = await svc.list_running_crons(
            user_id="owner-1",
            nick_name="Owner",
            bot_id="service-bot",
            runtime_stage="draft",
        )

        assert result["data"] == []
        assert result["failed_targets"] == [
            {
                "bot_id": "service-bot",
                "bot_name": "Service Bot",
                "owner_id": "owner-1",
                "runtime_stage": "draft",
                "reason": "cron_api_failed",
                "message": "adapter rejected",
            }
        ]


class TestForwardRequestUsesResolver:
    def test_verify_stage_falls_back_to_success_publish_record(self):
        bot = {
            "bot_id": "service-bot",
            "owner_id": "owner-1",
            "binding_id": 10,
            "bot_name": "Service Bot",
            "bot_type": "service",
        }
        success_record = SimpleNamespace(
            id=202,
            status=PublishStatus.SUCCESS.value,
            ext={"binding": {"verify": 20, "online": 30}},
        )
        publish_repo = MagicMock()
        publish_repo.get_latest_by_source_bot_id_and_owner_and_status.side_effect = (
            lambda source_bot_id, owner_id, status, env: (
                success_record if status == PublishStatus.SUCCESS.value else None
            )
        )
        svc = _make_service(publish_repo=publish_repo)

        target = svc._resolve_published_runtime_target(
            bot,
            "owner-1",
            "verify",
        )

        assert target.binding_id == 20
        assert target.publish_id == 202
        assert target.publish_status == PublishStatus.SUCCESS.value
        queried_statuses = [
            call.kwargs["status"]
            for call in publish_repo.get_latest_by_source_bot_id_and_owner_and_status.mock_calls
        ]
        assert queried_statuses == [
            PublishStatus.VALIDATING.value,
            PublishStatus.SUCCESS.value,
        ]

    @pytest.mark.asyncio
    async def test_forward_request_calls_resolver_not_v2(self):
        """forward_request uses the resolver-built adapter connection context."""
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
    async def test_get_cron_detail_reports_read_timeout(self, monkeypatch):
        monkeypatch.setattr(
            cron_runtime_targets,
            "CRON_READ_TIMEOUT_SECONDS",
            0.01,
        )
        bot_provider = MagicMock()
        bot_provider.get_bot.return_value = {
            "bot_id": "personal-bot",
            "owner_id": "owner-1",
            "binding_id": 42,
            "bot_name": "Personal Bot",
            "bot_type": "personal",
        }

        device_provider = MagicMock()
        device_provider.get_device.return_value = _make_device()

        resolver = MagicMock()
        resolver.resolve_for_bot.return_value = _make_ctx(
            binding_id=42,
            bot_id="personal-bot",
            user_id="owner-1",
        )

        async def invoke(
            conn_info,
            method,
            path,
            body=None,
            params=None,
            *,
            timeout=None,
        ):
            await asyncio.sleep(0.05)
            return {"success": True, "data": {"id": "task-1"}}

        transport = MagicMock()
        transport.invoke = AsyncMock(side_effect=invoke)
        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            resolver=resolver,
            transport=transport,
        )

        with pytest.raises(CronRelayError) as exc_info:
            await svc.get_cron_detail(
                bot_id="personal-bot",
                user_id="owner-1",
                nick_name="Owner",
                task_id="task-1",
            )

        assert exc_info.value.error_code == 504
        assert "cron_api_timeout" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_run_cron_routes_verify_stage_by_publish_binding_and_keeps_task_id(self):
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
        device_provider.get_device.return_value = _make_device(device_provider="arca")

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

        result = await svc.run_cron(
            bot_id="service-bot",
            user_id="owner-1",
            nick_name="Owner",
            task_id="task-v",
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
    async def test_update_cron_allows_enabled_toggle_for_published_stage(self):
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
        device_provider.get_device.return_value = _make_device(device_provider="arca")

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

        result = await svc.update_cron(
            bot_id="service-bot",
            user_id="owner-1",
            nick_name="Owner",
            task_id="task-o",
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
    async def test_update_cron_fans_out_enabled_toggle_to_runtime_instances(self):
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
        device_provider.list_devices_by_runtime_binding.return_value = [
            "DEVICE-001",
            "DEVICE-002",
        ]

        resolver = MagicMock()
        resolver.resolve_for_binding.side_effect = (
            lambda binding_id, operator_id, *, bot_id, device_uuid=None: _make_ctx(
                binding_id=binding_id,
                bot_id=bot_id,
                user_id=operator_id,
                device_uuid=device_uuid,
            )
        )

        async def invoke(conn_info, method, path, body=None, params=None, *, timeout=None):
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

        result = await svc.update_cron(
            bot_id="service-bot",
            user_id="owner-1",
            nick_name="Owner",
            task_id="task-o",
            body={"enabled": False},
            runtime_stage="online",
        )

        assert result["success"] is False
        assert result["message"] == "partial runtime instances failed"
        assert "target" not in result["data"]
        assert result["data"]["succeeded"] == 1
        assert result["data"]["failed"] == 1
        success_item = result["data"]["results"][0]
        assert success_item["bot_id"] == "service-bot"
        assert success_item["bot_name"] == "Service Bot"
        assert success_item["owner_id"] == "owner-1"
        assert success_item["runtime_stage"] == "online"
        assert success_item["publish_id"] == 202
        assert success_item["publish_status"] == PublishStatus.SUCCESS.value
        assert success_item["device_uuid"] == "DEVICE-001"
        assert "provider_device_id" not in success_item
        assert "bot_uuid" not in success_item
        assert "instance_status" not in success_item
        assert "instance_health" not in success_item
        assert "instance_health_status" not in success_item
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
    async def test_run_cron_fan_out_manual_run_reports_all_failed(self):
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
        device_provider.list_devices_by_runtime_binding.return_value = [
            "DEVICE-001",
            "DEVICE-002",
        ]

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

        result = await svc.run_cron(
            bot_id="service-bot",
            user_id="owner-1",
            nick_name="Owner",
            task_id="task-o",
            force=True,
            runtime_stage="online",
        )

        assert result["success"] is False
        assert "target" not in result["data"]
        assert result["data"]["succeeded"] == 0
        assert result["data"]["failed"] == 2
        assert {item["bot_id"] for item in result["data"]["results"]} == {
            "service-bot",
        }
        assert {item["runtime_stage"] for item in result["data"]["results"]} == {
            "online",
        }
        assert [item["reason"] for item in result["failed_targets"]] == [
            "cron_api_failed",
            "cron_api_failed",
        ]
        assert transport.invoke.await_count == 2

    @pytest.mark.asyncio
    async def test_run_cron_rejects_service_runtime_without_device_provider(self):
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
        device_provider.get_device.return_value = MagicMock(
            status=DeviceBindingStatus.ACTIVE
        )

        transport = MagicMock()
        transport.invoke = AsyncMock(return_value={"success": True, "data": {}})

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            transport=transport,
            publish_repo=publish_repo,
        )

        result = await svc.run_cron(
            bot_id="service-bot",
            user_id="owner-1",
            nick_name="Owner",
            task_id="task-o",
            force=True,
            runtime_stage="online",
        )

        assert result["success"] is False
        assert result["failed_targets"][0]["reason"] == "unsupported_device_provider"
        transport.invoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_cron_runs_fans_out_to_runtime_instances(self):
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
        device_provider.list_devices_by_runtime_binding.return_value = [
            "DEVICE-001",
            "DEVICE-002",
        ]

        resolver = MagicMock()
        resolver.resolve_for_binding.side_effect = (
            lambda binding_id, operator_id, *, bot_id, device_uuid=None: _make_ctx(
                binding_id=binding_id,
                bot_id=bot_id,
                user_id=operator_id,
                device_uuid=device_uuid,
            )
        )

        async def invoke(conn_info, method, path, body=None, params=None, *, timeout=None):
            device_uuid = conn_info["device_uuid"]
            return {
                "success": True,
                "data": {
                    "input": "hello",
                    "runs": [{"run_id": f"run-{device_uuid}"}],
                },
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

        result = await svc.get_cron_runs(
            bot_id="service-bot",
            user_id="owner-1",
            nick_name="Owner",
            task_id="task-o",
            limit=20,
            runtime_stage="online",
        )

        assert result["success"] is True
        assert "target" not in result["data"]
        assert result["data"]["succeeded"] == 2
        assert {item["device_uuid"] for item in result["data"]["results"]} == {
            "DEVICE-001",
            "DEVICE-002",
        }
        assert {
            item["data"]["runs"][0]["run_id"]
            for item in result["data"]["results"]
        } == {"run-DEVICE-001", "run-DEVICE-002"}
        for item in result["data"]["results"]:
            assert item["bot_id"] == "service-bot"
            assert item["bot_name"] == "Service Bot"
            assert item["owner_id"] == "owner-1"
            assert item["runtime_stage"] == "online"
            assert item["publish_id"] == 202
            assert item["publish_status"] == PublishStatus.SUCCESS.value
            assert "provider_device_id" not in item
            assert "bot_uuid" not in item
            assert "instance_status" not in item
            assert "instance_health" not in item
            assert "instance_health_status" not in item
            assert "bot_id" not in item["data"]
            assert "runtime_stage" not in item["data"]
        resolver.resolve_for_binding.assert_any_call(
            30, "owner-1", bot_id="service-bot", device_uuid="DEVICE-001"
        )
        resolver.resolve_for_binding.assert_any_call(
            30, "owner-1", bot_id="service-bot", device_uuid="DEVICE-002"
        )
        assert transport.invoke.await_count == 2
        assert {
            call.kwargs["timeout"] for call in transport.invoke.await_args_list
        } == {8.0}

    @pytest.mark.asyncio
    async def test_get_cron_runs_keeps_partial_results_on_timeout(self, monkeypatch):
        monkeypatch.setattr(
            cron_runtime_targets,
            "CRON_READ_TIMEOUT_SECONDS",
            0.01,
        )
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
        device_provider.list_devices_by_runtime_binding.return_value = [
            "DEVICE-001",
            "DEVICE-002",
        ]

        resolver = MagicMock()
        resolver.resolve_for_binding.side_effect = (
            lambda binding_id, operator_id, *, bot_id, device_uuid=None: _make_ctx(
                binding_id=binding_id,
                bot_id=bot_id,
                user_id=operator_id,
                device_uuid=device_uuid,
            )
        )

        async def invoke(
            conn_info,
            method,
            path,
            body=None,
            params=None,
            *,
            timeout=None,
        ):
            if conn_info["device_uuid"] == "DEVICE-002":
                await asyncio.sleep(0.05)
            return {
                "success": True,
                "data": {
                    "input": "hello",
                    "runs": [{"run_id": f"run-{conn_info['device_uuid']}"}],
                },
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

        result = await svc.get_cron_runs(
            bot_id="service-bot",
            user_id="owner-1",
            nick_name="Owner",
            task_id="task-o",
            runtime_stage="online",
        )

        assert result["success"] is False
        assert result["message"] == "partial runtime instances failed"
        assert result["data"]["succeeded"] == 1
        assert result["data"]["failed"] == 1
        failed_result = next(
            item for item in result["data"]["results"] if not item["success"]
        )
        assert failed_result["device_uuid"] == "DEVICE-002"
        assert failed_result["reason"] == "cron_api_timeout"
        assert result["failed_targets"][0]["reason"] == "cron_api_timeout"

    @pytest.mark.asyncio
    async def test_get_cron_runs_uses_runtime_device_list_not_get_instances(self):
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
        device_provider.get_device.return_value = _make_device(device_provider="teclaw")
        device_provider.get_instances.side_effect = AssertionError(
            "cron runtime expansion must not use get_instances"
        )
        device_provider.list_devices_by_runtime_binding.return_value = ["DEVICE-001"]

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
            "success": True,
            "data": {"runs": [{"run_id": "run-DEVICE-001"}]},
        })

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            resolver=resolver,
            transport=transport,
            publish_repo=publish_repo,
        )

        result = await svc.get_cron_runs(
            bot_id="service-bot",
            user_id="owner-1",
            nick_name="Owner",
            task_id="task-o",
            limit=20,
            runtime_stage="online",
        )

        assert result["success"] is True
        assert "target" not in result["data"]
        assert result["data"]["succeeded"] == 1
        result_item = result["data"]["results"][0]
        assert result_item["bot_id"] == "service-bot"
        assert result_item["bot_name"] == "Service Bot"
        assert result_item["owner_id"] == "owner-1"
        assert result_item["runtime_stage"] == "online"
        assert result_item["publish_id"] == 202
        assert result_item["publish_status"] == PublishStatus.SUCCESS.value
        assert result_item["device_uuid"] == "DEVICE-001"
        assert result["failed_targets"] == []
        device_provider.get_instances.assert_not_called()
        device_provider.list_devices_by_runtime_binding.assert_called_once_with(
            binding_id=30,
            timeout=cron_runtime_targets.RUNTIME_DEVICE_QUERY_TIMEOUT_SECONDS,
        )
        resolver.resolve_for_binding.assert_called_once_with(
            30,
            "owner-1",
            bot_id="service-bot",
            device_uuid="DEVICE-001",
        )

    @pytest.mark.asyncio
    async def test_get_cron_runs_routes_specific_device_uuid(self):
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
        device_provider.list_devices_by_runtime_binding.return_value = [
            "DEVICE-001",
            "DEVICE-002",
        ]

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
            "success": True,
            "data": {"runs": [{"run_id": "run-DEVICE-002"}]},
        })

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            resolver=resolver,
            transport=transport,
            publish_repo=publish_repo,
        )

        result = await svc.get_cron_runs(
            bot_id="service-bot",
            user_id="owner-1",
            nick_name="Owner",
            task_id="task-o",
            limit=20,
            runtime_stage="online",
            device_uuid="DEVICE-002",
        )

        assert result["success"] is True
        assert result["data"]["device_uuid"] == "DEVICE-002"
        assert result["data"]["runs"] == [{"run_id": "run-DEVICE-002"}]
        resolver.resolve_for_binding.assert_called_once_with(
            30, "owner-1", bot_id="service-bot", device_uuid="DEVICE-002"
        )
        transport.invoke.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_cron_runs_rejects_personal_bot_device_uuid(self):
        bot_provider = MagicMock()
        bot_provider.get_bot.return_value = {
            "bot_id": "personal-bot",
            "owner_id": "owner-1",
            "binding_id": 10,
            "bot_name": "Personal Bot",
            "bot_type": "personal",
        }

        device_provider = MagicMock()
        device_provider.get_device.return_value = _make_device(device_provider="arca")

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
        )

        with pytest.raises(CronRelayError) as exc_info:
            await svc.get_cron_runs(
                bot_id="personal-bot",
                user_id="owner-1",
                nick_name="Owner",
                task_id="task-1",
                runtime_stage="draft",
                device_uuid="DEVICE-002",
            )

        assert exc_info.value.error_code == 400

    @pytest.mark.asyncio
    async def test_run_cron_online_stage_routes_teclaw_binding_via_real_resolver(self):
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
        device_provider.get_device.return_value = _make_device(device_provider="teclaw")
        device_provider.list_devices_by_runtime_binding.return_value = ["DEVICE-T1"]

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

        await svc.run_cron(
            bot_id="service-bot",
            user_id="owner-1",
            nick_name="Owner",
            task_id="task-o",
            runtime_stage="online",
        )

        binding_repo.get_by_id.assert_called_once_with(30)
        teclaw_builder.build.assert_called_once()
        assert teclaw_builder.build.call_args.kwargs["device_uuid"] == "DEVICE-T1"
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
            await svc.delete_cron(
                bot_id="service-bot",
                user_id="owner-1",
                nick_name="Owner",
                task_id="task-v",
                runtime_stage="verify",
            )

        assert exc_info.value.error_code == 403
        bot_provider.get_bot.assert_not_called()

    @pytest.mark.asyncio
    async def test_forward_request_rejects_publish_stage_edit_except_enabled(self):
        bot_provider = MagicMock()
        svc = _make_service(bot_provider=bot_provider)

        with pytest.raises(CronRelayError) as exc_info:
            await svc.update_cron(
                bot_id="service-bot",
                user_id="owner-1",
                nick_name="Owner",
                task_id="task-v",
                body={"name": "new-name"},
                runtime_stage="online",
            )

        assert exc_info.value.error_code == 403
        bot_provider.get_bot.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_cron_rejects_publish_stage_non_dict_body(self):
        bot_provider = MagicMock()
        svc = _make_service(bot_provider=bot_provider)

        with pytest.raises(CronRelayError) as exc_info:
            await svc.update_cron(
                bot_id="service-bot",
                user_id="owner-1",
                nick_name="Owner",
                task_id="task-v",
                body=["enabled"],
                runtime_stage="online",
            )

        assert exc_info.value.error_code == 400
        assert str(exc_info.value) == "body must be a dictionary"
        bot_provider.get_bot.assert_not_called()
