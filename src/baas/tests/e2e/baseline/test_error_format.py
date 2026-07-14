"""E2E tests for consistent error format across different HTTP error codes.

Tests that all API error responses follow a consistent JSON structure
regardless of the HTTP status code or endpoint that generated the error.
"""

import pytest

from ..conftest import APITestHelper

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]


class Test404Format:
    """Tests that 404 Not Found errors follow consistent format."""

    @pytest.mark.asyncio
    async def test_nonexistent_endpoint_404(self, api: APITestHelper) -> None:
        """GET /api/v1/nonexistent-endpoint returns 404 with structured error."""
        response = await api.client.get(
            "/api/v1/nonexistent-endpoint",
            params=api.params(),
        )

        assert response.status_code == 404, (
            f"Expected 404 for nonexistent endpoint, got {response.status_code}: "
            f"{response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_response_is_json(self, api: APITestHelper) -> None:
        """404 response body is valid JSON."""
        response = await api.client.get(
            "/api/v1/nonexistent-endpoint",
            params=api.params(),
        )

        assert response.status_code == 404
        data = response.json()
        assert isinstance(data, dict), "Response body must be a JSON object"

    @pytest.mark.asyncio
    async def test_error_structure_has_detail_or_code(self, api: APITestHelper) -> None:
        """404 error response contains detail or code field for programmatic handling."""
        response = await api.client.get(
            "/api/v1/nonexistent-endpoint",
            params=api.params(),
        )

        assert response.status_code == 404
        data = response.json()

        # Accept either top-level or nested error structure
        has_detail = "detail" in data and data["detail"] is not None
        has_code = "code" in data and data["code"] != 0
        assert has_detail or has_code, (
            f"Error response must contain 'detail' or non-zero 'code' field. "
            f"Got keys: {list(data.keys())}, content: {data}"
        )


class Test400Format:
    """Tests that 400 Bad Request errors follow consistent format."""

    @pytest.mark.asyncio
    async def test_malformed_json_on_valid_endpoint(self, api: APITestHelper) -> None:
        """POST to bot endpoint with non-JSON body returns 400."""
        response = await api.client.post(
            api.bot_url(),
            params=api.params(),
            content=b"not valid json at all",
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code in (400, 422), (
            f"Expected 400 or 422 for malformed JSON, got {response.status_code}: "
            f"{response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_response_is_json(self, api: APITestHelper) -> None:
        """400/422 response body is valid JSON."""
        response = await api.client.post(
            api.bot_url(),
            params=api.params(),
            content=b"not valid json at all",
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code in (400, 422)
        data = response.json()
        assert isinstance(data, dict), "Response body must be a JSON object"


class Test405Format:
    """Tests that 405 Method Not Allowed errors follow consistent format."""

    @pytest.mark.asyncio
    async def test_wrong_method_on_endpoint(self, api: APITestHelper) -> None:
        """POST to a GET-only health endpoint returns 405."""
        response = await api.client.post(
            "/api/v1/bot-health-checker/health",
            params=api.params(),
        )

        assert response.status_code == 405, (
            f"Expected 405 for wrong method, got {response.status_code}: "
            f"{response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_response_is_json(self, api: APITestHelper) -> None:
        """405 response body is valid JSON."""
        response = await api.client.post(
            "/api/v1/bot-health-checker/health",
            params=api.params(),
        )

        assert response.status_code == 405
        data = response.json()
        assert isinstance(data, dict), "Response body must be a JSON object"


class Test422Format:
    """Tests that 422 Unprocessable Entity (validation) errors follow consistent format."""

    @pytest.mark.asyncio
    async def test_validation_error_format(self, api: APITestHelper) -> None:
        """POST empty body to API key create returns 422."""
        response = await api.client.post(
            api.api_key_url(action="app"),
            params=api.params(),
            json={},
        )

        assert response.status_code == 422, (
            f"Expected 422 for empty body, got {response.status_code}: "
            f"{response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_validation_error_has_field_details(self, api: APITestHelper) -> None:
        """422 validation error response includes per-field error details."""
        response = await api.client.post(
            api.api_key_url(action="app"),
            params=api.params(),
            json={},
        )

        assert response.status_code == 422
        data = response.json()

        # The error should contain field-level detail — look for common patterns
        body_text = response.text.lower()
        has_field_hint = (
            "detail" in data
            or (isinstance(data.get("detail"), list) and len(data["detail"]) > 0)
            or "field" in body_text
            or "app_id" in body_text
            or "key_name" in body_text
            or "required" in body_text
            or "validation" in body_text
        )
        assert has_field_hint, (
            f"422 error response should indicate which fields failed validation. "
            f"Got: {data}"
        )
