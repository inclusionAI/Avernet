"""E2E tests for request validation across API endpoints.

Verifies that missing required fields, invalid types, and malformed input
are rejected with appropriate error status codes and structured responses.
"""

import pytest

from ..conftest import APITestHelper

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]


class TestEmptyBody:
    """Test behavior when empty/missing body is sent to creation endpoints."""

    @pytest.mark.asyncio
    async def test_post_empty_body_to_bot_create(self, api: APITestHelper) -> None:
        """POST empty JSON to bot create returns 422."""
        response = await api.client.post(
            api.bot_url(),
            params=api.params(),
            json={},
        )

        assert response.status_code == 422, (
            f"Expected 422 for empty body on bot create, "
            f"got {response.status_code}: {response.text[:200]}"
        )
        data = response.json()
        assert isinstance(data, dict), "Response must be valid JSON"

    @pytest.mark.asyncio
    async def test_post_empty_body_to_api_key_create(self, api: APITestHelper) -> None:
        """POST empty JSON to API key create returns 422."""
        response = await api.client.post(
            api.api_key_url(action="app"),
            params=api.params(),
            json={},
        )

        assert response.status_code == 422, (
            f"Expected 422 for empty body on API key create, "
            f"got {response.status_code}: {response.text[:200]}"
        )
        data = response.json()
        assert isinstance(data, dict), "Response must be valid JSON"


class TestInvalidTypes:
    """Test that wrong data types in request bodies are rejected."""

    @pytest.mark.asyncio
    async def test_string_for_integer_field(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """POST with string where integer is expected returns 422."""
        response = await api.client.post(
            api.api_key_url(action="app"),
            params=api.params(),
            json={
                "app_id": f"e2e-type-err-{unique_id}",
                "key_name": f"type-err-key-{unique_id}",
                "rate_limit_rpm": "not-an-integer",
            },
        )

        assert response.status_code == 422, (
            f"Expected 422 for string-instead-of-integer, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_integer_for_string_field(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """POST with integer where string is expected returns 422."""
        response = await api.client.post(
            api.api_key_url(action="app"),
            params=api.params(),
            json={
                "app_id": 12345,
                "key_name": f"int-for-string-{unique_id}",
            },
        )

        assert response.status_code == 422, (
            f"Expected 422 for integer-instead-of-string, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_extra_unexpected_field(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """POST with unexpected field may return 422 or succeed gracefully."""
        response = await api.client.post(
            api.api_key_url(action="app"),
            params=api.params(),
            json={
                "app_id": f"e2e-extra-field-{unique_id}",
                "key_name": f"extra-field-key-{unique_id}",
                "nonexistent_field_xyz": "should-be-ignored-or-rejected",
            },
        )

        # The server may either reject unknown fields (422) or ignore them (200)
        assert response.status_code in (200, 422), (
            f"Expected 200 or 422 for extra field, "
            f"got {response.status_code}: {response.text[:200]}"
        )


class TestMalformedUUID:
    """Test handling of malformed UUIDs in URL path parameters."""

    @pytest.mark.asyncio
    async def test_get_with_malformed_uuid(self, api: APITestHelper) -> None:
        """GET bot with 'not-a-uuid' returns 404 or 422."""
        response = await api.client.get(
            api.bot_url("not-a-uuid"),
            params=api.params(),
        )

        assert response.status_code in (404, 422), (
            f"Expected 404 or 422 for malformed UUID, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_get_with_invalid_uuid_format(self, api: APITestHelper) -> None:
        """GET bot with invalid UUID format returns 404 or 422."""
        for bad_uuid in (
            "not-a-valid-uuid-format",
            "123",
            "ffffffff-ffff-ffff-ffff-ffffffffffff",
            "../../etc/passwd",
        ):
            response = await api.client.get(
                api.bot_url(bad_uuid),
                params=api.params(),
            )

            assert response.status_code in (404, 422), (
                f"Expected 404 or 422 for UUID {bad_uuid!r}, "
                f"got {response.status_code}: {response.text[:200]}"
            )


class TestMissingRequiredField:
    """Test behavior when required fields are omitted from requests."""

    @pytest.mark.asyncio
    async def test_missing_bot_name(self, api: APITestHelper) -> None:
        """POST bot create without name returns 422."""
        response = await api.client.post(
            api.bot_url(),
            params=api.params(),
            json={
                "template_uuid": "TEMPLATE-00000000000000000000000000000000",
            },
        )

        assert response.status_code == 422, (
            f"Expected 422 for missing bot name, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_missing_operator(self, api: APITestHelper, unique_id: str) -> None:
        """POST bot create without operator returns 422."""
        response = await api.client.post(
            api.bot_url(),
            params=api.params(),
            json={
                "name": f"missing-operator-{unique_id}",
                "template_uuid": "TEMPLATE-00000000000000000000000000000000",
            },
        )

        assert response.status_code == 422, (
            f"Expected 422 for missing operator, "
            f"got {response.status_code}: {response.text[:200]}"
        )
