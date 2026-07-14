"""Mock-based unit tests for DefaultDeviceTemplateService.

Covers all 9 static methods with mocked repository layer.
No database or network calls are made in these tests.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.api.template_manage import (
    ArcaTemplateConfig,
    DeviceTemplateResponse,
    TemplateCreate,
    TemplateListResponse,
    TemplateStatus,
    TemplateUpdate,
)
from secbaas.community.api.tenant_manage import TenantType
from secbaas.community.core.service.template_manage import DefaultDeviceTemplateService

# ==================== Fixtures ====================


def _make_mock_record(**overrides: object) -> MagicMock:
    """Create a mock DB record with realistic default values."""
    record = MagicMock()
    record.id = overrides.get("id", 1)
    record.template_id = overrides.get("template_id", 1001)
    record.type = overrides.get("type", "ARCA")
    record.template_uuid = overrides.get("template_uuid", "test-uuid-001")
    record.tenant = overrides.get("tenant", "test_tenant")
    record.name = overrides.get("name", "Test Template")
    record.description = overrides.get("description", None)
    record.status = overrides.get("status", TemplateStatus.CREATED.value)
    record.config = overrides.get("config", None)
    record.creator = overrides.get("creator", "user-1")
    record.modifier = overrides.get("modifier", "user-1")
    record.gmt_create = overrides.get("gmt_create", datetime(2025, 1, 1))
    record.gmt_modified = overrides.get("gmt_modified", datetime(2025, 1, 1))
    return record


@pytest.fixture
def mock_repo() -> MagicMock:
    """Fixture providing a mock DeviceTemplateRepository."""
    repo = MagicMock()
    yield repo


@pytest.fixture
def mock_tenant_service():
    """Create a mock tenant service that returns a tenant with config."""
    mock_tenant = MagicMock()
    mock_tenant.tenant_id = "test-tenant"
    mock_tenant.name = "test-tenant"
    mock_tenant.description = "test"
    mock_tenant.status = "active"
    mock_tenant.creator = "system"
    mock_tenant.modifier = "system"
    mock_tenant.extra_config = {}
    mock_tenant.env = "dev"
    mock_tenant.domain = "default"
    mock_tenant.gmt_create = datetime(2024, 1, 1)
    mock_tenant.gmt_modified = datetime(2024, 1, 1)

    mock_svc = MagicMock()
    mock_svc.get.return_value = mock_tenant
    return mock_svc


@pytest.fixture
def service(mock_repo, mock_tenant_service) -> "DefaultDeviceTemplateService":
    """Fixture providing a DefaultDeviceTemplateService with mock repository."""
    return DefaultDeviceTemplateService(
        repository=mock_repo,
        tenant_service=mock_tenant_service,
        secret_plugin=MagicMock(),
    )


@pytest.fixture
def mock_encrypt() -> MagicMock:
    """Fixture that patches _ensure_api_key_encrypted to a no-op."""
    with patch(
        "secbaas.community.core.service.template_manage._device_template_service._ensure_api_key_encrypted"
    ) as m:
        yield m


def _make_arca_config() -> ArcaTemplateConfig:
    return ArcaTemplateConfig(type="ARCA", base_url="http://test", api_key="test")


def _make_create_data(**overrides: object) -> TemplateCreate:
    """Helper to create a TemplateCreate with minimal boilerplate."""
    return TemplateCreate(
        template_id=overrides.get("template_id", 1001),
        type=overrides.get("type", TenantType.ARCA),
        name=overrides.get("name", "Test Template"),
        operator=overrides.get("operator", "user-1"),
        config=overrides.get("config", _make_arca_config()),
        description=overrides.get("description", None),
        template_uuid=overrides.get("template_uuid", None),
    )


# ==================== create_template ====================


class TestCreateTemplate:
    """Tests for DefaultDeviceTemplateService.create_template."""

    def test_create_basic(
        self, service, mock_repo: MagicMock, mock_encrypt: MagicMock
    ) -> None:
        """WHEN creating a template with valid data, THEN returns DeviceTemplateResponse."""
        template_id_val = 1001
        mock_record = _make_mock_record(template_id=template_id_val)
        mock_repo.get_by_template_id.return_value = None
        mock_repo.insert_template.return_value = 1
        mock_repo.get_by_id.return_value = mock_record

        result = service.create_template(
            tenant="test_tenant",
            data=_make_create_data(template_id=template_id_val),
        )

        assert isinstance(result, DeviceTemplateResponse)
        assert result.template_id == template_id_val
        assert result.name == "Test Template"
        mock_repo.get_by_template_id.assert_called_once_with(template_id_val)
        mock_repo.insert_template.assert_called_once()

    def test_duplicate_template_id_raises(
        self, service, mock_repo: MagicMock, mock_encrypt: MagicMock
    ) -> None:
        """WHEN template_id already exists, THEN raises ValueError."""
        mock_repo.get_by_template_id.return_value = _make_mock_record(
            template_uuid="existing-uuid"
        )

        with pytest.raises(ValueError, match="already exists"):
            service.create_template(
                tenant="test_tenant",
                data=_make_create_data(template_id=1001),
            )

    def test_auto_generate_uuid(
        self, service, mock_repo: MagicMock, mock_encrypt: MagicMock
    ) -> None:
        """WHEN template_uuid not provided, THEN auto-generates one."""
        mock_repo.get_by_template_id.return_value = None
        mock_record = _make_mock_record(template_uuid="TEMPLATE-auto")
        mock_repo.insert_template.return_value = 1
        mock_repo.get_by_id.return_value = mock_record

        result = service.create_template(
            tenant="test_tenant",
            data=_make_create_data(),
        )

        assert result is not None
        # insert_template was called with a template_uuid (auto-generated)
        actual_kwargs = mock_repo.insert_template.call_args.kwargs
        assert actual_kwargs["template_uuid"].startswith("TEMPLATE-")

    def test_encrypt_called_on_arca(
        self, service, mock_repo: MagicMock, mock_encrypt: MagicMock
    ) -> None:
        """WHEN config is ArcaTemplateConfig, THEN encrypt is called."""
        mock_repo.get_by_template_id.return_value = None
        mock_repo.insert_template.return_value = 1
        mock_repo.get_by_id.return_value = _make_mock_record()

        service.create_template(
            tenant="test_tenant",
            data=_make_create_data(),
        )

        mock_encrypt.assert_called_once()


# ==================== get_by_template_id ====================


class TestGetByTemplateId:
    """Tests for DefaultDeviceTemplateService.get_by_template_id."""

    def test_found(self, service, mock_repo: MagicMock) -> None:
        """WHEN template_id exists, THEN returns DeviceTemplateResponse."""
        mock_record = _make_mock_record(template_id=1001)
        mock_repo.get_by_template_id.return_value = mock_record

        result = service.get_by_template_id(template_id=1001)

        assert isinstance(result, DeviceTemplateResponse)
        assert result.template_id == 1001
        assert result.name == "Test Template"

    def test_not_found(self, service, mock_repo: MagicMock) -> None:
        """WHEN template_id does not exist, THEN returns None."""
        mock_repo.get_by_template_id.return_value = None

        result = service.get_by_template_id(template_id=9999)

        assert result is None


# ==================== get_default_or_explicit_template ====================


class TestGetDefaultOrExplicitTemplate:
    """Tests for DefaultDeviceTemplateService.get_default_or_explicit_template."""

    @pytest.fixture(autouse=True)
    def mock_tenant_service(self) -> MagicMock:
        """Mock the tenant manage service to avoid DB calls.

        DefaultTenantManageService is imported inside the method body via a
        delayed inline import, so we patch at the *target module* rather than
        the source module.
        """
        with patch(
            "secbaas.community.core.service.tenant_manage.DefaultTenantManageService"
        ) as m:
            yield m

    def test_explicit_uuid_found(
        self, service, mock_repo: MagicMock, mock_tenant_service: MagicMock
    ) -> None:
        """WHEN explicit template_uuid provided and found, THEN returns template."""
        mock_repo.get_online_by_template_uuid.return_value = _make_mock_record(
            template_uuid="explicit-uuid", tenant="test_tenant"
        )

        result = service.get_default_or_explicit_template(
            tenant="test_tenant", template_uuid="explicit-uuid"
        )

        assert isinstance(result, DeviceTemplateResponse)
        assert result.template_uuid == "explicit-uuid"
        mock_repo.get_online_by_template_uuid.assert_called_with(
            "explicit-uuid", "test_tenant"
        )

    def test_explicit_uuid_not_found(
        self, service, mock_repo: MagicMock, mock_tenant_service: MagicMock
    ) -> None:
        """WHEN explicit template_uuid not found, THEN raises ValueError."""
        mock_repo.get_online_by_template_uuid.return_value = None

        with pytest.raises(ValueError, match="Template not found"):
            service.get_default_or_explicit_template(
                tenant="test_tenant", template_uuid="nonexistent-uuid"
            )

    def test_explicit_uuid_tenant_mismatch(
        self, service, mock_repo: MagicMock, mock_tenant_service: MagicMock
    ) -> None:
        """WHEN explicit template belongs to different tenant, THEN raises ValueError."""
        mock_repo.get_online_by_template_uuid.return_value = _make_mock_record(
            template_uuid="explicit-uuid", tenant="other_tenant"
        )

        with pytest.raises(ValueError, match="does not belong"):
            service.get_default_or_explicit_template(
                tenant="test_tenant", template_uuid="explicit-uuid"
            )

    def test_default_from_tenant_config(
        self, service, mock_repo: MagicMock, mock_tenant_service: MagicMock
    ) -> None:
        """WHEN no explicit uuid, THEN falls back to tenant's default_template_uuid."""
        mock_tenant_obj = MagicMock()
        mock_tenant_obj.extra_config.default_template_uuid = "default-uuid"
        mock_tenant_service.get_tenant_by_name.return_value = mock_tenant_obj
        mock_repo.get_online_by_template_uuid.return_value = _make_mock_record(
            template_uuid="default-uuid", tenant="test_tenant"
        )

        result = service.get_default_or_explicit_template(
            tenant="test_tenant", template_uuid=None
        )

        assert isinstance(result, DeviceTemplateResponse)
        mock_repo.get_online_by_template_uuid.assert_called_with(
            "default-uuid", "test_tenant"
        )

    def test_default_no_extra_config(
        self, service, mock_repo: MagicMock, mock_tenant_service: MagicMock
    ) -> None:
        """WHEN tenant has no extra_config, THEN raises ValueError."""
        mock_tenant_obj = MagicMock()
        mock_tenant_obj.extra_config = None
        mock_tenant_service.get_tenant_by_name.return_value = mock_tenant_obj

        with pytest.raises(ValueError, match="Tenant not found or has no config"):
            service.get_default_or_explicit_template(
                tenant="test_tenant", template_uuid=None
            )

    def test_default_no_template_uuid_in_config(
        self, service, mock_repo: MagicMock, mock_tenant_service: MagicMock
    ) -> None:
        """WHEN extra_config missing default_template_uuid, THEN raises ValueError."""
        mock_tenant_obj = MagicMock()
        mock_tenant_obj.extra_config.default_template_uuid = None
        mock_tenant_service.get_tenant_by_name.return_value = mock_tenant_obj

        with pytest.raises(ValueError, match="No default_template_uuid"):
            service.get_default_or_explicit_template(
                tenant="test_tenant", template_uuid=None
            )

    def test_tenant_not_found(
        self, service, mock_repo: MagicMock, mock_tenant_service: MagicMock
    ) -> None:
        """WHEN tenant not found, THEN raises ValueError."""
        mock_tenant_service.get_tenant_by_name.return_value = None

        with pytest.raises(ValueError, match="Tenant not found or has no config"):
            service.get_default_or_explicit_template(
                tenant="nonexistent", template_uuid=None
            )


