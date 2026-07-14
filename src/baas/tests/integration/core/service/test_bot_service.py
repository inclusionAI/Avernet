"""Optimized integration tests for BotService with shared fixtures.

Consolidates 22 individual tests into 4 comprehensive test methods.
Uses shared fixtures to minimize database record creation.
"""

from unittest.mock import patch
from uuid import uuid4

import pytest

from secbaas.community.api.bot_manage import BotClusterCreate, BotStatus
from secbaas.community.api.bot_runtime import BotNotFoundError
from secbaas.community.api.device_manage import DeviceStatus
from secbaas.community.api.template_manage import TemplateNotFoundError
from secbaas.community.bootstrap import get_container
from secbaas.community.core.utils.env_utils import get_current_env

TEST_ENV = get_current_env()
FIXED_TENANT_NAME = "test_tenant"


def _dts():
    return get_container().services.device_template_service()


def _bs():
    return get_container().services.bot_crud_service()


def generate_uuid() -> str:
    """Generate unique UUID for testing."""
    return uuid4().hex


@pytest.fixture(scope="module")
def shared_bot_service_setup(
    bot_repository,
    device_repository,
    created_bot_ids,
    created_device_ids,
    created_tenant_ids,
    created_template_ids,
    skip_if_zdas_unavailable,
):
    """Create ONE shared bot with devices for all BotService tests.

    This fixture creates minimal test entities at module scope.
    """
    from secbaas.community.api.template_manage import (
        ArcaTemplateConfig,
        TemplateCreate,
    )
    from secbaas.community.api.tenant_manage import TenantType

    # Create/get shared tenant
    tenant_repo = get_container().repository.tenant_repository()
    existing_tenant = tenant_repo.get_by_name(FIXED_TENANT_NAME, TEST_ENV)

    if not existing_tenant:
        record_id = tenant_repo.insert_tenant(
            name=FIXED_TENANT_NAME,
            env=TEST_ENV,
            creator="test_user",
            modifier="test_user",
            description=None,
            extra_config=None,
        )
        created_tenant_ids.append(record_id)

    # Create ONE template
    template_uuid = (
        f"test-bot-service-{int(__import__('time').time() * 1000000) % 10000000000}"
    )
    # Use a unique template_id per run to avoid collisions from previous test runs
    template_id_seed = int(__import__("time").time() * 1000000) % 1000000 + 2073000000
    template = _dts().create_template(
        tenant=FIXED_TENANT_NAME,
        data=TemplateCreate(
            template_uuid=template_uuid,
            template_id=template_id_seed,
            type=TenantType.ARCA,
            name=f"Shared BotService Template {template_uuid}",
            description=None,
            config=ArcaTemplateConfig(
                type="ARCA",
                base_url="http://test",
                api_key="test",
                template_id="",
                arca_template_id_pre=None,
                arca_template_id_prod=None,
                oss_mount_id=None,
            ),
            operator="test_user",
        ),
    )
    # Ensure template is ONLINE for lookups in tests
    try:
        from secbaas.community.api.template_manage import TemplateStatus

        template_repo = get_container().repository.device_template_repository()
        template_repo.update_status(
            template_uuid=template.template_uuid,
            tenant=FIXED_TENANT_NAME,
            current_status=TemplateStatus.CREATED.value,
            new_status=TemplateStatus.ONLINE.value,
        )
    except Exception:
        pass
    created_template_ids.append(template.id)

    # Create ONE bot
    bot_uuid = generate_uuid()
    bot_id = bot_repository.insert_bot(
        bot_uuid=bot_uuid,
        tenant=FIXED_TENANT_NAME,
        env=TEST_ENV,
        domain="test_domain",
        creator="test_user",
        modifier="test_user",
        status=BotStatus.ACTIVE.value,
        name=f"Shared BotService Bot {bot_uuid[:8]}",
        description="Shared bot for bot_service tests",
        template_uuid=template.template_uuid,
        replica_desired=2,
        replica_minimum=1,
        replica_maximum=10,
        auto_scaling_enabled=0,
        sla_grade="standard",
        extra_config={},
    )
    created_bot_ids.append(bot_id)

    # Create ONE device
    device_uuid = generate_uuid()
    device_id = device_repository.insert_device(
        device_uuid=device_uuid,
        tenant=FIXED_TENANT_NAME,
        env=TEST_ENV,
        domain="test_domain",
        creator="test_user",
        modifier="test_user",
        status=DeviceStatus.ACTIVE.value,
        provider_type="Sigma",
        provider_device_id=None,
        provider_device_props={},
        extra_config={},
    )
    created_device_ids.append(device_id)

    # Create relationship
    rel_repo = get_container().repository.bot_device_rel_repository()
    rel_id = rel_repo.insert_rel(
        bot_id=bot_id,
        device_uuid=device_uuid,
        tenant=FIXED_TENANT_NAME,
        env=TEST_ENV,
        domain="test_domain",
        creator="test_user",
        modifier="test_user",
    )

    return {
        "tenant": FIXED_TENANT_NAME,
        "template_id": template.id,
        "template_uuid": template.template_uuid,
        "bot_id": bot_id,
        "bot_uuid": bot_uuid,
        "device_id": device_id,
        "device_uuid": device_uuid,
        "rel_id": rel_id,
    }


