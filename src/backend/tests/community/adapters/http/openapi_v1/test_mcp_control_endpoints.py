"""Canonical Bot MCP Direct endpoint coverage."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.mcp.router import bot_mcp_router
from agentclaw.community.api.skill_set_management_service import (
    SkillSetManagementServiceProtocol,
)
from tests.community.adapters.http.openapi_v1.conftest import (
    bind_bot_access_seam,
    user_scoped_client,
)


class _ControlPlane:
    def __init__(self) -> None:
        self.active: set[str] = set()

    def list_installed_mcps(self, **_kwargs):
        return self.active

    async def activate_mcp_direct(self, *, server_code: str, **_kwargs):
        self.active.add(server_code)
        return {"changed": True}

    async def deactivate_mcp_direct(self, *, server_code: str, **_kwargs):
        self.active.discard(server_code)
        return {"changed": True}


def _client(control: _ControlPlane):
    class Bindings(Module):
        def configure(self, binder):
            binder.bind(SkillSetManagementServiceProtocol, to=control)
            # The MCP rows declare ``Check(MEMBER)`` now, so the seam is on
            # every route here and fails closed against an unwired app.
            bind_bot_access_seam(binder)

    app = FastAPI()
    app.include_router(bot_mcp_router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "actor"}
    attach_injector(app, Injector([Bindings()]))
    return user_scoped_client(app, "actor")


def test_bot_mcp_direct_activation_and_deactivation_are_bot_scoped():
    client = _client(_ControlPlane())

    activated = client.post("/openapi/v1/bots/bot-1/mcps/mcp.weather/activate")
    assert activated.status_code == 200
    assert activated.json()["data"] == {"server_code": "mcp.weather", "active": True}

    listed = client.get("/openapi/v1/bots/bot-1/mcps")
    assert listed.status_code == 200
    assert listed.json()["data"] == [{"server_code": "mcp.weather", "active": True}]

    deactivated = client.post("/openapi/v1/bots/bot-1/mcps/mcp.weather/deactivate")
    assert deactivated.status_code == 200
    assert deactivated.json()["data"] == {"server_code": "mcp.weather", "active": False}