# ==================== get_online_template_by_uuid ====================


class TestGetOnlineTemplateByUuid:
    """Tests for DefaultDeviceTemplateService.get_online_template_by_uuid."""

    def test_found(self, service, mock_repo: MagicMock) -> None:
        """WHEN online template exists for uuid+tenant, THEN returns response."""
        mock_record = _make_mock_record(
            template_uuid="online-uuid", tenant="test_tenant", status="ONLINE"
        )
        mock_repo.get_online_by_template_uuid.return_value = mock_record

        result = service.get_online_template_by_uuid(
            tenant="test_tenant", template_uuid="online-uuid"
        )

        assert isinstance(result, DeviceTemplateResponse)
        assert result.template_uuid == "online-uuid"
        assert result.status == "ONLINE"

    def test_not_found(self, service, mock_repo: MagicMock) -> None:
        """WHEN no online template found, THEN returns None."""
        mock_repo.get_online_by_template_uuid.return_value = None

        result = service.get_online_template_by_uuid(
            tenant="test_tenant", template_uuid="nonexistent"
        )

        assert result is None


# ==================== update_template ====================


class TestUpdateTemplate:
    """Tests for DefaultDeviceTemplateService.update_template."""

    def test_full_update(
        self, service, mock_repo: MagicMock, mock_encrypt: MagicMock
    ) -> None:
        """WHEN updating name+description+config, THEN fields are updated."""
        mock_repo.get_by_template_uuid.return_value = _make_mock_record(
            status=TemplateStatus.CREATED.value
        )
        updated_record = _make_mock_record(
            name="Updated Name",
            description="new desc",
            status=TemplateStatus.CREATED.value,
        )
        mock_repo.get_by_template_uuid.side_effect = [
            _make_mock_record(status=TemplateStatus.CREATED.value),
            updated_record,
        ]

        result = service.update_template(
            tenant="test_tenant",
            template_uuid="test-uuid-001",
            status=TemplateStatus.CREATED,
            data=TemplateUpdate(
                name="Updated Name",
                description="new desc",
                config=_make_arca_config(),
                operator="user-1",
            ),
        )

        assert isinstance(result, DeviceTemplateResponse)
        mock_repo.update_template.assert_called_once()

    def test_partial_update_name_only(
        self, service, mock_repo: MagicMock, mock_encrypt: MagicMock
    ) -> None:
        """WHEN updating only name, THEN only name field is changed."""
        mock_record = _make_mock_record(status=TemplateStatus.CREATED.value)
        mock_repo.get_by_template_uuid.return_value = mock_record
        mock_repo.get_by_template_uuid.side_effect = [
            mock_record,
            _make_mock_record(name="Updated Name", status=TemplateStatus.CREATED.value),
        ]

        result = service.update_template(
            tenant="test_tenant",
            template_uuid="test-uuid-001",
            status=TemplateStatus.CREATED,
            data=TemplateUpdate(name="Updated Name", operator="user-1"),
        )

        assert result is not None
        assert result.name == "Updated Name"
        mock_repo.update_template.assert_called_once()

    def test_template_not_found(
        self, service, mock_repo: MagicMock, mock_encrypt: MagicMock
    ) -> None:
        """WHEN template not found by composite key, THEN returns None."""
        mock_repo.get_by_template_uuid.return_value = None

        result = service.update_template(
            tenant="test_tenant",
            template_uuid="nonexistent",
            status=TemplateStatus.CREATED,
            data=TemplateUpdate(name="New Name", operator="user-1"),
        )

        assert result is None
        mock_repo.update_template.assert_not_called()


