from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Binder, Injector, InstanceProvider, Module, singleton

from engine.community.plugin_api.auth_gate.protocol import AuthGateService
from engine.community.api.zero_check import router


class FakeAuthGateService:
    def __init__(self) -> None:
        self.enabled = True

    async def get_switch(self) -> bool:
        return self.enabled

    async def set_switch(self, enabled: bool) -> None:
        self.enabled = bool(enabled)

    async def verify(self, token: str, content: str, session_id: str):  # pragma: no cover
        raise NotImplementedError


@pytest.fixture
def client():
    app = FastAPI()
    service = FakeAuthGateService()

    class _Module(Module):
        def configure(self, binder: Binder) -> None:
            binder.bind(AuthGateService, to=InstanceProvider(service), scope=singleton)

    attach_injector(app, Injector([_Module()]))
    app.include_router(router)
    with TestClient(app) as c:
        yield c


def test_get_zero_check_switch_defaults_to_enabled(client):
    response = client.get("/api/openclaw/zero-check")

    assert response.status_code == 200
    assert response.json() == {"enabled": True}


def test_post_zero_check_switch_updates_runtime_state(client):
    response = client.post("/api/openclaw/zero-check", json={"enabled": False})

    assert response.status_code == 200
    assert response.json() == {"enabled": False}
    assert client.get("/api/openclaw/zero-check").json() == {"enabled": False}

    response = client.post("/api/openclaw/zero-check", json={"enabled": True})
    assert response.status_code == 200
    assert response.json() == {"enabled": True}
