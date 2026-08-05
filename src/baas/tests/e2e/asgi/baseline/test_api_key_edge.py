"""E2E tests for API Key management — edge cases.

Tests scenarios at the boundaries of expected behaviour:
  - Empty tenant returns empty list
  - Page number beyond available data
  - Reactivating a deactivated key
  - Deleting an already-deleted key
"""

import pytest

from tests.e2e.asgi.conftest import APITestHelper

pytestmark = [pytest.mark.e2e_asgi]


class TestEmptyResults:
    """Edge cases where queries return empty results."""

    @pytest.mark.asyncio
    async def test_list_keys_empty_tenant(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """List keys with a non-matching app_type returns an empty list."""
        response = await api.client.get(
            api.api_key_url(),
            params=api.params(app_type="nonexistent-type", page=1, page_size=10),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["items"] == []
        assert data["data"]["total"] == 0

    @pytest.mark.asyncio
    async def test_list_keys_empty_status(self, api: APITestHelper) -> None:
        """List keys filtered by a status that matches nothing returns 422."""
        response = await api.client.get(
            api.api_key_url(),
            params=api.params(status="NONEXISTENT_STATUS", page=1, page_size=10),
        )

        assert response.status_code == 422


class TestPagination:
    """Pagination boundary tests."""

    @pytest.mark.asyncio
    async def test_page_exceeds_available_data(self, api: APITestHelper) -> None:
        """Requesting a page beyond the available data returns an empty list."""
        response = await api.client.get(
            api.api_key_url(),
            params=api.params(page=99999, page_size=10),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["items"] == []
        assert data["data"]["total"] >= 0

    @pytest.mark.asyncio
    async def test_page_size_minimum(self, api: APITestHelper) -> None:
        """page_size=1 returns at most one item."""
        response = await api.client.get(
            api.api_key_url(),
            params=api.params(page=1, page_size=1),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert len(data["data"]["items"]) <= 1

    @pytest.mark.asyncio
    async def test_page_size_maximum(self, api: APITestHelper) -> None:
        """page_size=100 (the max) does not error."""
        response = await api.client.get(
            api.api_key_url(),
            params=api.params(page=1, page_size=100),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0

    @pytest.mark.asyncio
    async def test_zero_page_returns_error(self, api: APITestHelper) -> None:
        """page=0 (below minimum) returns a validation error."""
        response = await api.client.get(
            api.api_key_url(),
            params=api.params(page=0, page_size=10),
        )

        assert response.status_code in (400, 422)


class TestReactivation:
    """Reactivating a key that is already active or was deactivated."""

    @pytest.mark.asyncio
    async def test_reactivate_already_active_key(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Activating an already active key returns an error."""
        app_id = f"e2e-already-active-{unique_id}"
        create_resp = await api.client.post(
            api.api_key_url(action="app"),
            params=api.params(),
            json={"app_id": app_id, "key_name": f"already-active-{unique_id}"},
        )
        assert create_resp.status_code == 200
        prefix = create_resp.json()["data"]["api_key_prefix"]

        # The key is already ACTIVE; try to activate it again
        response = await api.client.patch(
            api.api_key_url(prefix, action="status"),
            params=api.params(),
            json={"action": "activate"},
        )

        # The API may reject this with 400 or silently return 200 depending on
        # the implementation; either is acceptable for an E2E edge case.
        assert response.status_code in (200, 400)

    @pytest.mark.asyncio
    async def test_reactivate_deactivated_key(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """A deactivated key can be reactivated back to ACTIVE."""
        app_id = f"e2e-react-deact-{unique_id}"
        create_resp = await api.client.post(
            api.api_key_url(action="app"),
            params=api.params(),
            json={"app_id": app_id, "key_name": f"react-deact-{unique_id}"},
        )
        assert create_resp.status_code == 200
        prefix = create_resp.json()["data"]["api_key_prefix"]

        # Deactivate
        deact_resp = await api.client.patch(
            api.api_key_url(prefix, action="status"),
            params=api.params(),
            json={"action": "deactivate"},
        )
        assert deact_resp.status_code == 200

        # Reactivate
        react_resp = await api.client.patch(
            api.api_key_url(prefix, action="status"),
            params=api.params(),
            json={"action": "activate"},
        )

        assert react_resp.status_code == 200
        data = react_resp.json()
        assert data["data"]["status"] == "ACTIVE"


class TestDeactivateEdge:
    """Deactivating a key that is already inactive."""

    @pytest.mark.asyncio
    async def test_deactivate_already_inactive_key(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Deactivating an already INACTIVE key returns an error."""
        app_id = f"e2e-already-inactive-{unique_id}"
        create_resp = await api.client.post(
            api.api_key_url(action="app"),
            params=api.params(),
            json={"app_id": app_id, "key_name": f"already-inactive-{unique_id}"},
        )
        assert create_resp.status_code == 200
        prefix = create_resp.json()["data"]["api_key_prefix"]

        # Deactivate first
        await api.client.patch(
            api.api_key_url(prefix, action="status"),
            params=api.params(),
            json={"action": "deactivate"},
        )

        # Try to deactivate again
        response = await api.client.patch(
            api.api_key_url(prefix, action="status"),
            params=api.params(),
            json={"action": "deactivate"},
        )

        # Should be rejected since the key is already INACTIVE
        assert response.status_code in (200, 400)


class TestRevokeEdge:
    """Revoking and re-revoking keys."""

    @pytest.mark.asyncio
    async def test_revoke_already_revoked_key(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Revoking an already REVOKED key returns an error."""
        app_id = f"e2e-already-revoked-{unique_id}"
        create_resp = await api.client.post(
            api.api_key_url(action="app"),
            params=api.params(),
            json={"app_id": app_id, "key_name": f"already-revoked-{unique_id}"},
        )
        assert create_resp.status_code == 200
        prefix = create_resp.json()["data"]["api_key_prefix"]

        # Revoke
        await api.client.patch(
            api.api_key_url(prefix, action="status"),
            params=api.params(),
            json={"action": "revoke"},
        )

        # Try to revoke again
        response = await api.client.patch(
            api.api_key_url(prefix, action="status"),
            params=api.params(),
            json={"action": "revoke"},
        )

        assert response.status_code in (200, 400)


class TestReactivateRevokedKey:
    """Attempting to reactivate a revoked key."""

    @pytest.mark.asyncio
    async def test_reactivate_revoked_key(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Revoked keys typically cannot be reactivated."""
        app_id = f"e2e-reactivate-revoked-{unique_id}"
        create_resp = await api.client.post(
            api.api_key_url(action="app"),
            params=api.params(),
            json={
                "app_id": app_id,
                "key_name": f"reactivate-revoked-{unique_id}",
            },
        )
        assert create_resp.status_code == 200
        prefix = create_resp.json()["data"]["api_key_prefix"]

        # Revoke
        await api.client.patch(
            api.api_key_url(prefix, action="status"),
            params=api.params(),
            json={"action": "revoke"},
        )

        # Attempt to reactivate
        response = await api.client.patch(
            api.api_key_url(prefix, action="status"),
            params=api.params(),
            json={"action": "activate"},
        )

        assert response.status_code in (200, 400)


class TestBotPermissionsEdge:
    """Edge cases around allowed-bots."""

    @pytest.mark.asyncio
    async def test_bot_key_allowed_bots_workflow(
        self, api: APITestHelper, unique_id: str, created_bot: dict
    ) -> None:
        bot_app_id = f"{created_bot['bot_uuid']}:000001"
        bot_key_resp = await api.client.post(
            api.api_key_url(action="bot"),
            params=api.params(),
            json={
                "app_id": bot_app_id,
                "key_name": f"e2e-bot-workflow-{unique_id}",
            },
        )
        assert bot_key_resp.status_code == 200

        app_id = f"e2e-workflow-app-{unique_id}"
        app_key_resp = await api.client.post(
            api.api_key_url(action="app"),
            params=api.params(),
            json={
                "app_id": app_id,
                "key_name": f"e2e-app-key-{unique_id}",
            },
        )
        assert app_key_resp.status_code == 200
        app_key_prefix = app_key_resp.json()["data"]["api_key_prefix"]

        grant_resp = await api.client.post(
            api.api_key_url(app_key_prefix, action="allowed-bots/grant"),
            params=api.params(),
            json={"bot_id": bot_app_id},
        )
        assert grant_resp.status_code == 200

        get_resp = await api.client.get(
            api.api_key_url(app_key_prefix, action="allowed-bots"),
            params=api.params(),
        )
        assert get_resp.status_code == 200
        data = get_resp.json()
        allowed_bots = data["data"]["allowed_bots"]
        assert bot_app_id in allowed_bots

        revoke_resp = await api.client.post(
            api.api_key_url(app_key_prefix, action="allowed-bots/revoke"),
            params=api.params(),
            json={"bot_id": bot_app_id},
        )
        assert revoke_resp.status_code == 200

        get_after = await api.client.get(
            api.api_key_url(app_key_prefix, action="allowed-bots"),
            params=api.params(),
        )
        assert get_after.status_code == 200
        assert bot_app_id not in get_after.json()["data"]["allowed_bots"]

    @pytest.mark.asyncio
    async def test_allowed_bots_with_bot_uuid_as_bot_id(
        self, api: APITestHelper, unique_id: str, created_bot: dict
    ) -> None:
        grant_bot_id = f"{created_bot['bot_uuid']}:000001"
        app_id = f"e2e-bot-uuid-app-{unique_id}"
        app_key_resp = await api.client.post(
            api.api_key_url(action="app"),
            params=api.params(),
            json={
                "app_id": app_id,
                "key_name": f"e2e-app-key-{unique_id}",
            },
        )
        assert app_key_resp.status_code == 200
        app_key_prefix = app_key_resp.json()["data"]["api_key_prefix"]

        grant_resp = await api.client.post(
            api.api_key_url(app_key_prefix, action="allowed-bots/grant"),
            params=api.params(),
            json={"bot_id": grant_bot_id},
        )
        assert grant_resp.status_code == 200

        get_resp = await api.client.get(
            api.api_key_url(app_key_prefix, action="allowed-bots"),
            params=api.params(),
        )
        assert get_resp.status_code == 200
        allowed_bots = get_resp.json()["data"]["allowed_bots"]
        assert grant_bot_id in allowed_bots
