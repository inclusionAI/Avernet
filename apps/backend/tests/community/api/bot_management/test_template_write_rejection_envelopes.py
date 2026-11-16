"""Template-write rejection is a controlled API error, not a successful update.

These minimal-app tests exercise real routes and their error mapping. The
service boundary is injected; real SQLite persistence and service-level error
propagation are exercised separately by the template-write seam suites.
"""
from __future__ import annotations

import pytest

from agentclaw.community.plugin_api.bot_template_write_extension import (
    TemplateWriteInvalidInput,
)
from tests.community.api.bot_management._bot_router_test_support import (
    admin_client as admin_client,
    admin_update_client as admin_update_client,
    client as client,
    mock_bot_service as mock_bot_service,
    mock_passport as mock_passport,
)


def _assert_rejected(response):
    # Existing ApiResponse envelopes use HTTP 200; 400 is the application code.
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["error_code"] == 400
    assert body["message"] == "invalid template snapshot"
    assert body["data"] is None


@pytest.mark.parametrize("operation", ["update", "restart"])
def test_owner_route_maps_template_write_rejection(client, operation):
    tc, service, _ = client
    if operation == "update":
        service.update_bot.side_effect = TemplateWriteInvalidInput("invalid template snapshot")
        response = tc.put("/api/bots/default", json={"template_config": {"k": "v"}})
    else:
        service.restart_bot_async.side_effect = TemplateWriteInvalidInput("invalid template snapshot")
        response = tc.post(
            "/api/bots/default/restart",
            json={"extra_configs": {"template_config": {"k": "v"}}},
        )
    _assert_rejected(response)


def test_admin_update_maps_template_write_rejection(admin_update_client):
    tc, service = admin_update_client
    service.admin_update_bot.side_effect = TemplateWriteInvalidInput("invalid template snapshot")
    _assert_rejected(tc.put(
        "/api/bots/default/admin",
        json={"owner_id": "test_user", "template_config": {"k": "v"}},
    ))


def test_restart_for_others_maps_template_write_rejection(admin_client):
    tc, service, _, _ = admin_client
    service.restart_bot_async.side_effect = TemplateWriteInvalidInput("invalid template snapshot")
    _assert_rejected(tc.post(
        "/api/bots/restart-for-others",
        json={"target_user_id": "test_user", "target_bot_id": "default"},
    ))
