"""E2E tests for device template management CRUD and status lifecycle.

Requires a running application; base URL loaded via ConfigLoader (module_config.web.port).
"""

from uuid import uuid4

import pytest


def _unique_template_id() -> int:
    """Generate a unique template_id for test isolation."""
    return abs(hash(uuid4().hex)) % 900000000 + 100000000


def _unique_uuid() -> str:
    """Generate a unique template UUID for test isolation."""
    return f"e2e-{uuid4().hex}"


class TestDeviceTemplateApi:
    """E2E tests for device template CRUD and status lifecycle."""

    async def test_create_template(self, http_client, test_tenant):
        """WHEN creating a template with valid data, THEN returns 201 with template data."""
        template_id = _unique_template_id()
        template_uuid = _unique_uuid()
        resp = await http_client.post(
            "/api/v1/device-templates",
            params={"tenant": test_tenant},
            json={
                "template_uuid": template_uuid,
                "template_id": template_id,
                "type": "ARCA",
                "name": "e2e-test-template",
                "config": {
                    "type": "ARCA",
                    "base_url": "http://test",
                    "api_key": "test",
                },
                "operator": "e2e-test",
            },
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["name"] == "e2e-test-template"
        assert data["template_uuid"] == template_uuid
        assert data["template_id"] == template_id
        assert data["status"] == "CREATED"
        assert data["tenant"] == test_tenant

    async def test_get_template_by_uuid(self, http_client, test_tenant):
        """WHEN getting an ONLINE template by UUID, THEN returns 200 with template data."""
        template_id = _unique_template_id()
        template_uuid = _unique_uuid()
        # Create
        create_resp = await http_client.post(
            "/api/v1/device-templates",
            params={"tenant": test_tenant},
            json={
                "template_uuid": template_uuid,
                "template_id": template_id,
                "type": "ARCA",
                "name": "e2e-get-test",
                "config": {
                    "type": "ARCA",
                    "base_url": "http://test",
                    "api_key": "test",
                },
                "operator": "e2e-test",
            },
        )
        assert create_resp.status_code == 201

        # Transition to ONLINE so GET (which only returns ONLINE) works
        transition_resp = await http_client.post(
            f"/api/v1/device-templates/{template_uuid}/status-transitions",
            params={"tenant": test_tenant},
            json={"current_status": "CREATED", "new_status": "AUDITED"},
        )
        assert transition_resp.status_code == 200
        transition_resp = await http_client.post(
            f"/api/v1/device-templates/{template_uuid}/status-transitions",
            params={"tenant": test_tenant},
            json={"current_status": "AUDITED", "new_status": "ONLINE"},
        )
        assert transition_resp.status_code == 200

        # Get by UUID
        resp = await http_client.get(
            f"/api/v1/device-templates/{template_uuid}",
            params={"tenant": test_tenant},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["template_uuid"] == template_uuid
        assert data["template_id"] == template_id
        assert data["name"] == "e2e-get-test"
        assert data["status"] == "ONLINE"

    async def test_get_nonexistent_template(self, http_client, test_tenant):
        """WHEN getting a nonexistent template UUID, THEN returns 404."""
        resp = await http_client.get(
            f"/api/v1/device-templates/{_unique_uuid()}",
            params={"tenant": test_tenant},
        )
        assert resp.status_code == 404

    async def test_list_templates(self, http_client, test_tenant):
        """WHEN listing templates, THEN returns paginated results."""
        resp = await http_client.get(
            "/api/v1/device-templates",
            params={"tenant": test_tenant, "page": 1, "page_size": 10},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "page_size" in data
        assert isinstance(data["items"], list)
        assert data["page"] == 1
        assert data["page_size"] == 10

    async def test_update_template(self, http_client, test_tenant):
        """WHEN updating a template, THEN returns updated template data."""
        template_id = _unique_template_id()
        template_uuid = _unique_uuid()
        create_resp = await http_client.post(
            "/api/v1/device-templates",
            params={"tenant": test_tenant},
            json={
                "template_uuid": template_uuid,
                "template_id": template_id,
                "type": "ARCA",
                "name": "e2e-update-before",
                "config": {
                    "type": "ARCA",
                    "base_url": "http://test",
                    "api_key": "test",
                },
                "operator": "e2e-test",
            },
        )
        assert create_resp.status_code == 201

        resp = await http_client.put(
            f"/api/v1/device-templates/{template_uuid}",
            params={"tenant": test_tenant, "status": "CREATED"},
            json={"name": "e2e-update-after", "operator": "e2e-test"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["name"] == "e2e-update-after"
        assert data["template_uuid"] == template_uuid

    async def test_template_status_lifecycle(self, http_client, test_tenant):
        """WHEN transitioning through status lifecycle, THEN each transition succeeds.

        Route: POST /api/v1/device-templates/{uuid}/status-transitions
        Lifecycle: CREATED -> AUDITED -> ONLINE -> OFFLINE -> ONLINE
        """
        template_id = _unique_template_id()
        template_uuid = _unique_uuid()
        create_resp = await http_client.post(
            "/api/v1/device-templates",
            params={"tenant": test_tenant},
            json={
                "template_uuid": template_uuid,
                "template_id": template_id,
                "type": "ARCA",
                "name": "e2e-lifecycle",
                "config": {
                    "type": "ARCA",
                    "base_url": "http://test",
                    "api_key": "test",
                },
                "operator": "e2e-test",
            },
        )
        assert create_resp.status_code == 201
        assert create_resp.json()["data"]["status"] == "CREATED"

        transitions = [
            ("CREATED", "AUDITED"),
            ("AUDITED", "ONLINE"),
            ("ONLINE", "OFFLINE"),
            ("OFFLINE", "ONLINE"),
        ]
        for current, new in transitions:
            resp = await http_client.post(
                f"/api/v1/device-templates/{template_uuid}/status-transitions",
                params={"tenant": test_tenant},
                json={"current_status": current, "new_status": new},
            )
            assert resp.status_code == 200
            assert resp.json()["data"]["status"] == new

    async def test_soft_delete_template(self, http_client, test_tenant):
        """WHEN soft-deleting a template via POST, THEN deletion succeeds.

        Uses POST with JSON body (operator in body).
        """
        template_id = _unique_template_id()
        template_uuid = _unique_uuid()
        create_resp = await http_client.post(
            "/api/v1/device-templates",
            params={"tenant": test_tenant},
            json={
                "template_uuid": template_uuid,
                "template_id": template_id,
                "type": "ARCA",
                "name": "e2e-delete-test",
                "config": {
                    "type": "ARCA",
                    "base_url": "http://test",
                    "api_key": "test",
                },
                "operator": "e2e-test",
            },
        )
        assert create_resp.status_code == 201

        delete_resp = await http_client.post(
            f"/api/v1/device-templates/{template_uuid}/delete",
            params={"tenant": test_tenant, "status": "CREATED"},
            json={"operator": "e2e-test"},
        )
        assert delete_resp.status_code == 200
        assert delete_resp.json()["data"]["success"] is True

    async def test_create_template_validation_error(self, http_client, test_tenant):
        """WHEN creating template with invalid data, THEN returns 400/422."""
        resp = await http_client.post(
            "/api/v1/device-templates",
            params={"tenant": test_tenant},
            json={"name": "test"},
        )
        assert resp.status_code in (400, 422)

    async def test_create_duplicate_template_id(self, http_client, test_tenant):
        """WHEN creating template with duplicate template_id, THEN returns error."""
        template_id = _unique_template_id()
        body = {
            "template_uuid": _unique_uuid(),
            "template_id": template_id,
            "type": "ARCA",
            "name": "e2e-dup-original",
            "config": {"type": "ARCA", "base_url": "http://test", "api_key": "test"},
            "operator": "e2e-test",
        }
        resp1 = await http_client.post(
            "/api/v1/device-templates",
            params={"tenant": test_tenant},
            json=body,
        )
        assert resp1.status_code == 201

        body["template_uuid"] = _unique_uuid()
        body["name"] = "e2e-dup-duplicate"
        resp2 = await http_client.post(
            "/api/v1/device-templates",
            params={"tenant": test_tenant},
            json=body,
        )
        assert resp2.status_code in (400, 409)