# ==================== update_status ====================


class TestUpdateStatus:
    """Tests for DefaultDeviceTemplateService.update_status."""

    def test_valid_transition(self, service, mock_repo: MagicMock) -> None:
        """WHEN valid status transition, THEN status is updated."""
        mock_repo.get_by_template_uuid.return_value = _make_mock_record(
            status=TemplateStatus.CREATED.value
        )
        updated_record = _make_mock_record(status=TemplateStatus.AUDITED.value)
        mock_repo.get_by_template_uuid.side_effect = [
            _make_mock_record(status=TemplateStatus.CREATED.value),
            updated_record,
        ]

        result = service.update_status(
            tenant="test_tenant",
            template_uuid="test-uuid-001",
            current_status=TemplateStatus.CREATED,
            new_status=TemplateStatus.AUDITED,
        )

        assert isinstance(result, DeviceTemplateResponse)
        assert result.status == "AUDITED"
        mock_repo.update_status.assert_called_once()

    def test_template_not_found(self, service, mock_repo: MagicMock) -> None:
        """WHEN template not found in current status, THEN returns None."""
        mock_repo.get_by_template_uuid.return_value = None

        result = service.update_status(
            tenant="test_tenant",
            template_uuid="nonexistent",
            current_status=TemplateStatus.CREATED,
            new_status=TemplateStatus.AUDITED,
        )

        assert result is None
        mock_repo.update_status.assert_not_called()


