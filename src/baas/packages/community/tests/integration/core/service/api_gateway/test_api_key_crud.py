"""Integration CRUD tests for DefaultAPIKeyService with real ZDAS database.

Tests the full service lifecycle: create, read, list, update, status transitions,
and error cases — all against a real ZDAS MySQL database.

Requires env vars: ZDAS_HOST, ZDAS_PORT, ZDAS_USER, ZDAS_PASSWORD
"""

import pytest

from secbaas.api import OperationContext
from secbaas.api.api_gateway import (
    APIKeyCreate,
    APIKeyError,
    APIKeyQuery,
    APIKeyUpdate,
)
from secbaas.core.service.api_gateway import DefaultAPIKeyService

pytestmark = pytest.mark.integration


@pytest.mark.integration
class TestCreateKey:
    """DefaultAPIKeyService.create_key integration tests."""

    async def test_create_key_success(
        self, svc: DefaultAPIKeyService, ctx: OperationContext, created_keys: list[int]
    ) -> None:
        """Create an active API key and verify the response contains a plaintext key."""
        data = APIKeyCreate(
            app_id="int-test-app-create",
            app_type="test",
            key_name="Integration Test Key",
            description="Created during integration test",
            owner="test_user",
            tenant="test_tenant_int",
        )
        result = await svc.create_key(data, ctx)
        created_keys.append(result.id)

        assert result.id > 0
        assert result.api_key is not None
        assert len(result.api_key) == 32  # base62 32 chars
        assert result.api_key_prefix == result.api_key[:8]
        assert result.app_id == "int-test-app-create"
        assert result.status == "ACTIVE"

    async def test_create_key_inactive(
        self, svc: DefaultAPIKeyService, ctx: OperationContext, created_keys: list[int]
    ) -> None:
        """Create an API key and verify status defaults to ACTIVE (no inactive path in service)."""
        data = APIKeyCreate(
            app_id="int-test-app-inactive",
            key_name="Inactive Test",
            tenant="test_tenant_int",
        )
        result = await svc.create_key(data, ctx)
        created_keys.append(result.id)

        assert result.id > 0
        assert result.api_key is not None
        # Service always creates ACTIVE
        assert result.status == "ACTIVE"


@pytest.mark.integration
class TestGetKey:
    """DefaultAPIKeyService.get_key integration tests."""

    async def test_get_key_found(
        self, svc: DefaultAPIKeyService, ctx: OperationContext, created_keys: list[int]
    ) -> None:
        """Retrieve an existing API key by ID and verify all fields."""
        data = APIKeyCreate(
            app_id="int-test-app-get",
            key_name="Get Test Key",
            description="Test key for get_by_id",
            app_type="test",
            owner="owner_user",
            tenant="test_tenant_int",
        )
        created = await svc.create_key(data, ctx)
        created_keys.append(created.id)

        result = await svc.get_key(created.id, ctx)

        assert result is not None
        assert result.id == created.id
        assert result.app_id == "int-test-app-get"
        assert result.key_name == "Get Test Key"
        assert result.description == "Test key for get_by_id"
        assert result.app_type == "test"
        assert result.owner == "owner_user"
        assert result.api_key_prefix is not None
        assert result.status == "ACTIVE"

    async def test_get_key_not_found(
        self, svc: DefaultAPIKeyService, ctx: OperationContext
    ) -> None:
        """get_key returns None for a non-existent ID."""
        result = await svc.get_key(999999999, ctx)
        assert result is None


@pytest.mark.integration
class TestListKeys:
    """DefaultAPIKeyService.list_keys integration tests."""

    async def test_list_keys_by_app_id(
        self, svc: DefaultAPIKeyService, ctx: OperationContext, created_keys: list[int]
    ) -> None:
        """List keys filtered by app_id."""
        app_id = "int-test-app-list-1"
        data = APIKeyCreate(
            app_id=app_id, key_name="List Test A", tenant="test_tenant_int"
        )
        k1 = await svc.create_key(data, ctx)
        created_keys.append(k1.id)
        k2 = await svc.create_key(data, ctx)
        created_keys.append(k2.id)

        query = APIKeyQuery(app_id=app_id)
        result = await svc.list_keys(query, ctx, page=1, page_size=10)

        assert result.total >= 2
        assert len(result.items) >= 2
        for item in result.items:
            assert item.app_id == app_id

    async def test_list_keys_empty_result(
        self, svc: DefaultAPIKeyService, ctx: OperationContext
    ) -> None:
        """list_keys returns empty list for non-existent app_id."""
        query = APIKeyQuery(app_id="int-test-app-nonexistent")
        result = await svc.list_keys(query, ctx, page=1, page_size=10)

        assert result.total == 0
        assert len(result.items) == 0


