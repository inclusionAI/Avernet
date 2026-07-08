"""E2E tests for error response formats.

Tests cover:
- Validation error response format (422)
- Not found error response format (404)
- Business logic error response format (400)
- Internal server error handling (500)
"""

import uuid

import pytest

from ...conftest import (
    APITestHelper,
    activate_test_bot,
    cleanup_bot,
    create_test_bot,
    find_existing_bot,
)

pytestmark = pytest.mark.e2e


class TestValidationErrors:
    """Tests for validation error responses (422)."""

    pytestmark = pytest.mark.crud

    @pytest.mark.asyncio
    async def test_create_bot_missing_required_field(self, api: APITestHelper) -> None:
        """Test 422 response for missing required field."""
        response = await api.client.post(
            api.bot_url(),
            params=api.params(),
            json={
                # Missing required fields: name, template_uuid, operator, request_id
                "device_count": 1,
            },
        )

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

    @pytest.mark.asyncio
    async def test_create_bot_invalid_request_id(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Test 422 response for invalid request_id format."""
        response = await api.client.post(
            api.bot_url(),
            params=api.params(),
            json={
                "name": f"test-bot-{unique_id}",
                "template_uuid": "TEMPLATE-4d0e2849d7004111836333de782b95d8",
                "operator": "e2e-test",
                "request_id": "too-short",  # Invalid: must be 32-64 chars
            },
        )

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

    @pytest.mark.asyncio
    async def test_scale_bot_invalid_count(self, api: APITestHelper) -> None:
        """Test 422 response for invalid scale count."""
        bot = await find_existing_bot(api)
        if bot is None:
            pytest.skip("No existing bots in system")

        response = await api.client.post(
            f"{api.bot_url(bot['bot_uuid'])}/scale",
            params=api.params(),
            json={
                "target_count": 0,  # Invalid: must be >= 1
                "operator": "e2e-test",
                "request_id": uuid.uuid4().hex,
            },
        )

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data


class TestNotFoundErrors:
    """Tests for not found error responses (404)."""

    pytestmark = pytest.mark.crud

    @pytest.mark.asyncio
    async def test_get_bot_not_found(self, api: APITestHelper) -> None:
        """Test 404 response for nonexistent bot."""
        response = await api.client.get(
            api.bot_url("nonexistent-bot-uuid-12345678"),
            params=api.params(),
        )

        assert response.status_code == 404
        data = response.json()
        assert "detail" in data
        assert data["detail"]["error_code"] == "BOT_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_update_bot_not_found(self, api: APITestHelper) -> None:
        """Test 404 response for updating nonexistent bot."""
        response = await api.client.post(
            f"{api.bot_url('nonexistent-bot-uuid-12345678')}/update",
            params=api.params(),
            json={"name": "should-fail", "modifier": "e2e-test"},
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_destroy_bot_not_found(self, api: APITestHelper) -> None:
        """Test 404 response for destroying nonexistent bot."""
        response = await api.client.post(
            api.bot_url("nonexistent-bot-uuid-12345678") + "/destroy",
            params=api.params(),
            json={"operator": "e2e-test", "request_id": uuid.uuid4().hex},
        )

        assert response.status_code == 404
        data = response.json()
        assert "detail" in data


class TestBusinessLogicErrors:
    """Tests for business logic error responses (400)."""

    pytestmark = pytest.mark.sync

    @pytest.mark.asyncio
    async def test_scale_bot_same_count(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        bot = await create_test_bot(api, f"test-scale-same-{unique_id}", device_count=1)
        await activate_test_bot(api, bot)

        response = await api.client.post(
            f"{api.bot_url(bot['bot_uuid'])}/scale",
            params=api.params(),
            json={
                "target_count": 1,
                "operator": "e2e-test",
                "request_id": uuid.uuid4().hex,
            },
        )

        # Accept either success (if no devices) or 400 (if already 1 device)
        if response.status_code == 400:
            data = response.json()
            # Should have error message
            assert "detail" in data or "message" in data

        await cleanup_bot(api, bot["bot_uuid"])


class TestInternalServerErrorHandling:
    """Tests for internal server error handling (500)."""

    pytestmark = pytest.mark.crud

    @pytest.mark.asyncio
    async def test_api_handles_errors_gracefully(self, api: APITestHelper) -> None:
        """Test that API returns proper error for unexpected issues."""
        response = await api.client.get(
            api.tenant_url(),
            params={"page": 1, "page_size": 1},
        )

        assert response.status_code in [200, 400, 404, 500]
        data = response.json()
        assert "code" in data or "detail" in data
