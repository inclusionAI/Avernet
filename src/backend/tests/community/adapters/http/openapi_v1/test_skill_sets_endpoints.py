"""Contract tests for the Bot-scoped canonical SkillSet API."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.skill_sets.router import router
from agentclaw.community.api.skill_set_management_service import (
    SkillSetManagementServiceProtocol,
)
from agentclaw.community.core.skill_center.skill_set_batch import (
    SkillSetAddOutcome,
)
from tests.community.adapters.http.openapi_v1.conftest import (
    bind_bot_access_seam,
    mount_public_error_handlers,
    user_scoped_client,
)


class _ControlPlane:
    def __init__(self) -> None:
        self.created: dict | None = None
        self.added: dict | None = None

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

    async def add_skills(self, **kwargs):
        self.added = kwargs
        return [SkillSetAddOutcome(skill_id="9", changed=True)]

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

    def list_resources(self, **_kwargs):
        return []


def _client(control: _ControlPlane) -> TestClient:
    class Bindings(Module):
        def configure(self, binder):
            binder.bind(SkillSetManagementServiceProtocol, to=control)
            # These operations now declare ``Check(MEMBER)``, so every route
            # here carries the seam and it fails closed against an unwired app.
            # ``actor`` owns ``bot-1`` in these tests, so the level resolves to
            # OWNER and the questions below stay the handler's own.
            bind_bot_access_seam(binder)

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
    control = _ControlPlane()
    client = _client(control)

    member = client.put("/openapi/v1/bots/bot-1/skill-sets/2/skills/9")
    active = client.post("/openapi/v1/bots/bot-1/skill-sets/2/activate")

    assert member.status_code == 200
    assert member.json()["data"] == {"changed": True}
    assert control.added == {
        "bot_id": "bot-1",
        "owner_id": "actor",
        "user_id": "actor",
        "set_id": "2",
        "skill_ids": ["9"],
    }
    assert active.status_code == 200
    assert active.json()["data"]["is_active"] is True


def test_a_caller_with_no_relation_is_refused_before_the_control_plane_runs():
    """What ``can_manage_bot`` used to answer, asserted where it now happens.

    The control plane checked this itself and raised ``SkillSetAccessDenied``
    — 403. It no longer checks: the row is ``Check(MEMBER)`` and ``bot_access``
    refuses first, with the masked 404 that a genuinely absent bot returns.

    Driven end to end rather than argued from the table, because the two facts
    that matter cannot be read off a row: that the refusal really reaches the
    caller as a 404, and that the handler — and so the control plane — never
    runs at all. A denial that arrived after the mutation would still be a 404.
    """
    control = _ControlPlane()

    class Bindings(Module):
        def configure(self, binder):
            binder.bind(SkillSetManagementServiceProtocol, to=control)
            # Default ``SeamCollaborators`` holds ``PermissionLevel.NONE``, and
            # here the caller does not own the bot either, so nothing
            # short-circuits to OWNER.
            bind_bot_access_seam(binder)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "stranger"}
    mount_public_error_handlers(app)
    attach_injector(app, Injector([Bindings()]))
    client = user_scoped_client(app, "stranger")

    response = client.post(
        "/openapi/v1/bots/bot-1/skill-sets?owner_id=someone-else",
        json={"name": "New", "description": "Description"},
    )

    assert response.status_code == 404
    assert response.json()["message"] == "Not found"
    assert control.created is None, "the control plane ran despite the refusal"
