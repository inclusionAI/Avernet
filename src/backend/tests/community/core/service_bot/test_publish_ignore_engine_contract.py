"""Cross-component contract: Backend transport to real Engine DI and filesystem.

Only device discovery and HTTP transport are local test doubles. Engine
runtime identity, replay journal and atomic file mutation execute unchanged.
"""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector

from agentclaw.community.kernel.publish_ignore import PublishIgnoreCommand
from agentclaw.community.plugins.community.publish_ignore_runtime import HttpPublishIgnoreRuntime


@pytest.fixture
def contract(tmp_path, monkeypatch):
    repository = Path(__file__).resolve().parents[6]
    monkeypatch.syspath_prepend(str(repository / "src/engine/src"))
    from engine.community.api.bot.router import router
    from engine.community.di.publish_ignore_config import PublishIgnoreModule
    from engine.community.plugins import publish_ignore
    from engine.community.shared.credentials import CredentialsService

    monkeypatch.delenv("SERVICE_BOT_PUBLISH_IGNORE_SIGNING_KEY", raising=False)
    monkeypatch.delenv("SERVICE_BOT_PUBLISH_IGNORE_VERIFY_KEY", raising=False)
    ignore = tmp_path / "ignore"
    monkeypatch.setattr(publish_ignore, "IGNORE_FILE", ignore)
    credentials = tmp_path / "credentials"
    credentials.write_text("BOT_ID=bot\nENTITY_ID=entity\nVERSION=V3\nSTAGE=online\n")
    credentials_service = CredentialsService(credentials)
    monkeypatch.setattr(publish_ignore, "get_credentials_service", lambda: credentials_service)
    app = FastAPI()
    app.include_router(router)
    attach_injector(app, Injector([PublishIgnoreModule()]))
    client = TestClient(app)
    captured = []

    class DeviceTransport:
        async def invoke(self, conn, method, endpoint, *, body=None, params=None, timeout):
            assert conn["device_uuid"] == "replica-a"
            assert conn["binding_id"] == 44
            captured.append(params if method == "GET" else body)
            response = client.request(method, endpoint, json=body, params=params)
            response.raise_for_status()
            return response.json()

    class ArcaHttp:
        def get(self, url, *, params, headers, timeout):
            assert url == "https://device.example/api/bot/publish-ignore"
            captured.append(params)
            return client.get("/api/bot/publish-ignore", params=params)

        def post(self, url, *, json, headers, timeout):
            assert url == "https://device.example/api/bot/publish-ignore"
            captured.append(json)
            return client.post("/api/bot/publish-ignore", json=json)

    baas = Mock(list_devices_by_bot_uuid=Mock(return_value=[{"uuid": "replica-a"}]))
    resolver = Mock(resolve_for_binding_invoke=Mock(return_value=NS(conn_info={
        "url": "https://device.example", "headers": {}, "binding_id": 44, "device_uuid": "replica-a",
    })))
    runtime = HttpPublishIgnoreRuntime(baas, resolver, DeviceTransport(), ArcaHttp())
    binding = NS(id=44, device_provider="baas", device_id="runtime-binding")
    command = PublishIgnoreCommand("bot", "entity", "online", "add", "workspace/cache", "parent-request")
    return NS(runtime=runtime, binding=binding, command=command, client=client,
              captured=captured, ignore=ignore, credentials=credentials)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["baas", "arca"])
