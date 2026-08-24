"""Integration tests for ExpireSandboxTimerTask routing.

Verifies the expiry sweep routes an expired ACK device through the high-level
``BotManageService.stop_bot`` flow (STOP publish + bot STOPPING), rather than
silently marking only the device STOPPED and leaving the bot ACTIVE/inconsistent.

Full teardown to bot STOPPED + device STOPPED is owned by the STOP-publish
pipeline and covered by ``core/service/test_bot_management_service.py`` plus the
publish service tests; here we assert the task's routing and the resulting
intermediate consistency contract.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from secbaas.community.api.bot_manage import BotStatus
from secbaas.community.api.device_manage import DeviceStatus
from secbaas.community.api.publish_manage import PublishType
from secbaas.community.bootstrap import get_container
from secbaas.community.core.service.scheduler._tasks._expire_sandbox_timer_task import (
    ExpireSandboxTimerTask,
    ExpireSandboxTimerTaskConfig,
)
from secbaas.community.core.utils.env_utils import get_current_env

pytestmark = pytest.mark.integration

TEST_ENV = get_current_env()


def _acquired_lock():
    return SimpleNamespace(acquired=True)


def _build_task(container) -> ExpireSandboxTimerTask:
    lock = MagicMock()
    lock.try_lock.return_value.__enter__.return_value = _acquired_lock()
    return ExpireSandboxTimerTask(
        config=ExpireSandboxTimerTaskConfig(
            enabled=True, dry_run=False, arca_provider="aliyun_ack"
        ),
        lock_service=lock,
        device_repo=container.repository.device_repository(),
        bot_manage_service=container.services.bot_management_service(),
        bot_repo=container.repository.bot_repository(),
        bot_device_rel_repo=container.repository.bot_device_rel_repository(),
    )


class TestExpireSandboxStopsBot:
    @pytest.mark.asyncio
    async def test_expired_device_routes_through_stop_bot(
        self,
        bot_repository,
        device_repository,
        rel_repository,
        publish_repository,
        created_bot_ids,
        created_device_ids,
        created_rel_ids,
        created_publish_ids,
    ):
        tenant = f"t-{uuid4().hex[:8]}"

        bot_uuid = uuid4().hex
        bot_id = bot_repository.insert_bot(
            bot_uuid=bot_uuid,
            tenant=tenant,
            env=TEST_ENV,
            domain="test",
            creator="tester",
            modifier="tester",
            status=BotStatus.ACTIVE.value,
            name="expiry-bot",
            description=None,
            template_uuid=None,
            replica_desired=1,
            replica_minimum=1,
            replica_maximum=10,
            auto_scaling_enabled=0,
            sla_grade="standard",
            extra_config={},
        )
        created_bot_ids.append(bot_id)

        device_uuid = uuid4().hex
        device_id = device_repository.insert_device(
            device_uuid=device_uuid,
            tenant=tenant,
            env=TEST_ENV,
            domain="test",
            creator="tester",
            modifier="tester",
            status=DeviceStatus.ACTIVE.value,
            provider_type="ARCA",
            provider_device_id="sbx-expiry",
            provider_device_props={
                "sandbox_id": "sbx-expiry",
                # already expired: timestamp in the past
                "ttl_expiration_timestamp": int((time.time() - 3600) * 1000),
            },
            extra_config={"deploy_config": {"ttl_in_minutes": 10080}},
        )
        created_device_ids.append(device_id)

        rel_id = rel_repository.insert_rel(
            bot_id=bot_id,
            device_uuid=device_uuid,
            tenant=tenant,
            env=TEST_ENV,
            domain="test",
            creator="tester",
            modifier="tester",
        )
        created_rel_ids.append(rel_id)

        container = get_container()
        task = _build_task(container)
        report = await task.run()

        # The due device was scanned and routed through stop_bot, not left dangling.
        assert report is not None
        assert report.scanned >= 1
        assert report.stopped >= 1
        assert report.failed == 0

        # Bot was transitioned to STOPPING (stop_bot initiated), not left ACTIVE.
        bot = bot_repository.get_by_id(bot_id, tenant=tenant, env=TEST_ENV)
        assert bot is not None
        assert bot.status == BotStatus.STOPPING.value

        # A STOP publish was created for the bot.
        publishes = publish_repository.list_by_bot_id(
            bot_id=bot_id, tenant=tenant, env=TEST_ENV
        )
        stop_publishes = [
            p for p in publishes if p.publish_type == PublishType.STOP.value
        ]
        assert len(stop_publishes) >= 1
        created_publish_ids.extend(p.id for p in publishes)

    @pytest.mark.asyncio
    async def test_unbound_device_is_skipped(
        self,
        device_repository,
        created_device_ids,
    ):
        tenant = f"t-{uuid4().hex[:8]}"
        device_uuid = uuid4().hex
        device_id = device_repository.insert_device(
            device_uuid=device_uuid,
            tenant=tenant,
            env=TEST_ENV,
            domain="test",
            creator="tester",
            modifier="tester",
            status=DeviceStatus.ACTIVE.value,
            provider_type="ARCA",
            provider_device_id="sbx-unbound",
            provider_device_props={
                "sandbox_id": "sbx-unbound",
                "ttl_expiration_timestamp": int((time.time() - 3600) * 1000),
            },
            extra_config={"deploy_config": {"ttl_in_minutes": 10080}},
        )
        created_device_ids.append(device_id)

        container = get_container()
        task = _build_task(container)
        report = await task.run()

        assert report is not None
        assert report.scanned >= 1
        assert report.skipped >= 1

        device = device_repository.get_by_id(device_id, tenant=tenant, env=TEST_ENV)
        assert device is not None
        assert device.status == DeviceStatus.ACTIVE.value
