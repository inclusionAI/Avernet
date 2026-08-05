"""Operator authorization tests for user-management HTTP endpoints."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.access.router import user_router
from agentclaw.community.adapters.http.auth.dependencies import require_operator
from agentclaw.community.api.user_service import UserServiceProtocol


class _UserServiceModule(Module):
    def __init__(self, service):
        self._service = service

    def configure(self, binder):
        binder.bind(UserServiceProtocol, to=self._service)


@pytest.fixture
def non_operator_client():
    service = MagicMock()
    app = FastAPI()
    app.include_router(user_router)

    async def _deny_operator():
        raise HTTPException(status_code=403, detail="operator required")

    app.dependency_overrides[require_operator] = _deny_operator
    attach_injector(app, Injector([_UserServiceModule(service)]))
    return TestClient(app), service


@pytest.mark.parametrize(
    ("method", "path", "json_body"),
    [
        ("get", "/api/v1/user", None),
        ("get", "/api/v1/user/COMPETE/another-user", None),
        (
            "post",
            "/api/v1/user",
            {
                "user_id": "another-user",
                "user_type": "COMPETE",
                "status": "ACCESS",
            },
        ),
    ],
)
def test_user_management_rejects_non_operator(
    non_operator_client, method, path, json_body,
):
    client, service = non_operator_client

    response = client.request(method, path, json=json_body)

    assert response.status_code == 403
    service.list_users.assert_not_called()
    service.get_user.assert_not_called()
    service.upsert_user.assert_not_called()
