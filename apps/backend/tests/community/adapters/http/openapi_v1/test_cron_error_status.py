"""Cron business failures retain their status at the public HTTP boundary."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from agentclaw.community.adapters.http.openapi_v1.responses import mapped_error_response
from agentclaw.community.api.cron_relay_service import CronRelayServiceProtocol
from agentclaw.community.core.cron.errors import CronApiTimeoutError, CronRelayError
from agentclaw.community.core.cron.services.cron_relay import CronRelayService
from tests.community.endpoints.test_openapi_skill_sets import (
    _BOT_ID,
    _HEADERS,
    _OWNER,
    _seed_inactive,
)


@pytest.mark.parametrize("code", [400, 403, 409, 502])
def test_cron_status_does_not_disclose_internal_exception_details(code):
    request = Request({"type": "http", "headers": []})
    response = mapped_error_response(CronRelayError("private-bot-id", error_code=code), request)
    assert response.status_code == code
    assert b"private-bot-id" not in response.body
    assert f'"code":{code * 1000}'.encode() in response.body


@pytest.mark.parametrize("code", [200, 999])
def test_unrecognized_cron_status_retains_upstream_error_fallback(code):
    request = Request({"type": "http", "headers": []})
    response = mapped_error_response(CronRelayError("private-detail", error_code=code), request)
    assert response.status_code == 502
    assert b"Cron relay service error" in response.body


def test_cron_timeout_retains_specific_504_mapping():
    request = Request({"type": "http", "headers": []})
    response = mapped_error_response(CronApiTimeoutError("/private/path", 1), request)
    assert response.status_code == 504
    assert b"Cron relay timed out" in response.body
    assert b"/private/path" not in response.body


@pytest.mark.parametrize("device_status,expected", [("PENDING", 409), ("ACTIVE", 200)])
def test_public_patch_exercises_real_cron_device_state_check(
    world, app_with_testing_modules, device_status, expected
):
    _seed_inactive(world)
    bots = MagicMock()
    bots.get_bot.return_value = {
        "bot_id": _BOT_ID,
        "bot_name": "probe",
        "owner_id": _OWNER,
        "bot_type": "personal",
        "binding_id": 1,
    }
    devices = MagicMock()
    devices.get_device.return_value = {"status": device_status}
    service = CronRelayService(
        bot_provider=bots,
        device_provider=devices,
        transport=MagicMock(),
        resolver=MagicMock(),
        template_repo=MagicMock(),
        publish_repo=MagicMock(),
        runtime_bindings=MagicMock(),
        collaborators=MagicMock(),
    )
    service._forward_runtime_target_request = AsyncMock(
        return_value={
            "success": True,
            "data": {"id": "t1", "name": "updated", "enabled": True},
        }
    )
    world.injector.binder.bind(CronRelayServiceProtocol, to=service, scope=None)
    client = TestClient(app_with_testing_modules, raise_server_exceptions=False)
    response = client.patch(
        f"/openapi/v1/bots/{_BOT_ID}/routines/t1",
        headers=_HEADERS,
        params={"user_id": _OWNER},
        json={"name": "updated"},
    )
    assert response.status_code == expected, response.text
    assert response.json()["code"] == expected * 1000
    devices.get_device.assert_called_once_with(binding_id=1)
    if device_status == "PENDING":
        service._forward_runtime_target_request.assert_not_awaited()
    else:
        service._forward_runtime_target_request.assert_awaited_once()
