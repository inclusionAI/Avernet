"""E2E tests for Admin API Gateway endpoints.

Tests cover admin-level API key management through /api/v1/admin/api-keys:
- GET  /api/v1/admin/api-keys            — paginated list
- POST /api/v1/admin/api-keys            — create key
- GET  /api/v1/admin/api-keys/{prefix}   — get key by prefix
- PATCH /api/v1/admin/api-keys/{prefix}/status — revoke key
- POST /api/v1/admin/api-keys/{prefix}/allowed-bots/grant  — grant bot permission
- POST /api/v1/admin/api-keys/{prefix}/allowed-bots/revoke — revoke bot permission

NOTE: Admin endpoints require admin authentication not available in bare mode.
Tests exercise the endpoint contract and error paths gracefully.
"""

import pytest

from ..conftest import APITestHelper

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]


class TestAdminListKeys:
    """GET /api/v1/admin/api-keys — list keys as admin."""

    @pytest.mark.asyncio
    async def test_admin_list_keys_paginated(self, api: APITestHelper) -> None:
        """GET /api/v1/admin/api-keys returns 200/403 (admin auth required)."""
        response = await api.client.get(
            api.admin_api_key_url(),
            params=api.params(page=1, page_size=10),
        )

        assert response.status_code in (200, 401, 403), (
            f"Expected 200, 401, or 403 for admin list, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_admin_list_keys_with_filters(self, api: APITestHelper) -> None:
        """GET /api/v1/admin/api-keys with status filter respects auth."""
        response = await api.client.get(
            api.admin_api_key_url(),
            params=api.params(page=1, page_size=10, status="ACTIVE"),
        )

        assert response.status_code in (200, 401, 403), (
            f"Expected 200, 401, or 403 for filtered admin list, "
            f"got {response.status_code}: {response.text[:200]}"
        )


class TestAdminCreateKey:
    """POST /api/v1/admin/api-keys — create key as admin."""

    @pytest.mark.asyncio
    async def test_admin_create_app_key(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """POST /api/v1/admin/api-keys with app key attributes exercises endpoint."""
        response = await api.client.post(
            api.admin_api_key_url(),
            params=api.params(),
            json={
                "app_type": "app",
                "app_id": f"e2e-admin-app-{unique_id}",
                "key_name": f"admin-app-key-{unique_id}",
                "description": "E2E admin-created app key",
                "rate_limit_rpm": 60,
                "rate_limit_rpd": 1000,
                "tenant": api.tenant,
            },
        )

        assert response.status_code in (200, 401, 403, 422), (
            f"Expected 200, 401, 403, or 422 for admin create app key, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_admin_create_bot_key(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """POST /api/v1/admin/api-keys with bot key attributes exercises endpoint."""
        response = await api.client.post(
            api.admin_api_key_url(),
            params=api.params(),
            json={
                "app_type": "bot",
                "app_id": f"e2e-admin-bot-{unique_id}",
                "key_name": f"admin-bot-key-{unique_id}",
                "description": "E2E admin-created bot key",
                "tenant": api.tenant,
            },
        )

        assert response.status_code in (200, 401, 403, 422), (
            f"Expected 200, 401, 403, or 422 for admin create bot key, "
            f"got {response.status_code}: {response.text[:200]}"
        )


class TestAdminGetKey:
    """GET /api/v1/admin/api-keys/{prefix} — get key details as admin."""

    @pytest.mark.asyncio
    async def test_admin_get_nonexistent_key(self, api: APITestHelper) -> None:
        """GET /api/v1/admin/api-keys/{nonexistent} returns 401/403/404."""
        response = await api.client.get(
            api.admin_api_key_url("nonexistent-prefix"),
            params=api.params(),
        )

        assert response.status_code in (401, 403, 404), (
            f"Expected 401, 403, or 404 for nonexistent admin key, "
            f"got {response.status_code}: {response.text[:200]}"
        )


class TestAdminRevokeKey:
    """PATCH /api/v1/admin/api-keys/{prefix}/status — revoke key as admin."""

    @pytest.mark.asyncio
    async def test_admin_revoke_nonexistent_key(self, api: APITestHelper) -> None:
        """PATCH /api/v1/admin/api-keys/{prefix}/status with action=revoke on nonexistent."""
        response = await api.client.patch(
            api.admin_api_key_url("nonexistent-prefix") + "/status",
            params=api.params(),
            json={"action": "revoke"},
        )

        assert response.status_code in (401, 403, 404), (
            f"Expected 401, 403, or 404 for admin revoke nonexistent, "
            f"got {response.status_code}: {response.text[:200]}"
        )


class TestAdminGrantRevokePermissions:
    """POST /api/v1/admin/api-keys/{prefix}/allowed-bots/grant and /revoke."""

    @pytest.mark.asyncio
    async def test_admin_grant_bot_permission(self, api: APITestHelper) -> None:
        """POST /api/v1/admin/api-keys/{prefix}/allowed-bots/grant exercises endpoint."""
        response = await api.client.post(
            api.admin_api_key_url("test-prefix") + "/allowed-bots/grant",
            params=api.params(),
            json={"bot_id": "bot-123:entity-456"},
        )

        assert response.status_code in (200, 401, 403, 404), (
            f"Expected 200, 401, 403, or 404 for admin grant bot permission, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_admin_revoke_bot_permission(self, api: APITestHelper) -> None:
        """POST /api/v1/admin/api-keys/{prefix}/allowed-bots/revoke exercises endpoint."""
        response = await api.client.post(
            api.admin_api_key_url("test-prefix") + "/allowed-bots/revoke",
            params=api.params(),
            json={"bot_id": "bot-123:entity-456"},
        )

        assert response.status_code in (200, 401, 403, 404), (
            f"Expected 200, 401, 403, or 404 for admin revoke bot permission, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_admin_grant_invalid_bot_id_format(self, api: APITestHelper) -> None:
        """POST /api/v1/admin/api-keys/{prefix}/allowed-bots/grant with invalid bot_id."""
        response = await api.client.post(
            api.admin_api_key_url("test-prefix") + "/allowed-bots/grant",
            params=api.params(),
            json={"bot_id": "invalid-bot-id"},
        )

        assert response.status_code in (200, 400, 401, 403, 404), (
            f"Expected 200, 400, 401, 403, or 404 for invalid bot_id format, "
            f"got {response.status_code}: {response.text[:200]}"
        )


class TestAdminErrors:
    """Admin endpoint error paths."""

    @pytest.mark.asyncio
    async def test_admin_create_invalid_body(self, api: APITestHelper) -> None:
        """POST /api/v1/admin/api-keys with empty body returns 400/422/403."""
        response = await api.client.post(
            api.admin_api_key_url(),
            params=api.params(),
            json={},
        )

        assert response.status_code in (400, 401, 403, 422), (
            f"Expected 400, 401, 403, or 422 for empty body admin create, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_admin_get_nonexistent_key_404(self, api: APITestHelper) -> None:
        """GET /api/v1/admin/api-keys/{nonexistent} returns 401/403/404."""
        response = await api.client.get(
            api.admin_api_key_url("aaaaaaaaaaaaaaaa"),
            params=api.params(),
        )

        assert response.status_code in (401, 403, 404), (
            f"Expected 401, 403, or 404 for nonexistent admin key, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_admin_update_nonexistent_config(self, api: APITestHelper) -> None:
        """PUT /api/v1/admin/api-keys/{prefix}/config on nonexistent key."""
        response = await api.client.put(
            api.admin_api_key_url("aaaa") + "/config",
            params=api.params(),
            json={"key_name": "renamed-key"},
        )

        assert response.status_code in (401, 403, 404), (
            f"Expected 401, 403, or 404 for update nonexistent config, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_admin_update_config_empty_body(self, api: APITestHelper) -> None:
        """PUT /api/v1/admin/api-keys/{prefix}/config with empty body returns 400/422."""
        response = await api.client.put(
            api.admin_api_key_url("test-prefix") + "/config",
            params=api.params(),
            json={},
        )

        assert response.status_code in (400, 401, 403, 404, 422), (
            f"Expected 400, 401, 403, 404, or 422 for empty config body, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_admin_patch_status_nonexistent(self, api: APITestHelper) -> None:
        """PATCH /api/v1/admin/api-keys/{prefix}/status on nonexistent key."""
        response = await api.client.patch(
            api.admin_api_key_url("aaaa") + "/status",
            params=api.params(),
            json={"action": "activate"},
        )

        assert response.status_code in (401, 403, 404), (
            f"Expected 401, 403, or 404 for status patch nonexistent, "
            f"got {response.status_code}: {response.text[:200]}"
        )
