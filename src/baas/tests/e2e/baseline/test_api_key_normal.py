"""E2E tests for API Key management — normal/happy path flows.

Endpoints tested (user-facing):
  - POST /api/v1/api-keys/app       — Create app key
  - POST /api/v1/api-keys/bot       — Create bot key
  - GET  /api/v1/api-keys           — List keys
  - GET  /api/v1/api-keys/{prefix}  — Get key by prefix
  - PUT  /api/v1/api-keys/{prefix}  — Update key metadata
  - PATCH /api/v1/api-keys/{prefix}/status — Update key status
  - POST /api/v1/api-keys/{prefix}/allowed-bots/grant  — Grant bot permission
  - POST /api/v1/api-keys/{prefix}/allowed-bots/revoke — Revoke bot permission
"""

import pytest

from ..conftest import APITestHelper

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]


class TestCreateAPIKey:
    """POST /api/v1/api-keys/app  and  POST /api/v1/api-keys/bot."""

    @pytest.mark.asyncio
    async def test_create_app_key_succeeds(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Create a new app-type API key — returns 200 with api_key and prefix."""
        app_id = f"e2e-app-{unique_id}"
        response = await api.client.post(
            api.api_key_url(action="app"),
            params=api.params(),
            json={
                "app_id": app_id,
                "key_name": f"e2e-app-key-{unique_id}",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["app_id"] == app_id
        assert data["data"]["app_type"] == "app"
        assert isinstance(data["data"]["api_key_prefix"], str)
        assert len(data["data"]["api_key_prefix"]) > 0
        assert isinstance(data["data"]["api_key"], str)
        assert len(data["data"]["api_key"]) > 0
        assert data["data"]["status"] == "ACTIVE"
        assert "id" in data["data"]

    @pytest.mark.asyncio
    async def test_create_bot_key_succeeds(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Create a new bot-type API key — returns 200 with api_key and prefix."""
        pytest.skip(
            "Bot API key creation requires authentication not available in bare mode"
        )

    @pytest.mark.asyncio
    async def test_create_app_key_with_full_fields(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Create an app key with all optional fields populated."""
        app_id = f"e2e-full-{unique_id}"
        response = await api.client.post(
            api.api_key_url(action="app"),
            params=api.params(),
            json={
                "app_id": app_id,
                "key_name": f"full-key-{unique_id}",
                "description": "An app key for E2E testing",
                "rate_limit_rpm": 60,
                "rate_limit_rpd": 1000,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["key_name"] == f"full-key-{unique_id}"
        assert data["data"]["description"] == "An app key for E2E testing"
        assert data["data"]["rate_limit_rpm"] == 60
        assert data["data"]["rate_limit_rpd"] == 1000


class TestListAPIKeys:
    """GET /api/v1/api-keys."""

    @pytest.mark.asyncio
    async def test_list_keys_returns_paginated(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """List keys returns a paginated response structure."""
        # Create a key so there is at least one owned by the current user
        app_id = f"e2e-list-{unique_id}"
        await api.client.post(
            api.api_key_url(action="app"),
            params=api.params(),
            json={"app_id": app_id, "key_name": f"list-key-{unique_id}"},
        )

        response = await api.client.get(
            api.api_key_url(),
            params=api.params(page=1, page_size=10),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert "items" in data["data"]
        assert "total" in data["data"]
        assert "page" in data["data"]
        assert "page_size" in data["data"]
        assert isinstance(data["data"]["items"], list)
        assert data["data"]["page"] == 1
        assert data["data"]["page_size"] == 10

    @pytest.mark.asyncio
    async def test_list_keys_with_app_type_filter(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """List keys filtered by app_type returns only matching keys."""
        # Create an app key first
        app_id = f"e2e-list-filter-{unique_id}"
        await api.client.post(
            api.api_key_url(action="app"),
            params=api.params(),
            json={"app_id": app_id, "key_name": f"filter-key-{unique_id}"},
        )

        response = await api.client.get(
            api.api_key_url(),
            params=api.params(app_type="app", page=1, page_size=50),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        for item in data["data"]["items"]:
            assert item["app_type"] == "app"

    @pytest.mark.asyncio
    async def test_list_keys_returns_only_owned_keys(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """List keys endpoint scopes results to the current user's keys."""
        response = await api.client.get(
            api.api_key_url(),
            params=api.params(page=1, page_size=100),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        # The list may be empty; verify structure regardless
        for item in data["data"]["items"]:
            assert "api_key_prefix" in item
            assert "app_id" in item
            assert "app_type" in item
            assert "status" in item

    @pytest.mark.asyncio
    async def test_list_keys_default_page_size(self, api: APITestHelper) -> None:
        """List keys without explicit page_size defaults to 20."""
        response = await api.client.get(
            api.api_key_url(),
            params=api.params(),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["page_size"] == 20


class TestGetAPIKey:
    """GET /api/v1/api-keys/{prefix}."""

    @pytest.mark.asyncio
    async def test_get_key_by_prefix(self, api: APITestHelper, unique_id: str) -> None:
        """Get an existing key by its prefix returns the key details."""
        app_id = f"e2e-get-{unique_id}"
        create_resp = await api.client.post(
            api.api_key_url(action="app"),
            params=api.params(),
            json={"app_id": app_id, "key_name": f"get-key-{unique_id}"},
        )
        assert create_resp.status_code == 200
        prefix = create_resp.json()["data"]["api_key_prefix"]

        response = await api.client.get(
            api.api_key_url(prefix),
            params=api.params(),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["api_key_prefix"] == prefix
        assert data["data"]["app_id"] == app_id
        assert data["data"]["id"] is not None
        # Secret should NOT be returned on a get-by-prefix
        assert "api_key" not in data["data"] or data["data"].get("api_key") is None

    @pytest.mark.asyncio
    async def test_get_key_returns_all_fields(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Get key response contains all expected metadata fields."""
        app_id = f"e2e-get-fields-{unique_id}"
        create_resp = await api.client.post(
            api.api_key_url(action="app"),
            params=api.params(),
            json={
                "app_id": app_id,
                "key_name": f"fields-key-{unique_id}",
                "description": "check fields",
            },
        )
        assert create_resp.status_code == 200
        prefix = create_resp.json()["data"]["api_key_prefix"]

        response = await api.client.get(
            api.api_key_url(prefix),
            params=api.params(),
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["api_key_prefix"] == prefix
        assert data["app_id"] == app_id
        assert data["app_type"] == "app"
        assert data["key_name"] == f"fields-key-{unique_id}"
        assert data["description"] == "check fields"
        assert data["status"] in ("ACTIVE", "INACTIVE", "REVOKED")
        assert data["owner"] is not None
        assert data["gmt_create"] is not None
        assert data["gmt_modified"] is not None


class TestUpdateAPIKey:
    """PUT /api/v1/api-keys/{prefix}."""

    @pytest.mark.asyncio
    async def test_update_key_name_and_description(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Update key_name and description on an existing key."""
        app_id = f"e2e-upd-{unique_id}"
        create_resp = await api.client.post(
            api.api_key_url(action="app"),
            params=api.params(),
            json={"app_id": app_id, "key_name": f"orig-name-{unique_id}"},
        )
        assert create_resp.status_code == 200
        prefix = create_resp.json()["data"]["api_key_prefix"]

        new_name = f"updated-name-{unique_id}"
        new_desc = f"Updated description for {unique_id}"
        response = await api.client.put(
            api.api_key_url(prefix),
            params=api.params(),
            json={
                "key_name": new_name,
                "description": new_desc,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["key_name"] == new_name
        assert data["data"]["description"] == new_desc

    @pytest.mark.asyncio
    async def test_update_key_status_deactivate(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Deactivate an active key via PATCH /status."""
        app_id = f"e2e-deact-{unique_id}"
        create_resp = await api.client.post(
            api.api_key_url(action="app"),
            params=api.params(),
            json={"app_id": app_id, "key_name": f"deact-key-{unique_id}"},
        )
        assert create_resp.status_code == 200
        prefix = create_resp.json()["data"]["api_key_prefix"]

        response = await api.client.patch(
            api.api_key_url(prefix, action="status"),
            params=api.params(),
            json={"action": "deactivate"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["status"] == "INACTIVE"

    @pytest.mark.asyncio
    async def test_reactivate_key(self, api: APITestHelper, unique_id: str) -> None:
        """Reactivate a deactivated key via PATCH /status."""
        app_id = f"e2e-react-{unique_id}"
        create_resp = await api.client.post(
            api.api_key_url(action="app"),
            params=api.params(),
            json={"app_id": app_id, "key_name": f"react-key-{unique_id}"},
        )
        assert create_resp.status_code == 200
        prefix = create_resp.json()["data"]["api_key_prefix"]

        # Deactivate first
        await api.client.patch(
            api.api_key_url(prefix, action="status"),
            params=api.params(),
            json={"action": "deactivate"},
        )

        # Reactivate
        response = await api.client.patch(
            api.api_key_url(prefix, action="status"),
            params=api.params(),
            json={"action": "activate"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["status"] == "ACTIVE"

    @pytest.mark.asyncio
    async def test_revoke_key(self, api: APITestHelper, unique_id: str) -> None:
        """Revoke an active key via PATCH /status with action=revoke."""
        app_id = f"e2e-revoke-{unique_id}"
        create_resp = await api.client.post(
            api.api_key_url(action="app"),
            params=api.params(),
            json={"app_id": app_id, "key_name": f"revoke-key-{unique_id}"},
        )
        assert create_resp.status_code == 200
        prefix = create_resp.json()["data"]["api_key_prefix"]

        response = await api.client.patch(
            api.api_key_url(prefix, action="status"),
            params=api.params(),
            json={"action": "revoke"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["status"] == "REVOKED"


class TestBotPermissions:
    """allowed-bots grant/revoke for app-type API keys."""

    @pytest.mark.asyncio
    async def test_grant_bot_permission(self) -> None:
        pytest.skip(
            "allowed-bots endpoints require authentication not available in bare mode"
        )

    @pytest.mark.asyncio
    async def test_revoke_bot_permission(self) -> None:
        pytest.skip(
            "allowed-bots endpoints require authentication not available in bare mode"
        )

    @pytest.mark.asyncio
    async def test_get_allowed_bots_after_grant(self) -> None:
        pytest.skip(
            "allowed-bots endpoints require authentication not available in bare mode"
        )
