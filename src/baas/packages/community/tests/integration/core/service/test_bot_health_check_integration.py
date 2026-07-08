from uuid import uuid4

import pytest

from secbaas.api.bot_manage import BotStatus
from secbaas.api.device_manage import DeviceStatus
from secbaas.core.utils.env_utils import get_current_env
from tests.integration.core.service.conftest import (
    create_test_devices_for_bot,
    create_test_tenant,
)

TEST_ENV = get_current_env()


@pytest.mark.integration
class TestBotHealthCheckIntegration:
    @pytest.fixture
    def bot_with_device(
        self,
        bot_repository,
        device_repository,
        rel_repository,
        created_bot_ids,
        created_device_ids,
        created_rel_ids,
        created_tenant_ids,
        created_template_ids,
    ):
        tenant, _ = create_test_tenant(
            created_tenant_ids=created_tenant_ids,
            created_template_ids=created_template_ids,
        )
        bot_uuid = uuid4().hex
        bot_id = bot_repository.insert_bot(
            bot_uuid=bot_uuid,
            tenant=tenant,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status=BotStatus.ACTIVE.value,
            name=f"HC-Test Bot {bot_uuid[:8]}",
            description="Bot for health check integration test",
            template_uuid=None,
            replica_desired=1,
            replica_minimum=1,
            replica_maximum=10,
            auto_scaling_enabled=0,
            sla_grade="standard",
            extra_config={},
        )
        created_bot_ids.append(bot_id)
        create_test_devices_for_bot(
            device_repository=device_repository,
            rel_repository=rel_repository,
            tenant=tenant,
            bot_id=bot_id,
            device_status=DeviceStatus.ACTIVE.value,
            num_devices=1,
            created_device_ids=created_device_ids,
            created_rel_ids=created_rel_ids,
        )
        return {"tenant": tenant, "bot_uuid": bot_uuid, "bot_id": bot_id}

    def test_di_wires_health_checker(self, bootstrap_init):
        bms = bootstrap_init.services.bot_management_service()
        assert bms._health_checker is not None

    @pytest.mark.asyncio
    async def test_get_bot_without_health_check(self, bootstrap_init, bot_with_device):
        bms = bootstrap_init.services.bot_management_service()
        result = await bms.get_bot(
            tenant=bot_with_device["tenant"],
            bot_uuid=bot_with_device["bot_uuid"],
            health_check=False,
        )
        assert result is not None
        assert result.bot_uuid == bot_with_device["bot_uuid"]

    @pytest.mark.asyncio
    async def test_get_bot_with_health_check_has_devices(
        self, bootstrap_init, bot_with_device
    ):
        bms = bootstrap_init.services.bot_management_service()
        result = await bms.get_bot(
            tenant=bot_with_device["tenant"],
            bot_uuid=bot_with_device["bot_uuid"],
            health_check=True,
        )
        assert result is not None
        assert result.bot_uuid == bot_with_device["bot_uuid"]

    @pytest.mark.asyncio
    async def test_get_bot_with_health_check_and_engine_type(
        self, bootstrap_init, bot_with_device
    ):
        bms = bootstrap_init.services.bot_management_service()
        result = await bms.get_bot(
            tenant=bot_with_device["tenant"],
            bot_uuid=bot_with_device["bot_uuid"],
            health_check=True,
            engine_type="openclaw",
        )
        assert result is not None
        assert result.bot_uuid == bot_with_device["bot_uuid"]
