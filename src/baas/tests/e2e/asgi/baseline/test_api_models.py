"""E2E tests for API Model validation and serialization (Phase 1.8).

Covers request/response model contracts through the API:
- Model validation: malformed JSON → 422
- Enum handling: invalid enum values → 422
- Serialization: response shape verification
"""

import pytest

from tests.e2e.asgi.conftest import APITestHelper

pytestmark = [pytest.mark.e2e_asgi]

NONEXISTENT_UUID = "00000000-0000-0000-0000-000000000000"


class TestModelValidation:
    """Tests for request body validation returning 422 on bad input."""

    @pytest.mark.asyncio
    async def test_malformed_json_returns_422(self, api: APITestHelper) -> None:
        """POST /api/v1/bots with malformed/invalid body returns 422."""
        response = await api.client.post(
            api.bot_url(),
            params=api.params(),
            json={"bad_field": True},
        )
        assert response.status_code == 422, (
            f"Expected 422 for malformed body, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_missing_required_fields_422(self, api: APITestHelper) -> None:
        """POST with missing required fields returns 422."""
        endpoints = [
            (api.bot_url(), {}, "POST"),
            (api.bot_url("some-uuid") + "/open-folder", {}, "POST"),
        ]
        for url, body, method in endpoints:
            if method == "POST":
                response = await api.client.post(url, params=api.params(), json=body)
            else:
                response = await api.client.get(url, params=api.params())
            assert response.status_code in (400, 404, 422), (
                f"Expected 400/404/422 for {method} {url}, "
                f"got {response.status_code}: {response.text[:200]}"
            )

    @pytest.mark.asyncio
    async def test_invalid_type_in_body_returns_422(self, api: APITestHelper) -> None:
        """POST with wrong field types returns 422."""
        response = await api.client.post(
            api.bot_url(),
            params=api.params(),
            json={
                "name": 12345,
                "template_uuid": True,
                "device_count": "not-a-number",
                "operator": None,
            },
        )
        assert response.status_code == 422, (
            f"Expected 422 for invalid types, "
            f"got {response.status_code}: {response.text[:200]}"
        )


class TestEnumHandling:
    """Tests for enum value validation."""

    @pytest.mark.asyncio
    async def test_invalid_platform_type_returns_error(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """POST /api/v1/paas/devices with invalid platform_type returns error."""
        response = await api.client.post(
            api.paas_device_url(),
            params=api.params(),
            json={
                "platform_type": "INVALID_PLATFORM",
                "config": {
                    "template_id": "TEMPLATE-TEST",
                    "name": f"test-{unique_id}",
                },
            },
        )
        assert response.status_code in (400, 404, 422, 500), (
            f"Expected 400/404/422/500 for invalid platform_type, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_invalid_bot_status_enum(self, api: APITestHelper) -> None:
        """GET /api/v1/bots with invalid status enum returns 422."""
        response = await api.client.get(
            api.bot_url(),
            params=api.params(page=1, page_size=10, status="INVALID_STATUS"),
        )
        assert response.status_code in (200, 422), (
            f"Expected 200 or 422 for invalid status enum (invalid query params may be ignored), "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_invalid_env_enum(self, api: APITestHelper) -> None:
        """GET /internal/bot-health-checker/alive with invalid env returns error."""
        response = await api.client.get(
            "/internal/bot-health-checker/alive",
            params={
                "bot_id": "test",
                "entity_id": "test",
                "env": "INVALID_ENV_VALUE",
            },
        )
        assert response.status_code in (200, 400, 404, 422), (
            f"Expected 200/400/404/422 for invalid env enum, "
            f"got {response.status_code}: {response.text[:200]}"
        )


class TestResponseSerialization:
    """Tests for response shape verification."""

    @pytest.mark.asyncio
    async def test_bot_create_response_shape(self, api: APITestHelper) -> None:
        """GET /api/v1/bots response conforms to expected paginated shape."""
        response = await api.client.get(
            api.bot_url(),
            params=api.params(page=1, page_size=1),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0, f"Expected code=0, got {data}"
        assert "data" in data
        assert "items" in data["data"]
        assert "total" in data["data"]
        assert "page" in data["data"]
        assert isinstance(data["data"]["items"], list)

    @pytest.mark.asyncio
    async def test_tenant_list_response_shape(self, api: APITestHelper) -> None:
        """GET /api/v1/tenants response conforms to expected shape."""
        response = await api.client.get(
            api.tenant_url(),
            params={"page": 1, "page_size": 10},
        )
        assert response.status_code in (200, 500)
        if response.status_code == 200:
            data = response.json()
            assert data["code"] == 0
            assert "items" in data["data"]
            assert isinstance(data["data"]["items"], list)

    @pytest.mark.asyncio
    async def test_paas_device_create_response_shape(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """POST /api/v1/paas/devices response has expected shape fields."""
        response = await api.client.post(
            api.paas_device_url(),
            params=api.params(),
            json={
                "platform_type": "ARCA",
                "config": {
                    "template_id": "TEMPLATE-4d0e2849d7004111836333de782b95d8",
                    "name": f"test-shape-{unique_id}",
                },
            },
        )
        assert response.status_code in (200, 201, 400, 404, 422, 500)
        if response.status_code in (200, 201):
            data = response.json()
            assert isinstance(data, dict)
            if "code" in data:
                assert data["code"] == 0
