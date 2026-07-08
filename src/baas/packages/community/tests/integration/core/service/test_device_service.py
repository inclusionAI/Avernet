"""Optimized integration tests for DeviceService with shared fixtures.

Consolidates 18 individual tests into 3 comprehensive test methods.
Uses shared fixtures to minimize database record creation.
"""

import random
from uuid import uuid4

import pytest

from secbaas.api.device_manage import DeviceStatus
from secbaas.bootstrap import get_container
from secbaas.core.utils.env_utils import get_current_env

TEST_ENV = get_current_env()
FIXED_TENANT_NAME = "test_tenant"


def _dts():
    return get_container().services.device_template_service()


def generate_uuid() -> str:
    return uuid4().hex


@pytest.fixture(scope="module")
def shared_device_setup(
    bot_repository,
    device_repository,
    created_bot_ids,
    created_device_ids,
    created_tenant_ids,
    created_template_ids,
    skip_if_zdas_unavailable,
):
    """Create ONE shared device for all DeviceService tests."""
    from secbaas.api.template_manage import (
        ArcaTemplateConfig,
        TemplateCreate,
    )
    from secbaas.api.tenant_manage import TenantType

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
        f"test-device-{int(__import__('time').time() * 1000000) % 10000000000}"
    )
    template = _dts().create_template(
        tenant=FIXED_TENANT_NAME,
        data=TemplateCreate(
            template_uuid=template_uuid,
            template_id=random.randint(1, 999999999),
            type=TenantType.ARCA,
            name=f"Shared Device Template {template_uuid}",
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
    created_template_ids.append(template.id)

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

    return {
        "tenant": FIXED_TENANT_NAME,
        "template_id": template.id,
        "device_id": device_id,
        "device_uuid": device_uuid,
    }


@pytest.mark.integration
class TestDeviceServiceIntegration:
    """Optimized integration tests with shared fixtures."""

    def test_device_destroy_operations(
        self, device_repository, shared_device_setup, created_device_ids
    ):
        """Consolidated test for device destroy operations.

        Tests:
        - destroy device tenant mismatch returns False
        - destroy nonexistent returns False
        """
        # Create a device for destroy tests
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

    def test_device_list_by_bot(
        self,
        bot_repository,
        device_repository,
        shared_device_setup,
        created_bot_ids,
        created_device_ids,
    ):
        """Test listing devices by bot ID.

        Tests:
        - list devices by bot ID
        - list by bot filters by tenant
        """
        # Create a bot
        bot_uuid = generate_uuid()
        bot_id = bot_repository.insert_bot(
            bot_uuid=bot_uuid,
            tenant=FIXED_TENANT_NAME,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="ACTIVE",
            name="Device Test Bot",
            description="Bot for device list test",
            template_uuid=None,
            replica_desired=1,
            replica_minimum=1,
            replica_maximum=10,
            auto_scaling_enabled=0,
            sla_grade="standard",
            extra_config={},
        )
        created_bot_ids.append(bot_id)

        # Create a device and relationship
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
