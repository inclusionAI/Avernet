"""Exercise HTTP -> core -> native port creation without list/history readback."""
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from engine.community.api.session import router as session_router
from engine.community.core.adapters.openclaw.session import OpenClawSessionAdapter
from engine.community.kernel.frames import ResponseFrame
from engine.community.local.openclaw import LocalOpenClawPluginImpl
from engine.community.plugins.openclaw.plugin_impl import OpenClawPluginImpl
from engine.community.plugins.skills_pool.center_content import MountedCenterContentAdapter


@pytest.mark.parametrize("model", [None, "alias", "provider/selected-model"])
async def test_native_creation_adds_authoritative_metadata_without_changing_legacy_fields(
    monkeypatch, model,
):
    canonical_id = "agent:custom:session:abc-123:user:clouduser"
    client = AsyncMock()
    client.send_request.return_value = ResponseFrame(id="1", ok=True, payload={
        "key": canonical_id,
        "resolved": {"modelProvider": "provider", "model": "selected-model"},
        "path": "/private/sessions.json",
        "entry": {"sessionId": "internal-transcript-id"},
    })
    pool = AsyncMock()
    pool.get.return_value = client
    plugin = OpenClawPluginImpl(
        center_content_adapter=MountedCenterContentAdapter(), pool=pool,
    )
    adapter = OpenClawSessionAdapter(plugin)
    monkeypatch.setattr(session_router, "_get_session_api", lambda engine: adapter)
    monkeypatch.setattr(session_router, "check_capability", lambda capability: None)
    app = FastAPI()
    app.include_router(session_router.router)
    body = {"uuid": "ABC-123", "user_id": "CloudUser", "title": "My Chat"}
    if model is not None:
        body["model"] = model

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        response = await http.post("/api/sessions", json=body)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["id"] == "session:ABC-123:user:CloudUser"
    assert data["title"] == "My Chat_ABC-123"
    assert data["model"] == model
    assert data["message_count"] == 0
    assert data["gmt_created"] == data["gmt_modified"]
    assert data["gmt_created"]
    assert data["ext_info"] == {"openclaw_creation": {
        "session_id": canonical_id, "model": "provider/selected-model",
    }}
    assert "/private" not in response.text
    assert "internal-transcript-id" not in response.text
    params = {"key": data["id"], "label": data["title"]}
    if model is not None:
        params["model"] = model
    client.send_request.assert_awaited_once_with("sessions.patch", params, timeout=60.0)
    pool.get.assert_awaited_once_with(None)


async def test_local_port_satisfies_the_same_creation_metadata_contract():
    from engine.community.core.session.models import SessionCreateRequest

    plugin = LocalOpenClawPluginImpl()
    adapter = OpenClawSessionAdapter(plugin)
    session = await adapter.create(SessionCreateRequest(user_id="u1", uuid="local"))

    assert session.id == "session:local:user:u1"
    assert session.model is None
    assert session.ext_info == {"openclaw_creation": {
        "session_id": session.id, "model": "local/local-model",
    }}
    stored = (await plugin.sessions_list())[0]
    assert session.ext_info["openclaw_creation"]["session_id"] == stored["key"]
    assert session.ext_info["openclaw_creation"]["model"] == stored["model"]