@pytest.mark.integration
class TestUpdateKey:
    """DefaultAPIKeyService.update_key integration tests."""

    async def test_update_key_metadata(
        self, svc: DefaultAPIKeyService, ctx: OperationContext, created_keys: list[int]
    ) -> None:
        """Update API key metadata fields."""
        data = APIKeyCreate(
            app_id="int-test-app-update",
            key_name="Original Name",
            description="Original description",
            tenant="test_tenant_int",
        )
        created = await svc.create_key(data, ctx)
        created_keys.append(created.id)

        update_data = APIKeyUpdate(
            key_name="Updated Name",
            description="Updated description",
        )
        result = await svc.update_key(created.id, update_data, ctx)

        assert result is not None
        assert result.key_name == "Updated Name"
        assert result.description == "Updated description"

        # Verify persistence
        fetched = await svc.get_key(created.id, ctx)
        assert fetched is not None
        assert fetched.key_name == "Updated Name"
        assert fetched.description == "Updated description"

    async def test_update_key_not_found(
        self, svc: DefaultAPIKeyService, ctx: OperationContext
    ) -> None:
        """update_key returns None for non-existent ID."""
        data = APIKeyUpdate(key_name="No-op")
        result = await svc.update_key(999999999, data, ctx)
        assert result is None

    async def test_update_key_env_mismatch(
        self, svc: DefaultAPIKeyService, ctx: OperationContext, created_keys: list[int]
    ) -> None:
        """update_key raises APIKeyError when env does not match."""
        data = APIKeyCreate(
            app_id="int-test-app-env",
            key_name="Env Mismatch Test",
            tenant="test_tenant_int",
        )
        created = await svc.create_key(data, ctx)
        created_keys.append(created.id)

        wrong_ctx = OperationContext(operator="test_user", env="prod")
        update_data = APIKeyUpdate(key_name="Should Fail")

        with pytest.raises(APIKeyError) as exc:
            await svc.update_key(created.id, update_data, wrong_ctx)
        assert "环境不匹配" in str(exc.value)


@pytest.mark.integration
class TestActivate:
    """DefaultAPIKeyService.activate integration tests."""

    async def test_activate_success(
        self, svc: DefaultAPIKeyService, ctx: OperationContext, created_keys: list[int]
    ) -> None:
        """Activate an INACTIVE key — verify status transitions to ACTIVE."""
        # Service always creates ACTIVE, so we manually set to INACTIVE via repository
        data = APIKeyCreate(
            app_id="int-test-app-activate",
            key_name="Activate Test",
            tenant="test_tenant_int",
        )
        created = await svc.create_key(data, ctx)
        created_keys.append(created.id)

        # Deactivate first to get INACTIVE status
        await svc.deactivate(created.id, ctx)

        result = await svc.activate(created.id, ctx)
        assert result is not None
        assert result.status == "ACTIVE"

        # Verify persistence
        fetched = await svc.get_key(created.id, ctx)
        assert fetched is not None
        assert fetched.status == "ACTIVE"

    async def test_activate_already_active(
        self, svc: DefaultAPIKeyService, ctx: OperationContext, created_keys: list[int]
    ) -> None:
        """activate raises APIKeyError when key is already ACTIVE."""
        data = APIKeyCreate(
            app_id="int-test-app-activate-err",
            key_name="Already Active",
            tenant="test_tenant_int",
        )
        created = await svc.create_key(data, ctx)
        created_keys.append(created.id)

        with pytest.raises(APIKeyError) as exc:
            await svc.activate(created.id, ctx)
        assert "INACTIVE" in str(exc.value)

    async def test_activate_not_found(
        self, svc: DefaultAPIKeyService, ctx: OperationContext
    ) -> None:
        """activate returns None for non-existent ID."""
        result = await svc.activate(999999999, ctx)
        assert result is None

    async def test_activate_env_mismatch(
        self, svc: DefaultAPIKeyService, ctx: OperationContext, created_keys: list[int]
    ) -> None:
        """activate raises APIKeyError when env does not match."""
        data = APIKeyCreate(
            app_id="int-test-app-activate-env",
            key_name="Activate Env Mismatch",
            tenant="test_tenant_int",
        )
        created = await svc.create_key(data, ctx)
        created_keys.append(created.id)

        wrong_ctx = OperationContext(operator="test_user", env="prod")
        with pytest.raises(APIKeyError) as exc:
            await svc.activate(created.id, wrong_ctx)
        assert "环境不匹配" in str(exc.value)