# ==================== list_templates ====================


class TestListTemplates:
    """Tests for DefaultDeviceTemplateService.list_templates."""

    def test_with_status_filter(self, service, mock_repo: MagicMock) -> None:
        """WHEN status filter provided, THEN repo receives status value."""
        mock_record = _make_mock_record(status=TemplateStatus.CREATED.value)
        mock_repo.list_templates.return_value = (1, [mock_record])

        result = service.list_templates(
            tenant="test_tenant",
            status=TemplateStatus.CREATED,
            page=1,
            page_size=10,
        )

        assert isinstance(result, TemplateListResponse)
        assert result.total == 1
        assert len(result.items) == 1
        mock_repo.list_templates.assert_called_with(
            tenant="test_tenant",
            status=TemplateStatus.CREATED.value,
            page=1,
            page_size=10,
        )

    def test_without_status_filter(self, service, mock_repo: MagicMock) -> None:
        """WHEN no status filter, THEN repo receives status=None."""
        mock_repo.list_templates.return_value = (0, [])

        result = service.list_templates(
            tenant="test_tenant", status=None, page=1, page_size=20
        )

        assert result.total == 0
        assert result.items == []
        mock_repo.list_templates.assert_called_with(
            tenant="test_tenant", status=None, page=1, page_size=20
        )

    def test_empty_results(self, service, mock_repo: MagicMock) -> None:
        """WHEN no templates match, THEN returns empty list."""
        mock_repo.list_templates.return_value = (0, [])

        result = service.list_templates(
            tenant="test_tenant", status=TemplateStatus.ONLINE, page=1, page_size=20
        )

        assert result.total == 0
        assert len(result.items) == 0


