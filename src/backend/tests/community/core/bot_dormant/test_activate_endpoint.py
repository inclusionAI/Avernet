"""Tests for the public dormant activation endpoint."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, InstanceProvider, singleton

from agentclaw.community.adapters.http.bot_dormant import router as dormant_router_module
from agentclaw.community.adapters.http.dependencies import RequestContext, get_request_context
from agentclaw.community.core.bot_dormant.activate_service import (
    ActivateBotService,
    BotNotFoundError,
)


def _build_app(activate_svc: ActivateBotService) -> TestClient:
    app = FastAPI()
    app.include_router(dormant_router_module.router)
    app.dependency_overrides[get_request_context] = (
        lambda: RequestContext(user_id="462750", nick_name="tester")
    )

    injector = Injector()
    injector.binder.bind(
        ActivateBotService, InstanceProvider(activate_svc), scope=singleton
    )
    attach_injector(app, injector)

    return TestClient(app)


@pytest.mark.unit
def test_activate_returns_business_404_when_bot_not_found():
    """Missing bot should be a clear not-found business error, not a generic 500."""
    activate_svc = MagicMock(spec=ActivateBotService)
    activate_svc.activate.side_effect = BotNotFoundError(
        "Bot not found: 20260803_qt3u3s7n"
    )

    client = _build_app(activate_svc)
    response = client.post("/api/bots/20260803_qt3u3s7n/activate", json={})

    assert response.status_code == 200
    assert response.json() == {
        "success": False,
        "data": None,
        "message": "Bot 不存在",
        "error_code": 404,
    }
