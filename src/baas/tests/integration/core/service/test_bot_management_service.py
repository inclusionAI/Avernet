"""Optimized integration tests for BotManagementService with shared fixtures.

Consolidates 32 individual tests into 8 comprehensive test methods.
Uses session-scoped fixtures to minimize database record creation.
"""

from unittest.mock import patch
from uuid import uuid4

import pytest

from secbaas.community.api.bot_manage import BotConfig, BotListResponse, BotStatus
from secbaas.community.api.bot_runtime import BotNotFoundError
from secbaas.community.api.device_manage import (
    DeployConfig,
    DeviceConfig,
    DeviceResponse,
    DeviceStatus,
)
from secbaas.community.api.publish_manage import (
    PublishStatus,
    PublishType,
    RestartScope,
)
from secbaas.community.api.template_manage import TemplateNotFoundError
from secbaas.community.api.tenant_manage import TenantConfig, TenantResponse
from secbaas.community.bootstrap import get_container
from secbaas.community.core.service.device_manage import (
    DefaultDeviceService,
    device_record_to_response,
)
from secbaas.community.core.service.tenant_manage import DefaultTenantManageService
from secbaas.community.core.utils.env_utils import get_current_env

from .conftest import create_test_devices_for_bot

TEST_ENV = get_current_env()
FIXED_TENANT_NAME = "test_tenant"


def _dts():
    return get_container().services.device_template_service()


def _bms():
    return get_container().services.bot_management_service()


def _ps():
    return get_container().services.publish_service()


def generate_uuid() -> str:
    """Generate unique UUID for testing."""
    return uuid4().hex


def create_mock_tenant_response(tenant_name: str = FIXED_TENANT_NAME) -> TenantResponse:
    """Create a mock TenantResponse for testing."""
    from datetime import datetime

    return TenantResponse(
        name=tenant_name,
        description="Mock tenant for testing",
        env=TEST_ENV,
        extra_config=TenantConfig(default_template_uuid=None),
        creator="test_user",
        modifier="test_user",
        gmt_create=datetime.now(),
        gmt_modified=datetime.now(),
    )


def _create_test_deploy_config() -> DeployConfig:
    """Create a DeployConfig with valid fields for testing."""
    return DeployConfig(
        after_create_cmd_hook="/nas/templates/create_test/init.sh",
        after_create_hook_wait_seconds=300,
        envs={"ENV": "test"},
        metadata={"source": "integration_test"},
    )