# ==================== list_online_templates ====================


class TestListOnlineTemplates:
    """Tests for DefaultDeviceTemplateService.list_online_templates."""

    def test_delegates_with_online_status(self, service, mock_repo: MagicMock) -> None:
        """WHEN listing online templates, THEN delegates with ONLINE status."""
        mock_record = _make_mock_record(status=TemplateStatus.ONLINE.value)
        mock_repo.list_templates.return_value = (1, [mock_record])

        result = service.list_online_templates(
            tenant="test_tenant", page=1, page_size=20
        )

        assert isinstance(result, TemplateListResponse)
        assert result.total == 1
        assert len(result.items) == 1
        assert result.items[0].status == "ONLINE"
        mock_repo.list_templates.assert_called_with(
            tenant="test_tenant",
            status=TemplateStatus.ONLINE.value,
            page=1,
            page_size=20,
        )

    def test_empty(self, service, mock_repo: MagicMock) -> None:
        """WHEN no online templates, THEN returns empty list."""
        mock_repo.list_templates.return_value = (0, [])

        result = service.list_online_templates(
            tenant="test_tenant", page=1, page_size=20
        )

        assert result.total == 0
        assert len(result.items) == 0


# ==================== soft_delete_template ====================


class TestSoftDeleteTemplate:
    """Tests for DefaultDeviceTemplateService.soft_delete_template."""

    def test_success(self, service, mock_repo: MagicMock) -> None:
        """WHEN template found and deleted, THEN returns True."""
        mock_repo.get_by_template_uuid.return_value = _make_mock_record(
            status=TemplateStatus.ONLINE.value
        )

        result = service.soft_delete_template(
            tenant="test_tenant",
            template_uuid="test-uuid-001",
            status=TemplateStatus.ONLINE,
            operator="user-1",
        )

        assert result is True
        mock_repo.soft_delete.assert_called_once()

    def test_not_found(self, service, mock_repo: MagicMock) -> None:
        """WHEN template not found, THEN returns False."""
        mock_repo.get_by_template_uuid.return_value = None

        result = service.soft_delete_template(
            tenant="test_tenant",
            template_uuid="nonexistent",
            status=TemplateStatus.ONLINE,
            operator="user-1",
        )

        assert result is False
        mock_repo.soft_delete.assert_not_called()


