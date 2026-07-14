"""E2E tests for device template status transitions, resolve, and validation errors.

Tests cover endpoints NOT already tested in test_device_template.py
and test_device_template_api.py:

- POST /api/v1/device-templates/{uuid}/status-transitions — error cases
- GET  /api/v1/device-templates/resolve — resolve by UUID
- GET  /api/v1/device-templates/online — online templates listing
- GET  /api/v1/device-templates/by-template-id/{template_id} — get by ID
- POST /api/v1/device-templates — create validation errors
- POST /api/v1/device-templates/{uuid}/delete — delete on non-existent
"""

import pytest

from ..conftest import DEFAULT_TEMPLATE_UUID, APITestHelper

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]

# ── Helpers ──────────────────────────────────────────────────────────────────


def _status_transitions_url(template_uuid: str) -> str:
    """Build status-transitions URL for a given template UUID."""
    return f"/api/v1/device-templates/{template_uuid}/status-transitions"


# ── Status Transitions – Error Cases ────────────────────────────────────────


class TestStatusTransitionsErrors:
    """Error cases for POST /api/v1/device-templates/{uuid}/status-transitions."""

    @pytest.mark.asyncio
    async def test_status_transition_nonexistent_template(
        self, api: APITestHelper
    ) -> None:
        """POST status-transitions on a non-existent template returns 404."""
        response = await api.client.post(
            _status_transitions_url("NONEXISTENT-0000000000000000"),
            params=api.params(),
            json={"current_status": "CREATED", "new_status": "ONLINE"},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_status_transition_missing_body(self, api: APITestHelper) -> None:
        """POST status-transitions with empty body returns 422."""
        response = await api.client.post(
            _status_transitions_url(DEFAULT_TEMPLATE_UUID),
            params=api.params(),
            json={},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_status_transition_invalid_target_status(
        self, api: APITestHelper
    ) -> None:
        """POST status-transitions with an unknown target_status returns 422."""
        response = await api.client.post(
            _status_transitions_url(DEFAULT_TEMPLATE_UUID),
            params=api.params(),
            json={"current_status": "CREATED", "new_status": "INVALID_STATUS"},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_status_transition_unauthorized_api_key(
        self, api: APITestHelper
    ) -> None:
        """POST status-transitions without a valid session returns 401/403.

        When no Bearer token is provided, the endpoint should reject the
        request (API-key-auth gate). In bare mode (no auth backend) this
        returns 403; with auth it returns 401.
        """
        response = await api.client.post(
            _status_transitions_url(DEFAULT_TEMPLATE_UUID),
            json={"current_status": "DRAFT", "new_status": "ONLINE"},
        )
        # No tenant param + no session → expect auth failure or validation error
        assert response.status_code in (401, 403, 422)


# ── Resolve Endpoint ────────────────────────────────────────────────────────


class TestResolveTemplate:
    """Tests for GET /api/v1/device-templates/resolve."""

    @pytest.mark.asyncio
    async def test_resolve_existing_template(self, api: APITestHelper) -> None:
        """GET resolve with a valid template_uuid returns 200 with template data."""
        response = await api.client.get(
            "/api/v1/device-templates/resolve",
            params=api.params(template_uuid=DEFAULT_TEMPLATE_UUID),
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["template_uuid"] == DEFAULT_TEMPLATE_UUID

    @pytest.mark.asyncio
    async def test_resolve_nonexistent_template(self, api: APITestHelper) -> None:
        """GET resolve with a non-existent template_uuid returns 404."""
        response = await api.client.get(
            "/api/v1/device-templates/resolve",
            params=api.params(template_uuid="NONEXISTENT-0000000000000000"),
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_resolve_missing_template_uuid(self, api: APITestHelper) -> None:
        """GET resolve without template_uuid param returns 404 or 422.

        Some FastAPI setups return 404 (route not matched) when a required
        query parameter is missing; others return 422 (validation error).
        """
        response = await api.client.get(
            "/api/v1/device-templates/resolve",
            params=api.params(),
        )
        assert response.status_code in (404, 422)


# ── Online Templates Listing ────────────────────────────────────────────────


class TestOnlineTemplates:
    """Tests for GET /api/v1/device-templates/online."""

    @pytest.mark.asyncio
    async def test_online_templates_pagination(self, api: APITestHelper) -> None:
        """GET online templates returns 200 with paginated results."""
        response = await api.client.get(
            "/api/v1/device-templates/online",
            params=api.params(page=1, page_size=10),
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "page_size" in data
        assert isinstance(data["items"], list)
        assert data["page"] == 1
        assert data["page_size"] == 10


# ── Get by Template ID ──────────────────────────────────────────────────────


class TestGetByTemplateId:
    """Tests for GET /api/v1/device-templates/by-template-id/{template_id}."""

    @pytest.mark.asyncio
    async def test_get_by_template_id_nonexistent(self, api: APITestHelper) -> None:
        """GET by-template-id with a non-existent ID returns 404 or 422."""
        response = await api.client.get(
            "/api/v1/device-templates/by-template-id/NONEXISTENT",
            params=api.params(),
        )
        assert response.status_code in (404, 422)


# ── Template Create Validation Errors ────────────────────────────────────────


class TestTemplateCreateValidationErrors:
    """Validation error cases for POST /api/v1/device-templates."""

    @pytest.mark.asyncio
    async def test_create_template_empty_body(self, api: APITestHelper) -> None:
        """POST device-templates with an empty body returns 422."""
        response = await api.client.post(
            "/api/v1/device-templates",
            params=api.params(),
            json={},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_create_template_missing_template_id(
        self, api: APITestHelper
    ) -> None:
        """POST device-templates without template_id returns 422."""
        response = await api.client.post(
            "/api/v1/device-templates",
            params=api.params(),
            json={
                "template_uuid": "E2E-UUID-MISSING-ID",
                "type": "ARCA",
                "name": "missing-id-test",
                "config": {
                    "type": "ARCA",
                    "base_url": "http://test",
                    "api_key": "test",
                },
                "operator": "e2e-test",
            },
        )
        assert response.status_code == 422


# ── Template Delete – Error Cases ────────────────────────────────────────────


class TestTemplateDeleteErrors:
    """Error cases for POST /api/v1/device-templates/{uuid}/delete."""

    @pytest.mark.asyncio
    async def test_delete_nonexistent_template(self, api: APITestHelper) -> None:
        """POST delete on a non-existent template returns 404."""
        response = await api.client.post(
            "/api/v1/device-templates/NONEXISTENT-0000000000000000/delete",
            params=api.params(status="CREATED"),
            json={"operator": "e2e-test"},
        )
        assert response.status_code == 404
