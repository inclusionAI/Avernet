"""Contract tests for the Bot-scoped canonical SkillSet API."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.skill_sets.router import router
from agentclaw.community.api.skill_set_control_plane import (
    SkillSetControlPlaneServiceProtocol,
)
from tests.community.adapters.http.openapi_v1.conftest import user_scoped_client


class _ControlPlane:
    def __init__(self) -> None:
        self.created: dict | None = None

    def list_sets(self, **_kwargs):
        return [
            {
                "id": "1",
                "name": "Default",
                "description": None,
                "is_default": True,
                "is_active": True,
            }
        ]

    def create_set(self, **kwargs):
        self.created = kwargs
        return {
            "id": "2",
            "name": kwargs["name"],
            "description": kwargs.get("description"),
            "is_default": False,
            "is_active": False,
        }

    def get_set(self, **_kwargs):
        return {
            "id": "2",
            "name": "New",
            "description": None,
            "is_default": False,
            "is_active": False,
        }

    def update_set(self, **_kwargs):
        return self.get_set()

    def delete_set(self, **_kwargs):
        return None

    def list_skills(self, **_kwargs):
        return []

    async def add_skill(self, **_kwargs):
        return {"changed": True}

    async def remove_skill(self, **_kwargs):
        return {"changed": False}

    async def activate(self, **_kwargs):
        return {
            "id": "2",
            "name": "New",
            "description": None,
            "is_default": False,
            "is_active": True,
            "changed": True,
        }

    async def deactivate(self, **_kwargs):
        return {
            "id": "2",
            "name": "New",
            "description": None,
            "is_default": False,
            "is_active": False,
            "changed": True,
        }

    def resources(self, **_kwargs):
        return []


def _client(control: _ControlPlane) -> TestClient:
    class Bindings(Module):
        def configure(self, binder):
            binder.bind(SkillSetControlPlaneServiceProtocol, to=control)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "actor"}
    attach_injector(app, Injector([Bindings()]))
    return user_scoped_client(app, "actor")


def test_create_is_inactive_without_an_idempotency_key():
    control = _ControlPlane()
    client = _client(control)

    response = client.post(
        "/openapi/v1/bots/bot-1/skill-sets",
        json={"name": "New", "description": "Description"},
    )

    assert response.status_code == 201
    assert response.json()["data"]["is_active"] is False
    assert control.created == {
        "bot_id": "bot-1",
        "owner_id": "actor",
        "user_id": "actor",
        "name": "New",
        "description": "Description",
    }


def test_membership_and_activation_are_bot_scoped():
    client = _client(_ControlPlane())

    member = client.put("/openapi/v1/bots/bot-1/skill-sets/2/skills/9")
    active = client.post("/openapi/v1/bots/bot-1/skill-sets/2/activate")

    assert member.status_code == 200
    assert member.json()["data"] == {"changed": True}
    assert active.status_code == 200
    assert active.json()["data"]["is_active"] is True
