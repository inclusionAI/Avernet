"""E2E tests for Admin API Key management endpoints."""

import pytest

pytestmark = [pytest.mark.e2e_asgi]


class TestAdminCreate:
    """POST /api/v1/admin/api-keys — create API key as admin."""

    @pytest.mark.asyncio
    async def test_admin_create_app_key(self) -> None: ...

    @pytest.mark.asyncio
    async def test_admin_create_bot_key(self) -> None: ...

    @pytest.mark.asyncio
    async def test_admin_create_key_with_all_fields(self) -> None: ...


class TestAdminList:
    """GET /api/v1/admin/api-keys — list keys as admin."""

    @pytest.mark.asyncio
    async def test_admin_list_keys(self) -> None: ...

    @pytest.mark.asyncio
    async def test_admin_list_keys_with_filters(self) -> None: ...

    @pytest.mark.asyncio
    async def test_admin_list_keys_with_status_filter(self) -> None: ...


class TestAdminGet:
    """GET /api/v1/admin/api-keys/{prefix} — get key as admin."""

    @pytest.mark.asyncio
    async def test_admin_get_key(self) -> None: ...

    @pytest.mark.asyncio
    async def test_admin_get_key_shows_all_fields(self) -> None: ...


class TestAdminUpdate:
    """PUT /api/v1/admin/api-keys/{prefix}/config — update key config as admin."""

    @pytest.mark.asyncio
    async def test_admin_update_key_config(self) -> None: ...

    @pytest.mark.asyncio
    async def test_admin_update_key_rate_limits(self) -> None: ...

    @pytest.mark.asyncio
    async def test_admin_update_key_owner(self) -> None: ...


class TestAdminStatus:
    """PATCH /api/v1/admin/api-keys/{prefix}/status — admin status management."""

    @pytest.mark.asyncio
    async def test_admin_deactivate_key(self) -> None: ...

    @pytest.mark.asyncio
    async def test_admin_activate_key(self) -> None: ...

    @pytest.mark.asyncio
    async def test_admin_revoke_key(self) -> None: ...


class TestAdminErrors:
    """Admin endpoint error paths."""

    @pytest.mark.asyncio
    async def test_admin_create_invalid_body(self) -> None: ...

    @pytest.mark.asyncio
    async def test_admin_get_nonexistent(self) -> None: ...

    @pytest.mark.asyncio
    async def test_admin_update_nonexistent_config(self) -> None: ...

    @pytest.mark.asyncio
    async def test_admin_update_config_empty_body(self) -> None: ...

    @pytest.mark.asyncio
    async def test_admin_patch_status_nonexistent(self) -> None: ...
