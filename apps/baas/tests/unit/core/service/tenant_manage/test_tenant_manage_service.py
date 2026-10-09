"""Unit tests for DefaultTenantManageService — core service implementation."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.api.tenant_manage import (
    TenantConfig,
    TenantCreate,
    TenantListResponse,
    TenantResponse,
    TenantUpdate,
)
from secbaas.community.core.service.tenant_manage import (
    DefaultTenantManageService,
)


def make_record(
    name="test-tenant",
    description="desc",
    env="dev",
    extra_config=None,
    creator="user-1",
    modifier="user-1",
    record_id=1,
):
    """Helper to build a mock TenantRecord (dataclass)."""
    from secbaas.community.core.repository.tenant import TenantRecord

    now = datetime.now()
    return TenantRecord(
        id=record_id,
        gmt_create=now,
        gmt_modified=now,
        is_deleted=0,
        creator=creator,
        modifier=modifier,
        name=name,
        description=description,
        extra_config=extra_config or {},
        env=env,
    )


class TestCreateTenant:
    """Tests for DefaultTenantManageService.create_tenant."""

    def test_success(self):
        """WHEN creating a tenant, THEN it delegates to repo and returns response."""
        mock_repo = MagicMock()
        mock_repo.insert_tenant.return_value = 1
        mock_repo.get_by_id.return_value = make_record()

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            service = DefaultTenantManageService(tenant_repository=mock_repo)
            data = TenantCreate(name="test-tenant", description="A test tenant")
            result = service.create_tenant(data)

        assert isinstance(result, TenantResponse)
        assert result.name == "test-tenant"
        assert result.description == "desc"
        assert result.env == "dev"
        mock_repo.insert_tenant.assert_called_once()
        mock_repo.get_by_id.assert_called_once_with(1)

    def test_with_extra_config(self):
        """WHEN creating with extra_config, THEN it is serialized and stored."""
        mock_repo = MagicMock()
        mock_repo.insert_tenant.return_value = 2
        mock_repo.get_by_id.return_value = make_record(
            extra_config={"default_template_uuid": "uuid-123"}
        )

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            service = DefaultTenantManageService(tenant_repository=mock_repo)
            config = TenantConfig(default_template_uuid="uuid-123")
            data = TenantCreate(name="t", extra_config=config)
            result = service.create_tenant(data)

        assert result.extra_config.default_template_uuid == "uuid-123"
        _, kwargs = mock_repo.insert_tenant.call_args
        assert kwargs["extra_config"] == {"default_template_uuid": "uuid-123"}

    def test_default_operator_is_system(self):
        """WHEN operator not provided, THEN defaults to 'system'."""
        mock_repo = MagicMock()
        mock_repo.insert_tenant.return_value = 1
        mock_repo.get_by_id.return_value = make_record(
            creator="system", modifier="system"
        )

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            service = DefaultTenantManageService(tenant_repository=mock_repo)
            data = TenantCreate(name="test-tenant")
            service.create_tenant(data)

        _, kwargs = mock_repo.insert_tenant.call_args
        assert kwargs["creator"] == "system"
        assert kwargs["modifier"] == "system"

    def test_custom_operator(self):
        """WHEN operator provided, THEN it is passed through."""
        mock_repo = MagicMock()
        mock_repo.insert_tenant.return_value = 1
        mock_repo.get_by_id.return_value = make_record(
            creator="alice", modifier="alice"
        )

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            service = DefaultTenantManageService(tenant_repository=mock_repo)
            data = TenantCreate(name="test-tenant", operator="alice")
            service.create_tenant(data)

        _, kwargs = mock_repo.insert_tenant.call_args
        assert kwargs["creator"] == "alice"
        assert kwargs["modifier"] == "alice"

    def test_runtime_error_when_record_missing_after_insert(self):
        """WHEN record not found after insert, THEN RuntimeError raised."""
        mock_repo = MagicMock()
        mock_repo.insert_tenant.return_value = 999
        mock_repo.get_by_id.return_value = None

        with (
            patch(
                "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
                return_value="dev",
            ),
            pytest.raises(RuntimeError, match="Tenant record not found after insert"),
        ):
            service = DefaultTenantManageService(tenant_repository=mock_repo)
            data = TenantCreate(name="test-tenant")
            service.create_tenant(data)


class TestGetTenantByName:
    """Tests for DefaultTenantManageService.get_tenant_by_name."""

    def test_found(self):
        """WHEN tenant exists, THEN returns TenantResponse."""
        mock_repo = MagicMock()
        mock_repo.get_by_name.return_value = make_record()

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            service = DefaultTenantManageService(tenant_repository=mock_repo)
            result = service.get_tenant_by_name("test-tenant")

        assert isinstance(result, TenantResponse)
        assert result.name == "test-tenant"
        mock_repo.get_by_name.assert_called_once_with("test-tenant", "dev")

    def test_not_found_returns_none(self):
        """WHEN tenant does not exist, THEN returns None."""
        mock_repo = MagicMock()
        mock_repo.get_by_name.return_value = None

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            service = DefaultTenantManageService(tenant_repository=mock_repo)
            result = service.get_tenant_by_name("nonexistent")

        assert result is None


class TestGetTenantConfig:
    """Tests for DefaultTenantManageService.get_tenant_config."""

    def test_found_with_config(self):
        """WHEN tenant has extra_config, THEN returns TenantConfig."""
        mock_repo = MagicMock()
        mock_repo.get_by_name.return_value = make_record(
            extra_config={"default_template_uuid": "uuid-999"}
        )

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            service = DefaultTenantManageService(tenant_repository=mock_repo)
            result = service.get_tenant_config("test-tenant")

        assert isinstance(result, TenantConfig)
        assert result.default_template_uuid == "uuid-999"

    def test_found_without_config(self):
        """WHEN tenant has no extra_config, THEN returns None."""
        mock_repo = MagicMock()
        mock_repo.get_by_name.return_value = make_record(extra_config={})

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            service = DefaultTenantManageService(tenant_repository=mock_repo)
            result = service.get_tenant_config("test-tenant")

        assert result is None

    def test_not_found_returns_none(self):
        """WHEN tenant does not exist, THEN returns None."""
        mock_repo = MagicMock()
        mock_repo.get_by_name.return_value = None

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            service = DefaultTenantManageService(tenant_repository=mock_repo)
            result = service.get_tenant_config("nonexistent")

        assert result is None


class TestUpdateTenant:
    """Tests for DefaultTenantManageService.update_tenant."""

    def test_update_description(self):
        """WHEN updating description, THEN it is passed to repo."""
        mock_repo = MagicMock()
        mock_repo.get_by_name.side_effect = [
            make_record(description="old desc"),
            make_record(description="new desc"),
        ]

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            service = DefaultTenantManageService(tenant_repository=mock_repo)
            data = TenantUpdate(description="new desc")
            result = service.update_tenant("test-tenant", data)

        assert isinstance(result, TenantResponse)
        assert result.description == "new desc"
        _, kwargs = mock_repo.update_tenant.call_args
        assert kwargs["description"] == "new desc"

    def test_tenant_not_found_returns_none(self):
        """WHEN tenant does not exist, THEN returns None."""
        mock_repo = MagicMock()
        mock_repo.get_by_name.return_value = None

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            service = DefaultTenantManageService(tenant_repository=mock_repo)
            data = TenantUpdate(description="new desc")
            result = service.update_tenant("nonexistent", data)

        assert result is None
        mock_repo.update_tenant.assert_not_called()

    def test_no_updates_returns_current(self):
        """WHEN no fields to update, THEN returns current record without calling update."""
        mock_repo = MagicMock()
        mock_repo.get_by_name.return_value = make_record()

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            service = DefaultTenantManageService(tenant_repository=mock_repo)
            data = TenantUpdate()
            result = service.update_tenant("test-tenant", data)

        assert isinstance(result, TenantResponse)
        mock_repo.update_tenant.assert_not_called()

    def test_runtime_error_when_record_missing_after_update(self):
        """WHEN record not found after update, THEN RuntimeError raised."""
        mock_repo = MagicMock()
        mock_repo.get_by_name.side_effect = [
            make_record(description="old"),
            None,  # post-update fetch fails
        ]

        with (
            patch(
                "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
                return_value="dev",
            ),
            pytest.raises(RuntimeError, match="Tenant record not found after update"),
        ):
            service = DefaultTenantManageService(tenant_repository=mock_repo)
            data = TenantUpdate(description="new")
            service.update_tenant("test-tenant", data)

    def test_update_extra_config(self):
        """WHEN updating extra_config, THEN it is serialized."""
        mock_repo = MagicMock()
        mock_repo.get_by_name.side_effect = [
            make_record(),
            make_record(extra_config={"default_template_uuid": "uuid-456"}),
        ]

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            service = DefaultTenantManageService(tenant_repository=mock_repo)
            data = TenantUpdate(
                extra_config=TenantConfig(default_template_uuid="uuid-456")
            )
            result = service.update_tenant("test-tenant", data)

        assert result.extra_config.default_template_uuid == "uuid-456"
        _, kwargs = mock_repo.update_tenant.call_args
        assert kwargs["extra_config"] == {"default_template_uuid": "uuid-456"}


class TestListTenants:
    """Tests for DefaultTenantManageService.list_tenants."""

    def test_empty(self):
        """WHEN no tenants, THEN returns empty list response."""
        mock_repo = MagicMock()
        mock_repo.list_tenants.return_value = (0, [])

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            service = DefaultTenantManageService(tenant_repository=mock_repo)
            result = service.list_tenants()

        assert isinstance(result, TenantListResponse)
        assert result.items == []
        assert result.total == 0

    def test_with_items(self):
        """WHEN tenants exist, THEN returns paginated list."""
        mock_repo = MagicMock()
        mock_repo.list_tenants.return_value = (1, [make_record()])

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            service = DefaultTenantManageService(tenant_repository=mock_repo)
            result = service.list_tenants(page=1, page_size=10)

        assert len(result.items) == 1
        assert result.total == 1
        assert result.page == 1
        assert result.page_size == 10
        mock_repo.list_tenants.assert_called_once_with(env="dev", page=1, page_size=10)


class TestSoftDeleteTenant:
    """Tests for DefaultTenantManageService.soft_delete_tenant."""

    def test_success(self):
        """WHEN tenant exists, THEN soft deletes and returns True."""
        mock_repo = MagicMock()
        mock_repo.get_by_name.return_value = make_record()

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            service = DefaultTenantManageService(tenant_repository=mock_repo)
            result = service.soft_delete_tenant("test-tenant", "user-1")

        assert result is True
        mock_repo.soft_delete.assert_called_once_with(
            name="test-tenant", env="dev", modifier="user-1"
        )

    def test_not_found_returns_false(self):
        """WHEN tenant does not exist, THEN returns False."""
        mock_repo = MagicMock()
        mock_repo.get_by_name.return_value = None

        with patch(
            "secbaas.community.core.service.tenant_manage._tenant_manage_service.get_current_env",
            return_value="dev",
        ):
            service = DefaultTenantManageService(tenant_repository=mock_repo)
            result = service.soft_delete_tenant("nonexistent", "user-1")

        assert result is False
        mock_repo.soft_delete.assert_not_called()
