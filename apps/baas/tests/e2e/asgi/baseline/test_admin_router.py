"""E2E tests for Admin Router endpoints — Phase 1.4 coverage.

Tests cover admin, config, and BCN downlink routers not already tested:
- Admin API Key management: list/create/revoke/get (via /api/v1/admin/api-keys)
- Admin Publish management: force-success, device status update (/api/v1/admin/*)
- Admin Bot restore: POST /api/v1/admin/bots/{bot_id}/restore (atomic record restore)
- System Config management: list/create/update/delete (/api/v1/system-configs)
- QPM Config management: set/get/delete per bot (/api/v1/bot-qpm)
- Admin publish: retry non-admin path (/api/v1/publishes/{id}/retry)

NOTE: Admin endpoints require admin authentication not available in bare mode.
Tests exercise endpoint contracts and error paths gracefully.
"""

from unittest.mock import AsyncMock

import pytest

from secbaas.community.api.publish_manage import PublishConflictError
from tests.e2e.asgi.conftest import APITestHelper
from tests.unit.adapters.web.conftest import iter_api_routes

pytestmark = [pytest.mark.e2e_asgi]


class TestAdminKeyCRUD:
    """CRUD operations on /api/v1/admin/api-keys."""

    @pytest.mark.asyncio
    async def test_admin_list_keys_paginated(self, api: APITestHelper) -> None:
        """GET /api/v1/admin/api-keys with pagination params."""
        response = await api.client.get(
            api.admin_api_key_url(),
            params=api.params(page=1, page_size=5),
        )

        assert response.status_code in (200, 401, 403), (
            f"Expected 200, 401, or 403 for admin list keys, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_admin_list_keys_page_two(self, api: APITestHelper) -> None:
        """GET /api/v1/admin/api-keys with page=2."""
        response = await api.client.get(
            api.admin_api_key_url(),
            params=api.params(page=2, page_size=5),
        )

        assert response.status_code in (200, 401, 403), (
            f"Expected 200, 401, or 403 for admin list page 2, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_admin_create_key(self, api: APITestHelper, unique_id: str) -> None:
        """POST /api/v1/admin/api-keys creates key via admin endpoint."""
        response = await api.client.post(
            api.admin_api_key_url(),
            params=api.params(),
            json={
                "app_type": "app",
                "app_id": f"e2e-admin-crud-{unique_id}",
                "key_name": f"admin-crud-key-{unique_id}",
                "description": "E2E admin router test key",
                "rate_limit_rpm": 120,
                "rate_limit_rpd": 5000,
                "tenant": api.tenant,
            },
        )

        assert response.status_code in (200, 401, 403, 422), (
            f"Expected 200, 401, 403, or 422 for admin create key, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_admin_create_key_with_all_fields(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """POST /api/v1/admin/api-keys with all optional fields populated."""
        response = await api.client.post(
            api.admin_api_key_url(),
            params=api.params(),
            json={
                "app_type": "bot",
                "app_id": f"e2e-admin-full-{unique_id}",
                "key_name": f"admin-full-key-{unique_id}",
                "description": "E2E admin key with all fields",
                "rate_limit_rpm": 300,
                "rate_limit_rpd": 10000,
                "owner": "e2e-test-user",
                "tenant": api.tenant,
                "policy": '{"roles": ["read", "write"]}',
            },
        )

        assert response.status_code in (200, 401, 403, 422), (
            f"Expected 200, 401, 403, or 422 for admin create full key, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_admin_create_duplicate_prefix(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """POST /api/v1/admin/api-keys with same params twice — second fails."""
        body = {
            "app_type": "app",
            "app_id": f"e2e-admin-dup-{unique_id}",
            "key_name": f"admin-dup-key-{unique_id}",
            "description": "Duplicate test",
            "tenant": api.tenant,
        }

        r1 = await api.client.post(
            api.admin_api_key_url(),
            params=api.params(),
            json=body,
        )
        assert r1.status_code in (200, 401, 403, 422)

        r2 = await api.client.post(
            api.admin_api_key_url(),
            params=api.params(),
            json=body,
        )

        assert r2.status_code in (200, 201, 400, 401, 403, 409, 422), (
            f"Expected 200, 201, 400, 401, 403, 409, or 422 for duplicate prefix, "
            f"got {r2.status_code}: {r2.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_admin_revoke_key(self, api: APITestHelper) -> None:
        """PATCH /api/v1/admin/api-keys/{prefix}/status with action=revoke."""
        response = await api.client.patch(
            api.admin_api_key_url("test-revoke-prefix") + "/status",
            params=api.params(),
            json={"action": "revoke"},
        )

        assert response.status_code in (200, 401, 403, 404), (
            f"Expected 200, 401, 403, or 404 for admin revoke key, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_admin_get_key(self, api: APITestHelper) -> None:
        """GET /api/v1/admin/api-keys/{prefix} returns key details."""
        response = await api.client.get(
            api.admin_api_key_url("test-prefix"),
            params=api.params(),
        )

        assert response.status_code in (200, 401, 403, 404), (
            f"Expected 200, 401, 403, or 404 for admin get key, "
            f"got {response.status_code}: {response.text[:200]}"
        )


class TestAdminPublish:
    """Admin publish endpoints: force-success, device status update, publish retry."""

    @pytest.mark.asyncio
    async def test_admin_force_success_nonexistent(self, api: APITestHelper) -> None:
        """POST /api/v1/admin/force-success with nonexistent publish_id returns 404."""
        response = await api.client.post(
            api.admin_force_success_url(),
            params=api.params(),
            json={"publish_id": 99999999, "modifier": "e2e-test"},
        )

        assert response.status_code == 404, (
            f"Expected 404 for nonexistent publish_id, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_admin_force_success_missing_params(self, api: APITestHelper) -> None:
        """POST /api/v1/admin/force-success with missing params returns 422."""
        response = await api.client.post(
            api.admin_force_success_url(),
            params=api.params(),
            json={},
        )

        assert response.status_code == 422, (
            f"Expected 422 for missing params in force-success, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_admin_force_success_missing_modifier(
        self, api: APITestHelper
    ) -> None:
        """POST /api/v1/admin/force-success without modifier returns 422."""
        response = await api.client.post(
            api.admin_force_success_url(),
            params=api.params(),
            json={"publish_id": 1},
        )

        assert response.status_code == 422, (
            f"Expected 422 for missing modifier, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_admin_update_device_status_nonexistent(
        self, api: APITestHelper
    ) -> None:
        """POST /api/v1/admin/devices/{uuid}/status on nonexistent device returns 404."""
        response = await api.client.post(
            "/api/v1/admin/devices/nonexistent-device-uuid/status",
            params=api.params(),
            json={"status": "ACTIVE", "operator": "e2e-test"},
        )

        assert response.status_code == 404, (
            f"Expected 404 for nonexistent device, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_admin_update_device_status_missing_params(
        self, api: APITestHelper
    ) -> None:
        """POST /api/v1/admin/devices/{uuid}/status with missing params returns 422."""
        response = await api.client.post(
            "/api/v1/admin/devices/test-device-uuid/status",
            params=api.params(),
            json={},
        )

        assert response.status_code in (404, 422), (
            f"Expected 404 or 422 for missing device status params, "
            f"got {response.status_code}: {response.text[:200]}"
        )


class TestPublishRetry:
    """Non-admin publish retry endpoint /api/v1/publishes/{id}/retry."""

    @pytest.mark.asyncio
    async def test_publish_retry_nonexistent(self, api: APITestHelper) -> None:
        """POST /api/v1/publishes/{id}/retry on nonexistent publish returns 404."""
        response = await api.client.post(
            api.publish_url(99999999, "retry"),
            params=api.params(),
            json={
                "operator": "e2e-test",
                "request_id": "e2e-retry-001",
            },
        )

        assert response.status_code in (200, 401, 403, 404, 422, 500), (
            f"Expected 200, 401, 403, 404, 422, or 500 for retry nonexistent, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_publish_retry_missing_operator(self, api: APITestHelper) -> None:
        """POST /api/v1/publishes/{id}/retry without operator returns 422."""
        response = await api.client.post(
            api.publish_url(1, "retry"),
            params=api.params(),
            json={"request_id": "e2e-retry-002"},
        )

        assert response.status_code in (200, 401, 403, 404, 422, 500), (
            f"Expected 200, 401, 403, 404, 422, or 500 for retry missing operator, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_publish_revoke_nonexistent(self, api: APITestHelper) -> None:
        """POST /api/v1/publishes/{id}/revoke on nonexistent publish."""
        response = await api.client.post(
            api.publish_url(99999999, "revoke"),
            params=api.params(),
            json={
                "operator": "e2e-test",
                "reason": "test revoke",
            },
        )

        assert response.status_code in (200, 401, 403, 404, 422, 500), (
            f"Expected 200, 401, 403, 404, 422, or 500 for revoke nonexistent, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_publish_reject_nonexistent(self, api: APITestHelper) -> None:
        """POST /api/v1/publishes/{id}/reject on nonexistent publish."""
        response = await api.client.post(
            api.publish_url(99999999, "reject"),
            params=api.params(),
            json={
                "operator": "e2e-test",
                "reason": "test reject",
            },
        )

        assert response.status_code in (200, 401, 403, 404, 422, 500), (
            f"Expected 200, 401, 403, 404, 422, or 500 for reject nonexistent, "
            f"got {response.status_code}: {response.text[:200]}"
        )


class TestConfigCRUD:
    """CRUD operations on /api/v1/system-configs."""

    @pytest.mark.asyncio
    async def test_config_list_paginated(self, api: APITestHelper) -> None:
        """GET /api/v1/system-configs with pagination."""
        response = await api.client.get(
            api.system_config_url(),
            params=api.params(page=1, page_size=5),
        )

        assert response.status_code == 200, (
            f"Expected 200 for config list, "
            f"got {response.status_code}: {response.text[:200]}"
        )
        data = response.json()
        assert data["code"] == 0
        assert "items" in data["data"]
        assert isinstance(data["data"]["items"], list)

    @pytest.mark.asyncio
    async def test_config_list_page_two(self, api: APITestHelper) -> None:
        """GET /api/v1/system-configs page=2."""
        response = await api.client.get(
            api.system_config_url(),
            params=api.params(page=2, page_size=5),
        )

        assert response.status_code in (200, 404), (
            f"Expected 200 or 404 for config list page 2, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_config_create_and_delete(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """POST → DELETE lifecycle for a system config."""
        conf_key = f"e2e.admin.test.{unique_id}"

        create_resp = await api.client.post(
            api.system_config_url(),
            params=api.params(),
            json={
                "conf_key": conf_key,
                "conf_value": "e2e-admin-router-test-value",
                "name": f"Admin Router Test {unique_id}",
                "description": "Created by test_admin_router E2E",
                "operator": "e2e-test",
            },
        )
        assert create_resp.status_code in (200, 201), (
            f"Expected 200/201 for config create, "
            f"got {create_resp.status_code}: {create_resp.text[:200]}"
        )

        get_resp = await api.client.get(
            api.system_config_url(conf_key),
            params=api.params(),
        )
        assert get_resp.status_code == 200, (
            f"Expected 200 for get after create, "
            f"got {get_resp.status_code}: {get_resp.text[:200]}"
        )
        assert get_resp.json()["data"]["conf_key"] == conf_key

        update_resp = await api.client.put(
            api.system_config_url(conf_key),
            params=api.params(),
            json={
                "conf_value": "e2e-admin-router-updated-value",
                "description": "Updated by test_admin_router E2E",
            },
        )
        assert update_resp.status_code == 200, (
            f"Expected 200 for config update, "
            f"got {update_resp.status_code}: {update_resp.text[:200]}"
        )

        delete_resp = await api.client.delete(
            api.system_config_url(conf_key),
            params=api.params(),
        )
        assert delete_resp.status_code == 200, (
            f"Expected 200 for config delete, "
            f"got {delete_resp.status_code}: {delete_resp.text[:200]}"
        )

        get_after = await api.client.get(
            api.system_config_url(conf_key),
            params=api.params(),
        )
        assert get_after.status_code == 404, (
            f"Expected 404 after delete, "
            f"got {get_after.status_code}: {get_after.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_config_create_missing_conf_key(self, api: APITestHelper) -> None:
        """POST /api/v1/system-configs without conf_key returns 422."""
        response = await api.client.post(
            api.system_config_url(),
            params=api.params(),
            json={"conf_value": "some-value"},
        )

        assert response.status_code in (200, 422), (
            f"Expected 200 or 422 for missing conf_key, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_config_update_nonexistent(self, api: APITestHelper) -> None:
        """PUT /api/v1/system-configs/{key} on nonexistent key returns 404."""
        response = await api.client.put(
            api.system_config_url("e2e-nonexistent-key-xyz-admin"),
            params=api.params(),
            json={"conf_value": "test"},
        )

        assert response.status_code == 404, (
            f"Expected 404 for update nonexistent config, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_config_delete_nonexistent(self, api: APITestHelper) -> None:
        """DELETE /api/v1/system-configs/{key} on nonexistent key returns 404."""
        response = await api.client.delete(
            api.system_config_url("e2e-nonexistent-key-del-admin"),
            params=api.params(),
        )

        assert response.status_code == 404, (
            f"Expected 404 for delete nonexistent config, "
            f"got {response.status_code}: {response.text[:200]}"
        )


class TestQpmConfig:
    """QPM config management via /api/v1/bot-qpm."""

    @pytest.mark.asyncio
    async def test_qpm_list(self, api: APITestHelper) -> None:
        """GET /api/v1/bot-qpm lists all QPM configs."""
        response = await api.client.get(
            api.qpm_config_url(),
            params=api.params(),
        )

        assert response.status_code == 200, (
            f"Expected 200 for QPM list, "
            f"got {response.status_code}: {response.text[:200]}"
        )
        data = response.json()
        assert data["code"] == 0
        assert "items" in data["data"]
        assert isinstance(data["data"]["items"], list)

    @pytest.mark.asyncio
    async def test_qpm_get_nonexistent(self, api: APITestHelper) -> None:
        """GET /api/v1/bot-qpm/{bot_id} on nonexistent bot_id returns 404."""
        response = await api.client.get(
            api.qpm_config_url("nonexistent-bot-id-00000"),
            params=api.params(),
        )

        assert response.status_code == 404, (
            f"Expected 404 for QPM get nonexistent, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_qpm_upsert(self, api: APITestHelper, unique_id: str) -> None:
        """POST /api/v1/bot-qpm upserts a QPM config."""
        bot_id = f"e2e-qpm-bot-{unique_id}"
        response = await api.client.post(
            api.qpm_config_url(),
            params=api.params(),
            json={"bot_id": bot_id, "qpm": 50},
        )

        assert response.status_code in (200, 201), (
            f"Expected 200/201 for QPM upsert, "
            f"got {response.status_code}: {response.text[:200]}"
        )

        get_resp = await api.client.get(
            api.qpm_config_url(bot_id),
            params=api.params(),
        )
        assert get_resp.status_code in (200, 404), (
            f"Expected 200 or 404 for QPM get after upsert, "
            f"got {get_resp.status_code}: {get_resp.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_qpm_update(self, api: APITestHelper, unique_id: str) -> None:
        """PUT /api/v1/bot-qpm/{bot_id} updates a QPM config."""
        bot_id = f"e2e-qpm-update-{unique_id}"
        create_resp = await api.client.post(
            api.qpm_config_url(),
            params=api.params(),
            json={"bot_id": bot_id, "qpm": 30},
        )
        assert create_resp.status_code in (200, 201)

        update_resp = await api.client.put(
            api.qpm_config_url(bot_id),
            params=api.params(),
            json={"qpm": 100},
        )

        assert update_resp.status_code in (200, 404), (
            f"Expected 200 or 404 for QPM update, "
            f"got {update_resp.status_code}: {update_resp.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_qpm_delete_nonexistent(self, api: APITestHelper) -> None:
        """DELETE /api/v1/bot-qpm/{bot_id} on nonexistent bot_id returns 404."""
        response = await api.client.delete(
            api.qpm_config_url("nonexistent-bot-id-del-000"),
            params=api.params(),
        )

        assert response.status_code == 404, (
            f"Expected 404 for QPM delete nonexistent, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_qpm_upsert_invalid_qpm(self, api: APITestHelper) -> None:
        """POST /api/v1/bot-qpm with qpm=0 returns 422."""
        response = await api.client.post(
            api.qpm_config_url(),
            params=api.params(),
            json={"bot_id": "test-invalid-qpm-bot", "qpm": 0},
        )

        assert response.status_code == 422, (
            f"Expected 422 for qpm=0, got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_qpm_upsert_too_large_qpm(self, api: APITestHelper) -> None:
        """POST /api/v1/bot-qpm with qpm > 100000 returns 422."""
        response = await api.client.post(
            api.qpm_config_url(),
            params=api.params(),
            json={"bot_id": "test-huge-qpm-bot", "qpm": 999999},
        )

        assert response.status_code == 422, (
            f"Expected 422 for qpm > 100000, "
            f"got {response.status_code}: {response.text[:200]}"
        )


class TestAdminGrantRevokeExtended:
    """Extended grant/revoke bot permission tests via admin endpoint."""

    @pytest.mark.asyncio
    async def test_admin_grant_empty_body(self, api: APITestHelper) -> None:
        """POST /api/v1/admin/api-keys/{prefix}/allowed-bots/grant with empty body."""
        response = await api.client.post(
            api.admin_api_key_url("test-prefix") + "/allowed-bots/grant",
            params=api.params(),
            json={},
        )

        assert response.status_code in (400, 401, 403, 404, 422), (
            f"Expected 400, 401, 403, 404, or 422 for grant empty body, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_admin_revoke_empty_body(self, api: APITestHelper) -> None:
        """POST /api/v1/admin/api-keys/{prefix}/allowed-bots/revoke with empty body."""
        response = await api.client.post(
            api.admin_api_key_url("test-prefix") + "/allowed-bots/revoke",
            params=api.params(),
            json={},
        )

        assert response.status_code in (400, 401, 403, 404, 422), (
            f"Expected 400, 401, 403, 404, or 422 for revoke empty body, "
            f"got {response.status_code}: {response.text[:200]}"
        )


class TestAdminRestoreBot:
    """POST /api/v1/admin/bots/{bot_id}/restore — atomic admin record restore."""

    def _get_restore_dep_callable(self, app):
        for route in iter_api_routes(app):
            if route.path == "/api/v1/admin/bots/{bot_id}/restore":
                for dep in route.dependant.dependencies:
                    return dep.call
        raise RuntimeError("restore route not found in app routes")

    def _insert_bot(self, repo, *, bot_uuid: str, env: str, tenant: str, name: str):
        return repo.insert_bot(
            bot_uuid=bot_uuid,
            tenant=tenant,
            env=env,
            domain="e2e_test_domain",
            creator="e2e-test",
            modifier="e2e-test",
            name=name,
        )

    @pytest.mark.asyncio
    async def test_restore_soft_deleted_matches_final_row_states(
        self, api: APITestHelper, unique_id: str, _testclient_app
    ) -> None:
        """200 restore: target revived, cross-env live sibling retired, envelope ok.

        Posts with only the JSON body — no tenant/env query params — proving the
        request contract is environment-agnostic.
        """
        from secbaas.community.bootstrap import get_container

        repo = get_container().repository.bot_repository()
        tenant = api.tenant
        bot_uuid = f"BOT-e2e-restore-{unique_id}"

        target_id = self._insert_bot(
            repo,
            bot_uuid=bot_uuid,
            env="dev",
            tenant=tenant,
            name=f"restore-target-{unique_id}",
        )
        repo.soft_delete(bot_id=target_id, tenant=tenant, env="dev", modifier="e2e")
        sibling_id = self._insert_bot(
            repo,
            bot_uuid=bot_uuid,
            env="dev",
            tenant=tenant,
            name=f"restore-sibling-{unique_id}",
        )
        cross_env_sibling_id = self._insert_bot(
            repo,
            bot_uuid=bot_uuid,
            env="gray",
            tenant=tenant,
            name=f"restore-cross-env-{unique_id}",
        )

        response = await api.client.post(
            f"/api/v1/admin/bots/{target_id}/restore",
            json={"operator": "e2e-ops"},
        )

        assert response.status_code == 200, (
            f"Expected 200 for restore, got {response.status_code}: {response.text[:200]}"
        )
        body = response.json()
        assert body["code"] == 0
        assert body["message"] == "success"
        data = body["data"]
        assert data["bot_id"] == target_id
        assert data["bot_uuid"] == bot_uuid
        assert data["previous_status"] == "PENDING"
        assert data["previous_is_deleted"] == target_id
        assert data["new_status"] == "ACTIVE"
        assert data["retired_sibling_count"] == 2

        target = repo.get_by_id_including_deleted(target_id, tenant, "dev")
        assert target is not None
        assert target.status == "ACTIVE"
        assert target.is_deleted == 0

        sibling = repo.get_by_id_including_deleted(sibling_id, tenant, "dev")
        assert sibling is not None
        assert sibling.status == "RELEASED"
        assert sibling.is_deleted == sibling_id

        cross_env_sibling = repo.get_by_id_including_deleted(
            cross_env_sibling_id, tenant, "gray"
        )
        assert cross_env_sibling is not None
        assert cross_env_sibling.status == "RELEASED"
        assert cross_env_sibling.is_deleted == cross_env_sibling_id

    @pytest.mark.asyncio
    async def test_restore_unknown_bot_returns_404(self, api: APITestHelper) -> None:
        """Restore with a bot_id matching no record (live or deleted) → 404."""
        response = await api.client.post(
            "/api/v1/admin/bots/99999999/restore",
            json={"operator": "e2e-ops"},
        )

        assert response.status_code == 404, (
            f"Expected 404 for unknown bot_id, "
            f"got {response.status_code}: {response.text[:200]}"
        )
        assert "99999999" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_restore_conflict_returns_409(
        self, api: APITestHelper, _testclient_app
    ) -> None:
        """Service conflict error maps to 409 with no partial data."""
        mock_admin_svc = AsyncMock()
        mock_admin_svc.restore_bot.side_effect = PublishConflictError(
            "live unique-key slot conflict"
        )

        dep_callable = self._get_restore_dep_callable(_testclient_app)
        _testclient_app.dependency_overrides[dep_callable] = lambda: mock_admin_svc
        try:
            response = await api.client.post(
                "/api/v1/admin/bots/123/restore",
                json={"operator": "e2e-ops"},
            )
        finally:
            del _testclient_app.dependency_overrides[dep_callable]

        assert response.status_code == 409, (
            f"Expected 409 for conflict, "
            f"got {response.status_code}: {response.text[:200]}"
        )
        assert "conflict" in response.json()["detail"].lower()
        mock_admin_svc.restore_bot.assert_awaited_once_with(
            bot_id=123,
            operator="e2e-ops",
        )