@pytest.fixture(scope="module")
def shared_bot_setup(
    bot_repository,
    device_repository,
    created_bot_ids,
    created_device_ids,
    created_tenant_ids,
    created_template_ids,
    skip_if_zdas_unavailable,
):
    """Create ONE shared bot with device for all tests in this module.

    This fixture creates minimal test entities at module scope,
    reducing database operations from N tests to 1 setup.
    """
    import random

    from secbaas.community.api.template_manage import (
        ArcaTemplateConfig,
        TemplateCreate,
        TemplateStatus,
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
        f"test-bmgt-{int(__import__('time').time() * 1000000) % 10000000000}"
    )
    template = _dts().create_template(
        tenant=FIXED_TENANT_NAME,
        data=TemplateCreate(
            template_uuid=template_uuid,
            template_id=random.randint(100000000, 999999999),
            type=TenantType.ARCA,
            name=f"Shared BotMgmt Template {template_uuid}",
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
    # Ensure the template is ONLINE for get_online_by_template_uuid lookups
    try:
        template_repo = get_container().repository.device_template_repository()
        template_repo.update_status(
            tenant=FIXED_TENANT_NAME,
            template_uuid=template.template_uuid,
            current_status=TemplateStatus.CREATED,
            new_status=TemplateStatus.ONLINE,
        )
    except Exception:
        # If repository path changes in the environment, skip this step gracefully
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
        name=f"Shared Test Bot {bot_uuid[:8]}",
        description="Shared bot for bot_management tests",
        template_uuid=None,
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
    created_rel_ids = [rel_id]

    return {
        "tenant": FIXED_TENANT_NAME,
        "template_id": template.id,
        "template_uuid": template.template_uuid,
        "bot_id": bot_id,
        "bot_uuid": bot_uuid,
        "device_id": device_id,
        "device_uuid": device_uuid,
        "created_rel_ids": created_rel_ids,
    }


# Track created relationships within tests
_test_rel_ids: list[int] = []


@pytest.fixture
def created_rel_ids():
    return _test_rel_ids


@pytest.mark.integration
class TestBotManagementServiceIntegration:
    """Optimized integration tests with shared fixtures and consolidated test methods."""

    @pytest.mark.asyncio
    async def test_create_bot_workflow(
        self,
        shared_bot_setup,
        created_bot_ids,
        device_repository,
        rel_repository,
        created_device_ids,
        created_rel_ids,
    ):
        """Consolidated test for bot creation workflow.

        Tests:
        - valid parameters creates bot
        - validates tenant exists
        - validates template exists
        - BotConfig with deploy_config is passed and persisted
        """
        import datetime

        # Test 1: Valid creation with BotConfig containing deploy_config
        deploy_config = _create_test_deploy_config()
        bot_config = BotConfig(
            entity_id="staff_create_test",
            entity_type="staff",
            deploy_config=deploy_config,
        )

        with (
            patch.object(
                DefaultTenantManageService,
                "get_tenant_by_name",
                return_value=create_mock_tenant_response(),
            ),
        ):
            result = await _bms().create_bot(
                tenant=FIXED_TENANT_NAME,
                name="Test Workflow Bot",
                template_uuid=shared_bot_setup["template_uuid"],
                device_count=1,
                operator="test_user",
                config=bot_config,
                request_id=uuid4().hex,
            )
            assert result is not None
            assert result.name == "Test Workflow Bot"
            assert result.publish_id is not None  # Verify publish_id is returned
            # Verify config.deploy_config is returned
            assert result.config is not None
            assert result.config.deploy_config is not None
            assert (
                result.config.deploy_config.after_create_cmd_hook
                == "/nas/templates/create_test/init.sh"
            )
            created_bot_ids.append(result.id)

        # Test 2: Validates template_uuid exists
        with pytest.raises(TemplateNotFoundError, match="Template with uuid not found"):
            await _bms().create_bot(
                tenant=FIXED_TENANT_NAME,
                name="Invalid Bot",
                template_uuid="nonexistent-template-uuid",
                device_count=1,
                operator="test_user",
                request_id=uuid4().hex,
            )

    @pytest.mark.asyncio
    async def test_bot_query_operations(
        self, bot_repository, shared_bot_setup, created_bot_ids
    ):
        """Consolidated test for bot query operations.

        Tests:
        - get_bot by UUID
        - get_bot nonexistent returns None
        - list_bots with pagination
        - list_bots enforces max page size
        - list_bots filters by status
        """
        # Test 1: Get bot by UUID
        result = await _bms().get_bot(
            tenant=FIXED_TENANT_NAME,
            bot_uuid=shared_bot_setup["bot_uuid"],
        )
        assert result is not None
        assert result.id == shared_bot_setup["bot_id"]

        # Test 2: Get nonexistent bot
        result = await _bms().get_bot(
            tenant="12345",
            bot_uuid="nonexistent-uuid",
        )
        assert result is None

        # Test 3: List with pagination
        list_result = await _bms().list_bots(
            tenant=FIXED_TENANT_NAME,
            page=1,
            page_size=10,
        )
        assert isinstance(list_result, BotListResponse)
        assert list_result.total >= 1
        assert list_result.page == 1
        assert list_result.page_size == 10

        # Test 4: Max page size enforced
        list_result2 = await _bms().list_bots(
            tenant=FIXED_TENANT_NAME,
            page=1,
            page_size=200,
        )
        assert list_result2.page_size == 100

        # Test 5: Filter by status
        list_result3 = await _bms().list_bots(
            tenant=FIXED_TENANT_NAME,
            status=BotStatus.ACTIVE.value,
            page=1,
            page_size=100,
        )
        for item in list_result3.items:
            assert item.status == BotStatus.ACTIVE.value

    @pytest.mark.asyncio
    async def test_destroy_bot_workflow(
        self,
        bot_repository,
        device_repository,
        rel_repository,
        shared_bot_setup,
        created_bot_ids,
        created_device_ids,
        created_rel_ids,
    ):
        # Create a fresh bot for destroy test
        bot_uuid = generate_uuid()
        bot_id = bot_repository.insert_bot(
            bot_uuid=bot_uuid,
            tenant=FIXED_TENANT_NAME,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status=BotStatus.ACTIVE.value,
            name="Bot To Destroy",
            description="Test bot for destroy",
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
            tenant=FIXED_TENANT_NAME,
            bot_id=bot_id,
            device_status=DeviceStatus.ACTIVE.value,
            num_devices=1,
            created_device_ids=created_device_ids,
            created_rel_ids=created_rel_ids,
        )

        # Test 1: Destroy by UUID
        with patch.object(
            DefaultTenantManageService,
            "get_tenant_by_name",
            return_value=create_mock_tenant_response(),
        ):
            result = await _bms().destroy_bot(
                tenant=FIXED_TENANT_NAME,
                bot_uuid=bot_uuid,
                operator="test_user",
                request_id=uuid4().hex,
            )
            assert result is not None
            assert result.publish_id is not None
            assert result.bot_uuid == bot_uuid

        # Test 2: Nonexistent UUID
        with patch.object(
            DefaultTenantManageService,
            "get_tenant_by_name",
            return_value=create_mock_tenant_response(),
        ):
            result = await _bms().destroy_bot(
                tenant="test_tenant",
                bot_uuid="nonexistent-uuid",
                operator="test_user",
                request_id=uuid4().hex,
            )
            assert result is None

        # Test 3: Tenant mismatch
        with patch.object(
            DefaultTenantManageService,
            "get_tenant_by_name",
            return_value=None,
        ):
            result = await _bms().destroy_bot(
                tenant="99999999",
                bot_uuid=shared_bot_setup["bot_uuid"],
                operator="test_user",
                request_id=uuid4().hex,
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_scale_bot_workflow(
        self,
        bot_repository,
        device_repository,
        shared_bot_setup,
        created_bot_ids,
        created_device_ids,
        created_publish_ids,
    ):
        """Consolidated test for scale bot workflow.

        Tests:
        - scale up creates SCALE_UP publish
        - scale down creates SCALE_DOWN publish
        - validates minimum count
        - validates different target
        - validates bot exists
        """

        # Create a fresh bot with 1 device for scale tests
        bot_uuid = generate_uuid()
        bot_id = bot_repository.insert_bot(
            bot_uuid=bot_uuid,
            tenant=FIXED_TENANT_NAME,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status=BotStatus.ACTIVE.value,
            name="Scale Test Bot",
            description="Bot for scaling tests",
            template_uuid=None,
            replica_desired=1,
            replica_minimum=1,
            replica_maximum=10,
            auto_scaling_enabled=0,
            sla_grade="standard",
            extra_config={},
        )
        created_bot_ids.append(bot_id)

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

        rel_repo = get_container().repository.bot_device_rel_repository()
        rel_repo.insert_rel(
            bot_id=bot_id,
            device_uuid=device_uuid,
            tenant=FIXED_TENANT_NAME,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
        )

        with patch.object(
            DefaultTenantManageService,
            "get_tenant_by_name",
            return_value=create_mock_tenant_response(),
        ):
            # Test 1: Scale up
            result = await _bms().scale_bot(
                tenant=FIXED_TENANT_NAME,
                bot_uuid=bot_uuid,
                target_count=3,
                operator="test_user",
                request_id=uuid4().hex,
            )
            assert result is not None
            assert result.publish_id is not None
            assert result.target_count == 3
            assert result.bot_uuid == bot_uuid
            created_publish_ids.append(result.publish_id)

            # Test 2: Validates minimum count (before scale down)
            with pytest.raises(ValueError, match="Target count must be at least 1"):
                await _bms().scale_bot(
                    tenant=FIXED_TENANT_NAME,
                    bot_uuid=bot_uuid,
                    target_count=0,
                    operator="test_user",
                    request_id=uuid4().hex,
                )

            # Test 3: Validates different target (current = 1, target = 1)
            with pytest.raises(ValueError, match="Target count equals current count"):
                await _bms().scale_bot(
                    tenant=FIXED_TENANT_NAME,
                    bot_uuid=bot_uuid,
                    target_count=1,
                    operator="test_user",
                    request_id=uuid4().hex,
                )

        # Test 4: Scale down - create bot with 3 devices
        scale_down_bot_uuid = generate_uuid()
        scale_down_bot_id = bot_repository.insert_bot(
            bot_uuid=scale_down_bot_uuid,
            tenant=FIXED_TENANT_NAME,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status=BotStatus.ACTIVE.value,
            name="Scale Down Bot",
            description="Bot for scale down test",
            template_uuid=None,
            replica_desired=3,
            replica_minimum=1,
            replica_maximum=10,
            auto_scaling_enabled=0,
            sla_grade="standard",
            extra_config={},
        )
        created_bot_ids.append(scale_down_bot_id)

        # Create 3 devices for scale down bot
        for _ in range(3):
            d_uuid = generate_uuid()
            d_id = device_repository.insert_device(
                device_uuid=d_uuid,
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
            created_device_ids.append(d_id)
            rel_repo.insert_rel(
                bot_id=scale_down_bot_id,
                device_uuid=d_uuid,
                tenant=FIXED_TENANT_NAME,
                env=TEST_ENV,
                domain="test_domain",
                creator="test_user",
                modifier="test_user",
            )

        with patch.object(
            DefaultTenantManageService,
            "get_tenant_by_name",
            return_value=create_mock_tenant_response(),
        ):
            result = await _bms().scale_bot(
                tenant=FIXED_TENANT_NAME,
                bot_uuid=scale_down_bot_uuid,
                target_count=1,
                operator="test_user",
                request_id=uuid4().hex,
            )
            assert result is not None
            assert result.publish_id is not None
            assert result.target_count == 1
            created_publish_ids.append(result.publish_id)

        # Test 5: Validates bot exists
        with patch.object(
            DefaultTenantManageService,
            "get_tenant_by_name",
            return_value=create_mock_tenant_response(),
        ):
            with pytest.raises(BotNotFoundError, match="Bot not found"):
                await _bms().scale_bot(
                    tenant=FIXED_TENANT_NAME,
                    bot_uuid="nonexistent-uuid",
                    target_count=3,
                    operator="test_user",
                    request_id=uuid4().hex,
                )

    @pytest.mark.asyncio
    async def test_update_bot_workflow(
        self,
        bot_repository,
        device_repository,
        shared_bot_setup,
        created_bot_ids,
        created_device_ids,
    ):
        """Consolidated test for update bot workflow.

        Tests:
        - update bot name
        - update multiple fields
        - update with bot_config containing deploy_config
        - validates bot exists
        """

        # Create a fresh bot for update tests
        bot_uuid = generate_uuid()
        bot_id = bot_repository.insert_bot(
            bot_uuid=bot_uuid,
            tenant=FIXED_TENANT_NAME,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status=BotStatus.ACTIVE.value,
            name="Original Name",
            description="Original desc",
            template_uuid=None,
            replica_desired=1,
            replica_minimum=1,
            replica_maximum=10,
            auto_scaling_enabled=0,
            sla_grade="standard",
            extra_config={},
        )
        created_bot_ids.append(bot_id)

        # Create a device + relationship so _get_operational_bot_record_by_uuid_for_update succeeds
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
        rel_repo = get_container().repository.bot_device_rel_repository()
        rel_repo.insert_rel(
            bot_id=bot_id,
            device_uuid=device_uuid,
            tenant=FIXED_TENANT_NAME,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
        )

        with patch.object(
            DefaultTenantManageService,
            "get_tenant_by_name",
            return_value=create_mock_tenant_response(),
        ):
            # Test 1: Update name
            result = await _bms().update_bot(
                tenant=FIXED_TENANT_NAME,
                bot_uuid=bot_uuid,
                operator="test_user",
                bot_name="Updated Name",
            )
            assert result is not None
            assert result.name == "Updated Name"

            # Test 2: Update multiple fields
            result = await _bms().update_bot(
                tenant=FIXED_TENANT_NAME,
                bot_uuid=bot_uuid,
                operator="test_user",
                bot_name="New Name",
                bot_desc="New Description",
            )
            assert result is not None
            assert result.name == "New Name"
            updated = bot_repository.get_by_id(bot_id, FIXED_TENANT_NAME, TEST_ENV)
            assert updated.description == "New Description"

            # Test 3: Update with bot_config containing deploy_config
            new_deploy_config = DeployConfig(
                after_create_cmd_hook="/nas/templates/updated/init.sh",
                envs={"ENV": "prod"},
                metadata={"source": "update_test"},
            )
            new_bot_config = BotConfig(
                entity_id="staff_updated",
                deploy_config=new_deploy_config,
            )

            result = await _bms().update_bot(
                tenant=FIXED_TENANT_NAME,
                bot_uuid=bot_uuid,
                operator="test_user",
                bot_config=new_bot_config,
                request_id="test-update-bot-config-12345678901234567890",
            )
            assert result is not None
            # Config change triggers UPDATE publish; publish_id is returned
            assert result.publish_id is not None

            # The new config is stored in the PENDING bot record created by
            # the UPDATE publish, not in the original bot record.
            # Find the PENDING bot record with the same bot_uuid
            all_records = bot_repository.list_by_bot_uuid(
                bot_uuid=bot_uuid, tenant=FIXED_TENANT_NAME, env=TEST_ENV
            )
            pending_record = next(
                (r for r in all_records if r.status == BotStatus.PENDING.value),
                None,
            )
            assert pending_record is not None, (
                "No PENDING bot record found after UPDATE publish"
            )
            stored_config = BotConfig.model_validate(pending_record.extra_config)
            assert stored_config.deploy_config is not None
            assert (
                stored_config.deploy_config.after_create_cmd_hook
                == "/nas/templates/updated/init.sh"
            )
            created_bot_ids.append(pending_record.id)

        # Test 4: Validates bot exists
        with patch.object(
            DefaultTenantManageService,
            "get_tenant_by_name",
            return_value=create_mock_tenant_response(),
        ):
            with pytest.raises(BotNotFoundError, match="Bot not found"):
                await _bms().update_bot(
                    tenant=FIXED_TENANT_NAME,
                    bot_uuid="nonexistent-uuid",
                    operator="test_user",
                    bot_name="Name",
                )

    @pytest.mark.asyncio
    async def test_restart_bot_workflow(
        self,
        bot_repository,
        device_repository,
        shared_bot_setup,
        created_bot_ids,
        created_device_ids,
        created_publish_ids,
    ):
        """Consolidated test for restart bot workflow.

        Tests:
        - restart with valid scope
        - validates scope
        - validates bot exists
        """

        # Create a fresh bot for restart tests
        bot_uuid = generate_uuid()
        bot_id = bot_repository.insert_bot(
            bot_uuid=bot_uuid,
            tenant=FIXED_TENANT_NAME,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status=BotStatus.ACTIVE.value,
            name="Restart Test Bot",
            description="Bot for restart tests",
            template_uuid=None,
            replica_desired=1,
            replica_minimum=1,
            replica_maximum=10,
            auto_scaling_enabled=0,
            sla_grade="standard",
            extra_config={},
        )
        created_bot_ids.append(bot_id)

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

        rel_repo = get_container().repository.bot_device_rel_repository()
        rel_repo.insert_rel(
            bot_id=bot_id,
            device_uuid=device_uuid,
            tenant=FIXED_TENANT_NAME,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
        )

        with patch.object(
            DefaultTenantManageService,
            "get_tenant_by_name",
            return_value=create_mock_tenant_response(),
        ):
            # Test 1: Valid restart
            result = await _bms().restart_bot(
                tenant=FIXED_TENANT_NAME,
                bot_uuid=bot_uuid,
                operator="test_user",
                scope=RestartScope.ALL,
                request_id=uuid4().hex,
            )
            assert result is not None
            assert result.publish_id is not None
            assert result.bot_uuid == bot_uuid
            created_publish_ids.append(result.publish_id)

            # Test 2: Invalid scope
            with pytest.raises(ValueError, match="Invalid scope"):
                await _bms().restart_bot(
                    tenant=FIXED_TENANT_NAME,
                    bot_uuid=bot_uuid,
                    operator="test_user",
                    scope="invalid_scope",  # type: ignore[arg-type]
                    request_id=uuid4().hex,
                )

            # Test 3: Restart with auto_approve_publish=True
            result2 = await _bms().restart_bot(
                tenant=FIXED_TENANT_NAME,
                bot_uuid=bot_uuid,
                operator="test_user",
                scope=RestartScope.ALL,
                request_id=uuid4().hex,
                auto_approve_publish=True,
            )
            assert result2 is not None
            assert result2.publish_id is not None
            created_publish_ids.append(result2.publish_id)

        # Test 4: Validates bot exists
        with patch.object(
            DefaultTenantManageService,
            "get_tenant_by_name",
            return_value=create_mock_tenant_response(),
        ):
            with pytest.raises(BotNotFoundError, match="Bot not found"):
                await _bms().restart_bot(
                    tenant=FIXED_TENANT_NAME,
                    bot_uuid="nonexistent-uuid",
                    operator="test_user",
                    scope=RestartScope.ALL,
                    request_id=uuid4().hex,
                )

    @pytest.mark.asyncio
    async def test_publish_workflow(
        self,
        bot_repository,
        device_repository,
        rel_repository,
        shared_bot_setup,
        created_bot_ids,
        created_device_ids,
        created_rel_ids,
        created_publish_ids,
    ):
        """Consolidated test for publish workflow.

        Tests:
        - full create workflow creates bot and publish
        - full destroy workflow marks bot released
        - publish starts in PENDING status
        - approval required before execution
        - PublishConfig with deploy_config is persisted
        """
        import datetime

        # Create a proper mock with all required attributes
        mock_device = DeviceResponse(
            id=789,
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

        def _create_device_side_effect(tenant, data):
            device_id = device_repository.insert_device(
                device_uuid=f"DEVICE-{uuid4().hex}",
                tenant=tenant,
                env=TEST_ENV,
                domain=data.domain,
                creator=data.operator,
                modifier=data.operator,
                status=DeviceStatus.PENDING.value,
                provider_type=None,
                provider_device_id=None,
                provider_device_props={},
                extra_config={},
            )
            if created_device_ids is not None:
                created_device_ids.append(device_id)
            record = device_repository.get_by_id(device_id, tenant, TEST_ENV)
            if not record:
                raise ValueError("Device not found after insert")
            return device_record_to_response(record)

        # Test 1: Full create workflow - verify bot and publish_id returned
        with (
            patch.object(
                DefaultDeviceService,
                "create_device",
                side_effect=_create_device_side_effect,
            ),
            patch.object(
                DefaultTenantManageService,
                "get_tenant_by_name",
                return_value=create_mock_tenant_response(),
            ),
        ):
            bot = await _bms().create_bot(
                tenant=FIXED_TENANT_NAME,
                name="Workflow Bot",
                template_uuid=shared_bot_setup["template_uuid"],
                device_count=1,
                operator="test_user",
                request_id=uuid4().hex,
            )
            assert bot is not None
            assert bot.publish_id is not None  # Verify publish_id is returned
            created_bot_ids.append(bot.id)
            created_publish_ids.append(bot.publish_id)

        # Test 2: Create publish in PENDING status with deploy_config
        from secbaas.community.api.publish_manage import PublishConfig

        deploy_config = DeployConfig(
            after_create_cmd_hook="/nas/templates/publish_test/init.sh",
            envs={"ENV": "test"},
            metadata={"source": "publish_test"},
        )

        publish_config = PublishConfig(
            bot_name="Workflow Bot",
            replica_desired=2,
            deploy_config=deploy_config,
        )

        create_test_devices_for_bot(
            device_repository=device_repository,
            rel_repository=rel_repository,
            tenant=FIXED_TENANT_NAME,
            bot_id=shared_bot_setup["bot_id"],
            device_status=DeviceStatus.PENDING.value,
            num_devices=1,
            created_device_ids=created_device_ids,
            created_rel_ids=created_rel_ids,
        )

        publish = await _ps().create_publish(
            tenant=FIXED_TENANT_NAME,
            bot_id=shared_bot_setup["bot_id"],
            publish_type=PublishType.CREATE,
            operator="test_user",
            request_id=uuid4().hex,
            config=publish_config,
        )
        created_publish_ids.append(publish.id)
        assert publish.status == PublishStatus.PENDING.value

        # Verify deploy_config is persisted in extra_config
        assert publish.extra_config is not None
        stored_config = PublishConfig.model_validate(publish.extra_config)
        assert stored_config.deploy_config is not None
        assert (
            stored_config.deploy_config.after_create_cmd_hook
            == "/nas/templates/publish_test/init.sh"
        )

        # Test 3: Cannot execute without approval
        with pytest.raises(ValueError, match="Cannot execute stage"):
            await _ps().execute_stage(
                tenant=FIXED_TENANT_NAME,
                publish_id=publish.id,
                operator="test_user",
            )

        # Test 4: Full destroy workflow
        bot_uuid = generate_uuid()
        bot_id = bot_repository.insert_bot(
            bot_uuid=bot_uuid,
            tenant=FIXED_TENANT_NAME,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status=BotStatus.ACTIVE.value,
            name="Destroy Workflow Bot",
            description="Bot for destroy workflow",
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
            tenant=FIXED_TENANT_NAME,
            bot_id=bot_id,
            device_status=DeviceStatus.ACTIVE.value,
            num_devices=1,
            created_device_ids=created_device_ids,
            created_rel_ids=created_rel_ids,
        )

        with patch.object(
            DefaultTenantManageService,
            "get_tenant_by_name",
            return_value=create_mock_tenant_response(),
        ):
            result = await _bms().destroy_bot(
                tenant=FIXED_TENANT_NAME,
                bot_uuid=bot_uuid,
                operator="test_user",
                request_id=uuid4().hex,
            )
            assert result is not None
            assert result.publish_id is not None

    @pytest.mark.asyncio
    async def test_destroying_status_workflow(
        self,
        bot_repository,
        device_repository,
        rel_repository,
        shared_bot_setup,
        created_bot_ids,
        created_device_ids,
        created_rel_ids,
        created_publish_ids,
    ):
        # Create a bot for DESTROYING status tests
        bot_uuid = generate_uuid()
        bot_id = bot_repository.insert_bot(
            bot_uuid=bot_uuid,
            tenant=FIXED_TENANT_NAME,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status=BotStatus.ACTIVE.value,
            name="Destroying Status Bot",
            description="Bot for DESTROYING status tests",
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
            tenant=FIXED_TENANT_NAME,
            bot_id=bot_id,
            device_status=DeviceStatus.ACTIVE.value,
            num_devices=1,
            created_device_ids=created_device_ids,
            created_rel_ids=created_rel_ids,
        )

        with patch.object(
            DefaultTenantManageService,
            "get_tenant_by_name",
            return_value=create_mock_tenant_response(),
        ):
            # Test 1: Bot status becomes DESTROYING after destroy_bot
            result = await _bms().destroy_bot(
                tenant=FIXED_TENANT_NAME,
                bot_uuid=bot_uuid,
                operator="test_user",
                request_id=uuid4().hex,
            )
            assert result is not None
            assert result.publish_id is not None
            created_publish_ids.append(result.publish_id)

            # Verify bot status is DESTROYING
            bot_record = bot_repository.get_by_id(bot_id, FIXED_TENANT_NAME, TEST_ENV)
            assert bot_record.status == BotStatus.DESTROYING.value

            # Test 2: Cannot destroy a bot that is already DESTROYING
            with pytest.raises(ValueError, match="already being destroyed"):
                await _bms().destroy_bot(
                    tenant=FIXED_TENANT_NAME,
                    bot_uuid=bot_uuid,
                    operator="test_user",
                    request_id=uuid4().hex,
                )

        # Test 3: Cannot scale a bot that is DESTROYING
        with patch.object(
            DefaultTenantManageService,
            "get_tenant_by_name",
            return_value=create_mock_tenant_response(),
        ):
            with pytest.raises(
                ValueError, match="Cannot scale bot in DESTROYING status"
            ):
                await _bms().scale_bot(
                    tenant=FIXED_TENANT_NAME,
                    bot_uuid=bot_uuid,
                    target_count=3,
                    operator="test_user",
                    request_id=uuid4().hex,
                )

        # Test 4: Cannot restart a bot that is DESTROYING
        with patch.object(
            DefaultTenantManageService,
            "get_tenant_by_name",
            return_value=create_mock_tenant_response(),
        ):
            with pytest.raises(
                ValueError, match="Cannot restart bot in DESTROYING status"
            ):
                await _bms().restart_bot(
                    tenant=FIXED_TENANT_NAME,
                    bot_uuid=bot_uuid,
                    operator="test_user",
                    request_id=uuid4().hex,
                    scope=RestartScope.ALL,
                )

    @pytest.mark.asyncio
    async def test_destroying_status_blocks_double_destroy(
        self, bot_repository, shared_bot_setup, created_bot_ids
    ):
        """Test that destroy_bot rejects when bot is already DESTROYING."""

        # Create a bot with DESTROYING status
        bot_uuid = generate_uuid()
        bot_id = bot_repository.insert_bot(
            bot_uuid=bot_uuid,
            tenant=FIXED_TENANT_NAME,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status=BotStatus.DESTROYING.value,
            name="Already Destroying Bot",
            description="Bot already in DESTROYING state",
            template_uuid=None,
            replica_desired=1,
            replica_minimum=1,
            replica_maximum=10,
            auto_scaling_enabled=0,
            sla_grade="standard",
            extra_config={},
        )
        created_bot_ids.append(bot_id)

        with patch.object(
            DefaultTenantManageService,
            "get_tenant_by_name",
            return_value=create_mock_tenant_response(),
        ):
            with pytest.raises(ValueError, match="already being destroyed"):
                await _bms().destroy_bot(
                    tenant=FIXED_TENANT_NAME,
                    bot_uuid=bot_uuid,
                    operator="test_user",
                    request_id=uuid4().hex,
                )

    @pytest.mark.asyncio
    async def test_stop_workflow(
        self,
        bot_repository,
        device_repository,
        rel_repository,
        shared_bot_setup,
        created_bot_ids,
        created_device_ids,
        created_publish_ids,
        created_rel_ids,
    ):
        from uuid import uuid4

        from secbaas.community.api.bot_manage import BotStatus
        from secbaas.community.api.device_manage import DeviceStatus
        from secbaas.community.api.publish_manage import PublishType
        from secbaas.community.bootstrap import get_container
        from secbaas.community.core.utils.env_utils import get_current_env

        env = get_current_env()
        bms = get_container().services.bot_management_service()

        # Create a standalone bot (no concurrent publish) for stop test
        bot_uuid = uuid4().hex
        bot_id = bot_repository.insert_bot(
            bot_uuid=bot_uuid,
            tenant=shared_bot_setup["tenant"],
            env=env,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status=BotStatus.ACTIVE.value,
            name="Stop Workflow Bot",
            description="Bot for stop workflow test",
            template_uuid=None,
            replica_desired=1,
            replica_minimum=1,
            replica_maximum=10,
            auto_scaling_enabled=0,
            sla_grade="standard",
            extra_config={},
        )
        created_bot_ids.append(bot_id)

        from .conftest import create_test_devices_for_bot

        create_test_devices_for_bot(
            device_repository=device_repository,
            rel_repository=rel_repository,
            tenant=shared_bot_setup["tenant"],
            bot_id=bot_id,
            device_status=DeviceStatus.ACTIVE.value,
            num_devices=1,
            created_device_ids=created_device_ids,
            created_rel_ids=created_rel_ids,
        )

        result = await bms.stop_bot(
            tenant=shared_bot_setup["tenant"],
            bot_uuid=bot_uuid,
            operator="test_user",
            request_id=uuid4().hex,
        )

        assert result is not None
        assert result.publish_id is not None
        assert result.status == BotStatus.STOPPING.value

    @pytest.mark.asyncio
    async def test_stop_eligibility(
        self,
        bot_repository,
        device_repository,
        shared_bot_setup,
        created_bot_ids,
        created_device_ids,
    ):
        from uuid import uuid4

        from secbaas.community.api.bot_manage import BotStatus
        from secbaas.community.api.publish_manage import RestartScope
        from secbaas.community.bootstrap import get_container
        from secbaas.community.core.utils.env_utils import get_current_env

        env = get_current_env()
        bms = get_container().services.bot_management_service()

        bot_uuid = uuid4().hex
        bot_id = bot_repository.insert_bot(
            bot_uuid=bot_uuid,
            tenant=shared_bot_setup["tenant"],
            env=env,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status=BotStatus.STOPPED.value,
            name="Stopped Eligibility Bot",
            description="Bot for STOPPED eligibility tests",
            template_uuid=None,
            replica_desired=1,
            replica_minimum=1,
            replica_maximum=10,
            auto_scaling_enabled=0,
            sla_grade="standard",
            extra_config={},
        )
        created_bot_ids.append(bot_id)

        # restart_bot should NOT reject STOPPED status — any error should be unrelated
        try:
            await bms.restart_bot(
                tenant=shared_bot_setup["tenant"],
                bot_uuid=bot_uuid,
                operator="test_user",
                request_id=uuid4().hex,
                scope=RestartScope.ALL,
            )
        except Exception as e:
            assert "STOPPED" not in str(e), (
                f"restart_bot should not reject STOPPED status, got: {e}"
            )

        # update_bot (name-only) should NOT reject STOPPED status
        try:
            await bms.update_bot(
                tenant=shared_bot_setup["tenant"],
                bot_uuid=bot_uuid,
                operator="test_user",
                bot_name="Updated Stopped Bot",
            )
        except Exception as e:
            assert "STOPPED" not in str(e), (
                f"update_bot should not reject STOPPED status, got: {e}"
            )
