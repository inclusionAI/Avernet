"""Integration tests for DefaultDeviceTemplateService with shared fixtures."""

import random
import time

import pytest

from secbaas.api.template_manage import TemplateStatus
from secbaas.bootstrap import get_container
from secbaas.core.utils.env_utils import get_current_env

TEST_ENV = get_current_env()
FIXED_TENANT_NAME = "test_tenant"


def _dts():
    return get_container().services.device_template_service()


def generate_template_uuid() -> str:
    return f"test-template-{int(time.time() * 1000000) % 10000000000}"


@pytest.fixture(scope="module")
def shared_template_setup(
    created_tenant_ids,
    skip_if_zdas_unavailable,
):
    """Create ONE shared template for all DeviceTemplateService tests."""
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
    template_uuid = generate_template_uuid()
    template = _dts().create_template(
        tenant=FIXED_TENANT_NAME,
        data=TemplateCreate(
            template_uuid=template_uuid,
            template_id=random.randint(1, 999999999),
            type=TenantType.ARCA,
            name=f"Shared Template {template_uuid}",
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
    # Mark template ONLINE for tests that require ONLINE lookups
    try:
        template_repo = get_container().repository.device_template_repository()
        template_repo.update_status(
            template_uuid=template.template_uuid,
            tenant=FIXED_TENANT_NAME,
            current_status=TemplateStatus.CREATED.value,
            new_status="ONLINE",
        )
    except Exception:
        pass

    return {
        "tenant": FIXED_TENANT_NAME,
        "template_id": template.id,
        "template_uuid": template.template_uuid,
    }


@pytest.mark.integration
class TestDeviceTemplateServiceIntegration:
    """Optimized integration tests for DefaultDeviceTemplateService."""

    def test_template_crud_operations(
        self, shared_template_setup, created_template_ids
    ):
        """Consolidated test for template CRUD operations.

        Tests:
        - create template with valid data
        - create duplicate UUID raises error
        - get by existing UUID
        - get by nonexistent UUID returns None
        - get by ID
        - update template with valid data
        - list templates with pagination
        - list filters by status
        """
        from secbaas.api.template_manage import (
            ArcaTemplateConfig,
            TemplateCreate,
            TemplateUpdate,
        )
        from secbaas.api.tenant_manage import TenantType

        # Test 1: Create template with valid data
        template_uuid = generate_template_uuid()
        created = _dts().create_template(
            tenant=FIXED_TENANT_NAME,
            data=TemplateCreate(
                template_uuid=template_uuid,
                template_id=random.randint(1, 999999999),
                type=TenantType.ARCA,
                name=f"Test Template {template_uuid}",
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
        assert created is not None
        assert created.template_uuid == template_uuid
        created_template_ids.append(created.id)

        # Test 2: Create duplicate template_id raises ValueError
        with pytest.raises(ValueError, match="already exists"):
            _dts().create_template(
                tenant=FIXED_TENANT_NAME,
                data=TemplateCreate(
                    template_uuid=generate_template_uuid(),
                    template_id=created.template_id,  # Same template_id as first template
                    type=TenantType.ARCA,
                    name="Duplicate",
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

        # Test 3: Get by existing UUID (after setting ONLINE)
        tmpl_repo = get_container().repository.device_template_repository()
        tmpl_repo.update_status(
            template_uuid=template_uuid,
            tenant=FIXED_TENANT_NAME,
            current_status=TemplateStatus.CREATED.value,
            new_status="ONLINE",
        )

        uuid_result = _dts().get_online_template_by_uuid(
            tenant=FIXED_TENANT_NAME, template_uuid=template_uuid
        )
        assert uuid_result is not None
        assert uuid_result.template_uuid == template_uuid
        # Test 4: Get by nonexistent UUID returns None
        none_result = _dts().get_online_template_by_uuid(
            tenant=FIXED_TENANT_NAME, template_uuid="nonexistent-uuid"
        )
        assert none_result is None

        # Test 5: Get by ID
        template_uuid_2 = generate_template_uuid()
        template_2 = _dts().create_template(
            tenant=FIXED_TENANT_NAME,
            data=TemplateCreate(
                template_uuid=template_uuid_2,
                template_id=random.randint(1, 999999999),
                type=TenantType.ARCA,
                name="Test 2",
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
        created_template_ids.append(template_2.id)

        result = _dts().get_by_template_id(
            template_id=template_2.template_id,
        )
        assert result is not None
        assert result.template_uuid == template_2.template_uuid

        # Test 6: Update template with valid data
        updated = _dts().update_template(
            tenant=FIXED_TENANT_NAME,
            template_uuid=template_2.template_uuid,
            status=TemplateStatus.CREATED,
            data=TemplateUpdate(
                name="Updated Name",
                operator="test_user",
                template_id=None,
                type=None,
                description=None,
                config=None,
            ),
        )
        assert updated is not None
        assert updated.name == "Updated Name"

        # Test 7: List templates with pagination
        listing = _dts().list_templates(
            tenant=FIXED_TENANT_NAME,
            page=1,
            page_size=10,
        )
        assert listing.total >= 2
        assert listing.page == 1

        # Test 8: List filters by status
        filtered = _dts().list_templates(
            tenant=FIXED_TENANT_NAME,
            status=TemplateStatus.CREATED,
            page=1,
            page_size=10,
        )
        for item in filtered.items:
            assert item.status == "CREATED"

    def test_template_status_operations(
        self, shared_template_setup, created_template_ids
    ):
        """Consolidated test for template status operations.

        Tests:
        - status transition created to audited
        - status transition audited to online
        - status transition online to offline
        - status transition offline to online
        - soft delete template
        - tenant isolation get template
        """
        from secbaas.api.template_manage import (
            ArcaTemplateConfig,
            TemplateCreate,
        )
        from secbaas.api.tenant_manage import TenantType

        # Create template for status tests
        template_uuid = generate_template_uuid()
        template = _dts().create_template(
            tenant=FIXED_TENANT_NAME,
            data=TemplateCreate(
                template_uuid=template_uuid,
                template_id=random.randint(1, 999999999),
                type=TenantType.ARCA,
                name="Status Test Template",
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

        # Test 1: Status transition created to audited
        result = _dts().update_status(
            tenant=FIXED_TENANT_NAME,
            template_uuid=template.template_uuid,
            current_status=TemplateStatus.CREATED,
            new_status=TemplateStatus.AUDITED,
        )
        assert result is not None
        assert result.status == "AUDITED"

        # Test 2: Status transition audited to online
        result = _dts().update_status(
            tenant=FIXED_TENANT_NAME,
            template_uuid=template.template_uuid,
            current_status=TemplateStatus.AUDITED,
            new_status=TemplateStatus.ONLINE,
        )
        assert result is not None
        assert result.status == "ONLINE"

        # Test 3: Status transition online to offline
        result = _dts().update_status(
            tenant=FIXED_TENANT_NAME,
            template_uuid=template.template_uuid,
            current_status=TemplateStatus.ONLINE,
            new_status=TemplateStatus.OFFLINE,
        )
        assert result is not None
        assert result.status == "OFFLINE"

        # Test 4: Status transition offline to online
        result = _dts().update_status(
            tenant=FIXED_TENANT_NAME,
            template_uuid=template.template_uuid,
            current_status=TemplateStatus.OFFLINE,
            new_status=TemplateStatus.ONLINE,
        )
        assert result is not None
        assert result.status == "ONLINE"

        # Test 5: Soft delete template
        soft_delete_result = _dts().soft_delete_template(
            tenant=FIXED_TENANT_NAME,
            template_uuid=template.template_uuid,
            status=TemplateStatus.ONLINE,
            operator="test_user",
        )
        assert soft_delete_result is True

        # Test 6: Tenant isolation - get with wrong tenant
        # Use the repository's get_by_template_uuid with CREATED since the template
        # was soft-deleted from ONLINE status and won't be found with ONLINE
        isolation_result = _dts().get_online_template_by_uuid(
            tenant="wrong_tenant", template_uuid=template.template_uuid
        )
        assert isolation_result is None

    def test_get_default_or_explicit_template(
        self, shared_template_setup, created_template_ids
    ):
        """Test two-tier template resolution strategy.

        Tests:
        - tier-1: explicit UUID resolves template
        - tier-2: default from tenant extra_config
        - tier-2: missing default_template_uuid raises ValueError
        """
        tenant = shared_template_setup["tenant"]
        shared_uuid = shared_template_setup["template_uuid"]

        # Test 1: Tier-1 - explicit UUID
        result = _dts().get_default_or_explicit_template(
            tenant=tenant, template_uuid=shared_uuid
        )
        assert result is not None
        assert result.template_uuid == shared_uuid

        # Test 2: Tier-1 - nonexistent UUID raises ValueError
        with pytest.raises(ValueError, match="Template not found"):
            _dts().get_default_or_explicit_template(
                tenant=tenant, template_uuid="nonexistent-uuid"
            )

        # Test 3: Tier-2 - default from tenant config
        # Set up tenant's extra_config with default_template_uuid pointing to shared template
        tenant_repo = get_container().repository.tenant_repository()
        tenant_repo.get_by_name(tenant, TEST_ENV)
        from secbaas.api.tenant_manage import TenantConfig

        tenant_repo.update_tenant(
            name=tenant,
            env=TEST_ENV,
            modifier="test_user",
            extra_config=TenantConfig(default_template_uuid=shared_uuid).model_dump(
                exclude_none=True
            ),
            description=None,
        )

        result = _dts().get_default_or_explicit_template(
            tenant=tenant, template_uuid=None
        )
        assert result is not None
        assert result.template_uuid == shared_uuid

        # Test 4: Tier-2 - missing config for tenant without default
        # Create a new tenant without default_template_uuid
        new_tenant_name = f"test_no_default_{int(time.time() * 1000000) % 10000000000}"
        tenant_repo.insert_tenant(
            name=new_tenant_name,
            env=TEST_ENV,
            creator="test_user",
            modifier="test_user",
        )
        with pytest.raises((ValueError, TypeError)):
            _dts().get_default_or_explicit_template(
                tenant=new_tenant_name, template_uuid=None
            )

    def test_list_online_templates(self, shared_template_setup, created_template_ids):
        """Test list_online_templates returns only ONLINE templates."""
        from secbaas.api.template_manage import (
            ArcaTemplateConfig,
            TemplateCreate,
        )
        from secbaas.api.tenant_manage import TenantType

        template_repo = get_container().repository.device_template_repository()
        tenant = shared_template_setup["tenant"]

        # Create templates in various statuses
        statuses = ["CREATED", "AUDITED", "ONLINE", "OFFLINE"]
        created_uuids = []
        for i, status in enumerate(statuses):
            t_uuid = generate_template_uuid()
            t = _dts().create_template(
                tenant=tenant,
                data=TemplateCreate(
                    template_uuid=t_uuid,
                    template_id=random.randint(1, 999999999),
                    type=TenantType.ARCA,
                    name=f"Status Test {status}",
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
            created_template_ids.append(t.id)
            created_uuids.append(t_uuid)

            # Transition to the target status
            if status == "AUDITED":
                template_repo.update_status(
                    template_uuid=t_uuid,
                    tenant=tenant,
                    current_status="CREATED",
                    new_status="AUDITED",
                )
            elif status == "ONLINE":
                template_repo.update_status(
                    template_uuid=t_uuid,
                    tenant=tenant,
                    current_status="CREATED",
                    new_status="AUDITED",
                )
                template_repo.update_status(
                    template_uuid=t_uuid,
                    tenant=tenant,
                    current_status="AUDITED",
                    new_status="ONLINE",
                )
            elif status == "OFFLINE":
                template_repo.update_status(
                    template_uuid=t_uuid,
                    tenant=tenant,
                    current_status="CREATED",
                    new_status="AUDITED",
                )
                template_repo.update_status(
                    template_uuid=t_uuid,
                    tenant=tenant,
                    current_status="AUDITED",
                    new_status="ONLINE",
                )
                template_repo.update_status(
                    template_uuid=t_uuid,
                    tenant=tenant,
                    current_status="ONLINE",
                    new_status="OFFLINE",
                )
            # CREATED stays as-is

        # list_online_templates should return only ONLINE templates
        result = _dts().list_online_templates(tenant=tenant, page=1, page_size=20)
        assert result.total >= 1
        for item in result.items:
            assert item.status == "ONLINE", f"Expected ONLINE, got {item.status}"

        # Verify at least one of our test templates is in the list
        online_uuids = {item.template_uuid for item in result.items}
        assert shared_template_setup["template_uuid"] in online_uuids or any(
            u in online_uuids for u in created_uuids
        )

    def test_tenant_isolation(self, shared_template_setup, created_template_ids):
        """Test tenant isolation for update, status, delete, and list operations."""
        from secbaas.api.template_manage import (
            ArcaTemplateConfig,
            TemplateCreate,
            TemplateUpdate,
        )
        from secbaas.api.tenant_manage import TenantType

        tenant = shared_template_setup["tenant"]
        wrong_tenant = "wrong_tenant"

        # Create a template to test isolation
        t_uuid = generate_template_uuid()
        t = _dts().create_template(
            tenant=tenant,
            data=TemplateCreate(
                template_uuid=t_uuid,
                template_id=random.randint(1, 999999999),
                type=TenantType.ARCA,
                name="Isolation Test",
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
        created_template_ids.append(t.id)

        # Test 1: Update with wrong tenant returns None
        result = _dts().update_template(
            tenant=wrong_tenant,
            template_uuid=t_uuid,
            status=TemplateStatus.CREATED,
            data=TemplateUpdate(name="Hacked", operator="hacker"),
        )
        assert result is None

        # Test 2: Status transition with wrong tenant returns None
        result = _dts().update_status(
            tenant=wrong_tenant,
            template_uuid=t_uuid,
            current_status=TemplateStatus.CREATED,
            new_status=TemplateStatus.AUDITED,
        )
        assert result is None

        # Test 3: Soft delete with wrong tenant returns False
        result = _dts().soft_delete_template(
            tenant=wrong_tenant,
            template_uuid=t_uuid,
            status=TemplateStatus.CREATED,
            operator="hacker",
        )
        assert result is False

        # Test 4: List with wrong tenant returns empty or different results
        list_result = _dts().list_templates(tenant=wrong_tenant, page=1, page_size=10)
        # Wrong tenant should get 0 or no overlap with our templates
        for item in list_result.items:
            assert item.tenant != tenant or item.template_uuid != t_uuid

        # Test 5: get_online_template_by_uuid with wrong tenant returns None
        result = _dts().get_online_template_by_uuid(
            tenant=wrong_tenant, template_uuid=t_uuid
        )
        assert result is None

        # Verify our template is still in CREATED for the correct tenant
        correct_result = _dts().get_by_template_id(
            template_id=t.template_id,
        )
        assert correct_result is not None
        assert correct_result.status == "CREATED"