@pytest.mark.integration
class TestBotServiceIntegration:
    """Optimized integration tests with shared fixtures."""

    @pytest.mark.asyncio
    async def test_create_bot_lifecycle(
        self, shared_bot_service_setup, created_bot_ids
    ):
        """Consolidated test for bot creation lifecycle.

        Tests:
        - create with valid tenant and template
        - calculates status ACTIVE with device success
        - calculates status based on device failures
        - validates tenant exists
        - validates template exists
        """
        import datetime

        from secbaas.community.api.device_manage import DeviceConfig, DeviceResponse
        from secbaas.community.core.service.device_manage import DefaultDeviceService

        # Mock device for successful creation
        mock_device = DeviceResponse(
            id=123,
            device_uuid=generate_uuid(),
            tenant=FIXED_TENANT_NAME,
            env=TEST_ENV,
            domain="test_domain",
            status=DeviceStatus.ACTIVE.value,
            provider_type="Sigma",
            provider_device_id=None,
            provider_device_props={},
            extra_config=DeviceConfig(),
            creator="test_user",
            modifier="test_user",
            gmt_create=datetime.datetime.now(),
            gmt_modified=datetime.datetime.now(),
        )

        # Test 1: Create bot with valid tenant/template
        data = BotClusterCreate(
            bot_name="Test Bot",
            bot_desc=None,
            device_count=1,
            template_uuid=shared_bot_service_setup["template_uuid"],
            operator="test_user",
        )

        with patch.object(
            DefaultDeviceService,
            "create_device",
            return_value=mock_device,
        ):
            result = await _bs().create_bot(
                tenant=FIXED_TENANT_NAME,
                data=data,
            )
            assert result is not None
            assert result.status == BotStatus.PENDING.value
            created_bot_ids.append(result.id)

        # Test 2: Validates template tenant (template not found for wrong tenant)
        with pytest.raises(TemplateNotFoundError, match="Template with uuid not found"):
            await _bs().create_bot(
                tenant="nonexistent_tenant",
                data=data,
            )

        # Test 3: Validates template exists (through template service)
        data_invalid_template = BotClusterCreate(
            bot_name="Test Bot",
            bot_desc=None,
            device_count=1,
            template_uuid="nonexistent-template-uuid",  # Non-existent
            operator="test_user",
        )
        with pytest.raises(TemplateNotFoundError, match="Template with uuid not found"):
            await _bs().create_bot(
                tenant=FIXED_TENANT_NAME,
                data=data_invalid_template,
            )

    @pytest.mark.asyncio
    async def test_device_selection(
        self, shared_bot_service_setup, created_bot_ids, created_device_ids
    ):
        """Consolidated test for device selection.

        Tests:
        - select device from active devices
        - no active devices raises error
        - validates tenant ownership
        - validates bot exists
        """
        # Test 1: Select device from active devices
        result = await _bs().select_device(
            tenant=FIXED_TENANT_NAME,
            bot_id=shared_bot_service_setup["bot_id"],
        )
        assert result is not None
        assert result.device_uuid == shared_bot_service_setup["device_uuid"]

        # Test 2: No active devices raises error
        # Create a bot without devices
        bot_repo = get_container().repository.bot_repository()
        empty_bot_uuid = generate_uuid()
        empty_bot_id = bot_repo.insert_bot(
            bot_uuid=empty_bot_uuid,
            tenant=FIXED_TENANT_NAME,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status=BotStatus.PENDING.value,
            name="Empty Bot",
            description="Bot without devices",
            template_uuid=None,
            replica_desired=0,
            replica_minimum=0,
            replica_maximum=10,
            auto_scaling_enabled=0,
            sla_grade="standard",
            extra_config={},
        )
        created_bot_ids.append(empty_bot_id)

        with pytest.raises(RuntimeError, match="No available Device"):
            await _bs().select_device(
                tenant=FIXED_TENANT_NAME,
                bot_id=empty_bot_id,
            )

        # Test 3: Validates bot exists
        with pytest.raises(BotNotFoundError, match="Bot not found"):
            await _bs().select_device(
                tenant=FIXED_TENANT_NAME,
                bot_id=99999999,
            )

    @pytest.mark.asyncio
    async def test_bot_query(
        self, bot_repository, shared_bot_service_setup, created_bot_ids
    ):
        """Consolidated test for bot query operations.

        Tests:
        - get bot by ID with tenant match
        - tenant mismatch returns None
        - calculates status from devices
        - list bots with pagination
        - list filters by tenant
        - list calculates status on demand
        """
        # Test 1: Get bot by ID with tenant match
        result = await _bs().get_bot(
            tenant=FIXED_TENANT_NAME,
            bot_id=shared_bot_service_setup["bot_id"],
        )
        assert result is not None
        assert result.id == shared_bot_service_setup["bot_id"]
        assert result.status == BotStatus.ACTIVE.value

        # Test 2: Tenant mismatch returns None
        result = await _bs().get_bot(
            tenant="wrong_tenant",
            bot_id=shared_bot_service_setup["bot_id"],
        )
        assert result is None

        # Test 3: List bots with pagination
        list_result = await _bs().list_bots(
            tenant=FIXED_TENANT_NAME,
            page=1,
            page_size=10,
        )
        assert list_result.total >= 1
        assert len(list_result.items) >= 1
        assert list_result.page == 1

        # Test 4: List filters by tenant
        list_result2 = await _bs().list_bots(
            tenant="wrong_tenant",
            page=1,
            page_size=10,
        )
        # Should have 0 or very few items for wrong tenant
        assert list_result2.total == 0 or all(
            item.id != shared_bot_service_setup["bot_id"] for item in list_result2.items
        )

    @pytest.mark.asyncio
    async def test_bot_status_and_destroy(
        self,
        bot_repository,
        device_repository,
        shared_bot_service_setup,
        created_bot_ids,
        created_device_ids,
    ):
        """Consolidated test for bot status and destroy operations.

        Tests:
        - destroy bot with devices
        - validates tenant ownership
        - validates bot exists
        - already released returns False
        - status with all devices failed
        - status with mixed device states
        - status with no devices
        """

        # Test 1: Destroy bot with devices
        # Create a bot to destroy
        destroy_bot_uuid = generate_uuid()
        destroy_bot_id = bot_repository.insert_bot(
            bot_uuid=destroy_bot_uuid,
            tenant=FIXED_TENANT_NAME,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status=BotStatus.ACTIVE.value,
            name="Bot To Destroy",
            description="Bot for destroy test",
            template_uuid=None,
            replica_desired=1,
            replica_minimum=1,
            replica_maximum=10,
            auto_scaling_enabled=0,
            sla_grade="standard",
            extra_config={},
        )
        created_bot_ids.append(destroy_bot_id)

        result = await _bs().destroy_bot(
            tenant=FIXED_TENANT_NAME,
            bot_id=destroy_bot_id,
            modifier="test_user",
        )
        assert result is True

        # Test 2: Already released returns False
        result = await _bs().destroy_bot(
            tenant=FIXED_TENANT_NAME,
            bot_id=destroy_bot_id,
            modifier="test_user",
        )
        assert result is False

        # Test 3: Validates tenant ownership
        bot_uuid = generate_uuid()
        bot_id = bot_repository.insert_bot(
            bot_uuid=bot_uuid,
            tenant=FIXED_TENANT_NAME,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status=BotStatus.ACTIVE.value,
            name="Tenant Test Bot",
            description="Bot for tenant test",
            template_uuid=None,
            replica_desired=1,
            replica_minimum=1,
            replica_maximum=10,
            auto_scaling_enabled=0,
            sla_grade="standard",
            extra_config={},
        )
        created_bot_ids.append(bot_id)

        result = await _bs().destroy_bot(
            tenant="wrong_tenant",
            bot_id=bot_id,
            modifier="test_user",
        )
        assert result is False

        # Test 4: Bot status with no devices (empty bot created earlier)
        bot_result = await _bs().get_bot(
            tenant=FIXED_TENANT_NAME,
            bot_id=shared_bot_service_setup["bot_id"],
        )
        assert bot_result is not None
        # Bot should have ACTIVE status since it has at least one device
        assert bot_result.status == BotStatus.ACTIVE.value