@pytest.mark.parametrize("stage,version", [("online", ""), ("verify", "V4"), ("draft", "")])
async def test_add_remove_without_keys_across_real_engine(contract, provider, stage, version):
    ctx = contract
    ctx.command = replace(ctx.command, stage=stage)
    ctx.credentials.write_text(f"BOT_ID=bot\nENTITY_ID=entity\nVERSION={version}\nSTAGE={stage}\n")
    ctx.binding.device_provider = provider
    targets = await ctx.runtime.targets(ctx.binding)
    result = await ctx.runtime.change(ctx.binding, targets[0], ctx.command, "authorized-manager")
    assert result["status"] == "changed"
    assert ctx.ignore.read_text() == "workspace/cache\n"
    assert result["entry_count"] == 1
    again = await ctx.runtime.change(ctx.binding, targets[0], ctx.command, "authorized-manager")
    assert again["status"] == "unchanged"
    removed = await ctx.runtime.change(ctx.binding, targets[0], replace(ctx.command, operation="remove"), "authorized-manager")
    assert removed["status"] == "changed" and ctx.ignore.read_bytes() == b""
    assert len({data["request_id"] for data in ctx.captured}) == 3
    assert all("authorization" not in data for data in ctx.captured)
    assert ctx.client.post("/api/bot/publish-ignore", json=ctx.captured[0]).status_code == 409
    assert ctx.ignore.read_bytes() == b""


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", [
    "BOT_ID=other\nENTITY_ID=entity\nSTAGE=draft\n",
    "BOT_ID=bot\nENTITY_ID=other\nSTAGE=draft\n",
    "BOT_ID=bot\nENTITY_ID=entity\nSTAGE=online\n",
    "BOT_ID=bot\nENTITY_ID=entity\n",
])
async def test_draft_runtime_identity_mismatch(contract, identity):
    ctx = contract
    ctx.command = replace(ctx.command, stage="draft")
    ctx.credentials.write_text(identity)
    result = await ctx.runtime.change(ctx.binding, "replica-a", ctx.command, "authorized-manager")
    assert result["status"] == "failed"
    assert not ctx.ignore.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["baas", "arca"])
async def test_wrong_runtime_identity_is_not_mutated(contract, provider):
    ctx = contract
    ctx.binding.device_provider = provider
    ctx.credentials.write_text("BOT_ID=other\nENTITY_ID=entity\nSTAGE=online\n")
    target = (await ctx.runtime.targets(ctx.binding))[0]
    result = await ctx.runtime.change(ctx.binding, target, ctx.command, "authorized-manager")
    assert result["status"] == "failed"
    assert not ctx.ignore.exists()
    assert ctx.client.post("/api/bot/publish-ignore", json=ctx.captured[0]).status_code == 409


@pytest.mark.asyncio
async def test_invalid_path_cannot_mutate(contract):
    ctx = contract
    await ctx.runtime.change(ctx.binding, "replica-a", ctx.command, "authorized-manager")
    data = dict(ctx.captured[0])
    data["path"] = "../protected"
    assert ctx.client.post("/api/bot/publish-ignore", json=data).status_code == 422
    assert ctx.ignore.read_text() == "workspace/cache\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["baas", "arca"])
@pytest.mark.parametrize("stage", ["draft", "verify", "online"])
@pytest.mark.parametrize("contents", [None, b"# comment\r\n./workspace/bin/\r\nworkspace/bin\n"])
async def test_query_across_real_engine_without_writes(contract, provider, stage, contents):
    from hashlib import sha256
    from agentclaw.community.kernel.publish_ignore import PublishIgnoreQuery

    ctx = contract
    ctx.credentials.write_text(f"BOT_ID=bot\nENTITY_ID=entity\nSTAGE={stage}\n")
    ctx.binding.device_provider = provider
    if contents is not None:
        ctx.ignore.write_bytes(contents)
    before = {path.name: path.read_bytes() for path in ctx.ignore.parent.iterdir()}
    result = await ctx.runtime.query(
        ctx.binding, "replica-a", PublishIgnoreQuery("bot", "entity", stage, "query"), "manager",
    )
    assert result["status"] == "success"
    assert result["paths"] == ([] if contents is None else ["workspace/bin", "workspace/bin"])
    assert result["entry_count"] == len(result["paths"])
    assert result["revision"] == sha256(contents or b"").hexdigest()
    assert before == {path.name: path.read_bytes() for path in ctx.ignore.parent.iterdir()}
