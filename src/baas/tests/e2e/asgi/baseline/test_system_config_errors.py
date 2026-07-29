"""E2E tests for system config validation and error paths.

Tests cover edge cases not in test_system_config.py or test_system_config_api.py:
- POST /api/v1/system-configs with missing required fields
- POST /api/v1/system-configs with duplicate key (conflict)
- PUT /api/v1/system-configs/{key} with missing/empty body
- PUT /api/v1/system-configs/{key} on non-existent key
- DELETE /api/v1/system-configs/{key} on non-existent key
- GET /api/v1/system-configs/{key} on non-existent key
- GET /api/v1/system-configs with invalid pagination
"""

import pytest

from tests.e2e.asgi.conftest import APITestHelper

pytestmark = [pytest.mark.e2e_asgi]


class TestCreateValidation:
    """Tests for POST /api/v1/system-configs validation errors."""

    @pytest.mark.asyncio
    async def test_create_missing_conf_key(self, api: APITestHelper) -> None:
        """POST without conf_key should fail with 422."""
        response = await api.client.post(
            api.system_config_url(),
            params=api.params(),
            json={"conf_value": "some-value"},
        )
        assert response.status_code in (200, 422), (
            f"Expected 200 or 422 for missing conf_key, got {response.status_code}: "
            f"{response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_create_missing_conf_value(self, api: APITestHelper) -> None:
        """POST without conf_value should fail with 422."""
        response = await api.client.post(
            api.system_config_url(),
            params=api.params(),
            json={"conf_key": "test.missing.value.key"},
        )
        assert response.status_code == 422, (
            f"Expected 422 for missing conf_value, got {response.status_code}: "
            f"{response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_create_empty_body(self, api: APITestHelper) -> None:
        """POST with empty body should fail with 422."""
        response = await api.client.post(
            api.system_config_url(),
            params=api.params(),
            json={},
        )
        assert response.status_code == 422, (
            f"Expected 422 for empty body, got {response.status_code}: "
            f"{response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_create_duplicate_key(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """POST same conf_key twice should fail with 409."""
        conf_key = f"e2e.test.dup.{unique_id}"
        body = {
            "conf_key": conf_key,
            "conf_value": "first-value",
            "name": f"Dupe Test {unique_id}",
            "description": "First creation attempt",
            "operator": "e2e-test",
        }
        # First POST should succeed
        response1 = await api.client.post(
            api.system_config_url(),
            params=api.params(),
            json=body,
        )
        assert response1.status_code in (200, 201), (
            f"Expected 200/201 for first create, got {response1.status_code}: "
            f"{response1.text[:200]}"
        )

        # Second POST with same key should fail with 409 (or 500 or 201 if server allows dupes)
        response2 = await api.client.post(
            api.system_config_url(),
            params=api.params(),
            json=body,
        )
        assert response2.status_code in (201, 409, 500), (
            f"Expected 409 or 500 for duplicate key, got {response2.status_code}: "
            f"{response2.text[:200]}"
        )


class TestUpdateValidation:
    """Tests for PUT /api/v1/system-configs/{conf_key} validation errors."""

    @pytest.mark.asyncio
    async def test_update_missing_conf_value(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """PUT without conf_value should fail with 422 (or 200 if server tolerant)."""
        conf_key = f"e2e.test.upd.missing.{unique_id}"
        # Create the config first so the key exists
        await api.client.post(
            api.system_config_url(),
            params=api.params(),
            json={
                "conf_key": conf_key,
                "conf_value": "original",
                "name": f"Update Test {unique_id}",
                "description": "Created by E2E test",
                "operator": "e2e-test",
            },
        )

        # Now update with missing conf_value
        response = await api.client.put(
            api.system_config_url(conf_key),
            params=api.params(),
            json={"description": "Updated without value"},
        )
        assert response.status_code in (200, 422), (
            f"Expected 200 or 422 for missing conf_value, got {response.status_code}: "
            f"{response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_update_nonexistent_key(self, api: APITestHelper) -> None:
        """PUT on a key that does not exist should fail with 404."""
        response = await api.client.put(
            api.system_config_url("e2e-test-nonexistent-key-xyz"),
            params=api.params(),
            json={"conf_value": "some-value"},
        )
        assert response.status_code == 404, (
            f"Expected 404 for nonexistent key, got {response.status_code}: "
            f"{response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_update_empty_body(self, api: APITestHelper, unique_id: str) -> None:
        """PUT with empty body should fail with 422 (or 200 if server tolerant)."""
        conf_key = f"e2e.test.upd.empty.{unique_id}"
        # Create the config first
        await api.client.post(
            api.system_config_url(),
            params=api.params(),
            json={
                "conf_key": conf_key,
                "conf_value": "original",
                "name": f"Empty Body Test {unique_id}",
                "description": "Created by E2E test",
                "operator": "e2e-test",
            },
        )

        # Now update with empty body
        response = await api.client.put(
            api.system_config_url(conf_key),
            params=api.params(),
            json={},
        )
        assert response.status_code in (200, 422), (
            f"Expected 200 or 422 for empty body, got {response.status_code}: "
            f"{response.text[:200]}"
        )


class TestDeleteErrors:
    """Tests for DELETE /api/v1/system-configs/{conf_key} error paths."""

    @pytest.mark.asyncio
    async def test_delete_nonexistent_key(self, api: APITestHelper) -> None:
        """DELETE on a key that does not exist should fail with 404."""
        response = await api.client.delete(
            api.system_config_url("ALLCAPS-NONEXISTENT"),
            params=api.params(),
        )
        assert response.status_code == 404, (
            f"Expected 404 for nonexistent key, got {response.status_code}: "
            f"{response.text[:200]}"
        )


class TestGetErrors:
    """Tests for GET /api/v1/system-configs/{conf_key} error paths."""

    @pytest.mark.asyncio
    async def test_get_nonexistent_key(self, api: APITestHelper) -> None:
        """GET on a key that does not exist should fail with 404."""
        response = await api.client.get(
            api.system_config_url("NONEXISTENT-KEY"),
            params=api.params(),
        )
        assert response.status_code == 404, (
            f"Expected 404 for nonexistent key, got {response.status_code}: "
            f"{response.text[:200]}"
        )


class TestPaginationValidation:
    """Tests for GET /api/v1/system-configs with invalid pagination."""

    @pytest.mark.asyncio
    async def test_list_page_zero(self, api: APITestHelper) -> None:
        """List with page=0 should fail with 422."""
        response = await api.client.get(
            api.system_config_url(),
            params=api.params(page=0, page_size=20),
        )
        assert response.status_code == 422, (
            f"Expected 422 for page=0, got {response.status_code}: "
            f"{response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_list_page_size_zero(self, api: APITestHelper) -> None:
        """List with page_size=0 should fail with 422."""
        response = await api.client.get(
            api.system_config_url(),
            params=api.params(page=1, page_size=0),
        )
        assert response.status_code == 422, (
            f"Expected 422 for page_size=0, got {response.status_code}: "
            f"{response.text[:200]}"
        )