@pytest.mark.integration
class TestDeactivate:
    """DefaultAPIKeyService.deactivate integration tests."""

    async def test_deactivate_success(
        self, svc: DefaultAPIKeyService, ctx: OperationContext, created_keys: list[int]
    ) -> None:
        """Deactivate an ACTIVE key — verify status transitions to INACTIVE."""
        data = APIKeyCreate(
            app_id="int-test-app-deactivate",
            key_name="Deactivate Test",
            tenant="test_tenant_int",
        )
        created = await svc.create_key(data, ctx)
        created_keys.append(created.id)

        result = await svc.deactivate(created.id, ctx)
        assert result is not None
        assert result.status == "INACTIVE"

        # Verify persistence
        fetched = await svc.get_key(created.id, ctx)
        assert fetched is not None
        assert fetched.status == "INACTIVE"

    async def test_deactivate_already_inactive(
        self, svc: DefaultAPIKeyService, ctx: OperationContext, created_keys: list[int]
    ) -> None:
        """deactivate raises APIKeyError when key is already INACTIVE."""
        data = APIKeyCreate(
            app_id="int-test-app-deactivate-err",
            key_name="Already Inactive",
            tenant="test_tenant_int",
        )
        created = await svc.create_key(data, ctx)
        created_keys.append(created.id)

        # Deactivate first
        await svc.deactivate(created.id, ctx)

        with pytest.raises(APIKeyError) as exc:
            await svc.deactivate(created.id, ctx)
        assert "ACTIVE" in str(exc.value)


@pytest.mark.integration
class TestRevoke:
    """DefaultAPIKeyService.revoke integration tests."""

    async def test_revoke_success(
        self, svc: DefaultAPIKeyService, ctx: OperationContext, created_keys: list[int]
    ) -> None:
        """Revoke an ACTIVE key — verify status transitions to REVOKED."""
        data = APIKeyCreate(
            app_id="int-test-app-revoke",
            key_name="Revoke Test",
            tenant="test_tenant_int",
        )
        created = await svc.create_key(data, ctx)
        created_keys.append(created.id)

        result = await svc.revoke(created.id, ctx)
        assert result is not None
        assert result.status == "REVOKED"

        # Verify persistence
        fetched = await svc.get_key(created.id, ctx)
        assert fetched is not None
        assert fetched.status == "REVOKED"

    async def test_revoke_already_revoked(
        self, svc: DefaultAPIKeyService, ctx: OperationContext, created_keys: list[int]
    ) -> None:
        """revoke raises APIKeyError when key is already REVOKED."""
        data = APIKeyCreate(
            app_id="int-test-app-revoke-err",
            key_name="Already Revoked",
            tenant="test_tenant_int",
        )
        created = await svc.create_key(data, ctx)
        created_keys.append(created.id)

        await svc.revoke(created.id, ctx)

        with pytest.raises(APIKeyError) as exc:
            await svc.revoke(created.id, ctx)
        assert "已吊销" in str(exc.value)

    async def test_revoke_not_found(
        self, svc: DefaultAPIKeyService, ctx: OperationContext
    ) -> None:
        """revoke returns None for non-existent ID."""
        result = await svc.revoke(999999999, ctx)
        assert result is None


@pytest.mark.integration
class TestFullLifecycle:
    """Full API key lifecycle integration test."""

    async def test_full_lifecycle(
        self, svc: DefaultAPIKeyService, ctx: OperationContext, created_keys: list[int]
    ) -> None:
        """Create -> read -> update -> deactivate -> activate -> revoke."""
        # Create
        data = APIKeyCreate(
            app_id="int-test-app-lifecycle",
            key_name="Lifecycle Key",
            description="Full lifecycle test",
            tenant="test_tenant_int",
        )
        created = await svc.create_key(data, ctx)
        created_keys.append(created.id)
        assert created.status == "ACTIVE"

        # Read
        fetched = await svc.get_key(created.id, ctx)
        assert fetched is not None
        assert fetched.key_name == "Lifecycle Key"

        # Update
        updated = await svc.update_key(
            created.id, APIKeyUpdate(key_name="Updated Lifecycle"), ctx
        )
        assert updated is not None
        assert updated.key_name == "Updated Lifecycle"

        # Deactivate
        deactivated = await svc.deactivate(created.id, ctx)
        assert deactivated is not None
        assert deactivated.status == "INACTIVE"

        # Activate
        activated = await svc.activate(created.id, ctx)
        assert activated is not None
        assert activated.status == "ACTIVE"

        # Revoke
        revoked = await svc.revoke(created.id, ctx)
        assert revoked is not None
        assert revoked.status == "REVOKED"