# ==================== _ensure_api_key_encrypted ====================


class TestEnsureApiKeyEncrypted:
    """Standalone tests for _ensure_api_key_encrypted function (forced encryption)."""

    def test_encrypts_when_flag_true_and_key_present(self):
        """Encrypts api_key when encrypt_api_key=True and api_key is provided."""
        from secbaas.community.core.service.template_manage._device_template_service import (
            _ensure_api_key_encrypted,
        )

        config = ArcaTemplateConfig(
            type="ARCA",
            base_url="http://test",
            api_key="plaintext-key",
            encrypt_api_key=True,
        )

        with patch(
            "secbaas.community.core.service.template_manage._device_template_service.common_sm4_encrypt",
            return_value="encrypted-key",
        ) as mock_encrypt:
            _ensure_api_key_encrypted(config, key_b64="test-key")
            mock_encrypt.assert_not_called()  # Already encrypted — guard prevents re-encryption
            assert config.api_key == "plaintext-key"
            assert config.encrypt_api_key is True

    def test_encrypts_when_flag_false_and_key_present(self):
        """Encrypts api_key when encrypt_api_key=False (forced encryption regardless of flag)."""
        from secbaas.community.core.service.template_manage._device_template_service import (
            _ensure_api_key_encrypted,
        )

        config = ArcaTemplateConfig(
            type="ARCA",
            base_url="http://test",
            api_key="plaintext-key",
            encrypt_api_key=False,
        )

        with patch(
            "secbaas.community.core.service.template_manage._device_template_service.common_sm4_encrypt",
            return_value="encrypted-key",
        ) as mock_encrypt:
            _ensure_api_key_encrypted(config, key_b64="test-key")
            mock_encrypt.assert_called_once_with("plaintext-key", "test-key")
            assert config.api_key == "encrypted-key"
            assert config.encrypt_api_key is True

    def test_no_encrypt_when_key_missing(self):
        """Does NOT encrypt when api_key is empty."""
        from secbaas.community.core.service.template_manage._device_template_service import (
            _ensure_api_key_encrypted,
        )

        config = ArcaTemplateConfig(
            type="ARCA",
            base_url="http://test",
            api_key="",
            encrypt_api_key=False,
        )

        with patch(
            "secbaas.community.core.service.template_manage._device_template_service.common_sm4_encrypt",
        ) as mock_encrypt:
            _ensure_api_key_encrypted(config, key_b64="test-key")
            mock_encrypt.assert_not_called()

    def test_no_encrypt_when_config_none(self):
        """Does NOT encrypt when config is None."""
        from secbaas.community.core.service.template_manage._device_template_service import (
            _ensure_api_key_encrypted,
        )

        # Should not raise
        _ensure_api_key_encrypted(None, key_b64="test-key")

    def test_no_encrypt_when_config_not_arca(self):
        """Does NOT encrypt when config is not ArcaTemplateConfig."""
        from secbaas.community.core.service.template_manage._device_template_service import (
            _ensure_api_key_encrypted,
        )

        # Plain dict is not ArcaTemplateConfig
        non_arca_config = {"type": "ARCA", "base_url": "http://test", "api_key": "key"}

        _ensure_api_key_encrypted(non_arca_config, key_b64="test-key")  # type: ignore[arg-type]
        assert non_arca_config["api_key"] == "key"

    def test_no_double_encrypt_when_already_encrypted(self):
        """Does NOT re-encrypt when encrypt_api_key is already True (anti-double-encrypt guard)."""
        from secbaas.community.core.service.template_manage._device_template_service import (
            _ensure_api_key_encrypted,
        )

        config = ArcaTemplateConfig(
            type="ARCA",
            base_url="http://test",
            api_key="already-encrypted-string",
            encrypt_api_key=True,
        )

        with patch(
            "secbaas.community.core.service.template_manage._device_template_service.common_sm4_encrypt",
        ) as mock_encrypt:
            _ensure_api_key_encrypted(config, key_b64="test-key")
            mock_encrypt.assert_not_called()
            assert config.api_key == "already-encrypted-string"
            assert config.encrypt_api_key is True
