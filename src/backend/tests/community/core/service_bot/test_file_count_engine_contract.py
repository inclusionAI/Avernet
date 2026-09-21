"""Backend transport contract against the real Engine file port and filesystem."""

from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def file_count_contract(tmp_path, monkeypatch):
    repository = Path(__file__).resolve().parents[6]
    monkeypatch.syspath_prepend(str(repository / "src/engine/src"))
    from engine.community.api.file.router import router
    from engine.community.core.adapters.openclaw.file import OpenClawFileAdapter
    from engine.community.core.engine.base import BaseEngine
    from engine.community.core.engine.capability import Capability, EngineCapabilities
    from engine.community.core.engine.registry import EngineRegistry
    from engine.community.manager import EngineManager
    from engine.community.plugins.openclaw.plugin_impl import OpenClawPluginImpl
    from engine.community.plugins.skills_pool.center_content import MountedCenterContentAdapter

    from agentclaw.community.kernel.file_count import FileCountBinding
    from agentclaw.community.plugin_api.device_adapter_transport import DeviceAdapterHTTPStatusError
    from agentclaw.community.plugins.community.file_count_runtime import HttpFileCountRuntime

    root = tmp_path.resolve() / "openclaw"
    directory = root / "workspace" / "test_ignore"
    directory.mkdir(parents=True)
    (directory / "nested").mkdir()
    (directory / "one.txt").write_text("one")
    (directory / ".hidden").write_text("hidden")
    (directory / "nested" / "archive.zip").write_bytes(b"not unpacked")
    (directory / "link").symlink_to(directory / "one.txt")
    (directory / "loop").symlink_to(directory, target_is_directory=True)
    (root / ".service_bot_publish_ignore").write_text("workspace/test_ignore\n")
    monkeypatch.setenv("OPENCLAW_WORKSPACE_DIR", str(root / "workspace"))

    class CountEngine(BaseEngine):
        name = "file-count-contract"
        version = "1.0.0"

        @property
        def capabilities(self):
            return EngineCapabilities(supported={Capability.FILE_LIST})

    registry = EngineRegistry()
    registry.register(CountEngine)
    EngineManager.reset_instance()
    manager = EngineManager(CountEngine.name, registry=registry)
    manager._active_engine = CountEngine()
    manager._active_engine._file = OpenClawFileAdapter(
        OpenClawPluginImpl(center_content_adapter=MountedCenterContentAdapter()),
    )
    EngineManager._instance = manager
    app = FastAPI()
    app.include_router(router)
    captured = []

    with TestClient(app) as client:
        class DeviceTransport:
            async def invoke(self, conn, method, endpoint, *, params, timeout):
                assert conn["device_uuid"] == "replica-a"
                assert method == "GET" and endpoint == "/api/file/count"
                captured.append(params)
                response = client.get(endpoint, params=params)
                if response.is_error:
                    raise DeviceAdapterHTTPStatusError(response.status_code, response.text)
                return response.json()

        class ArcaHttp:
            def get(self, url, *, params, headers, timeout):
                assert url == "https://device.example/api/file/count"
                captured.append(params)
                return client.get("/api/file/count", params=params)

        resolver = Mock(resolve_for_binding_invoke=Mock(return_value=NS(conn_info={
            "url": "https://device.example", "headers": {}, "device_uuid": "replica-a",
        })))
        runtime = HttpFileCountRuntime(
            Mock(list_devices_by_bot_uuid=Mock(return_value=[{"uuid": "replica-a"}])),
            resolver, DeviceTransport(), ArcaHttp(),
        )
        yield NS(runtime=runtime, root=root, directory=directory, captured=captured,
                 binding=lambda provider: FileCountBinding(44, provider, "runtime-device"))
    EngineManager.reset_instance()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["baas", "arca"])
@pytest.mark.parametrize("stage", ["draft", "verify", "online"])
@pytest.mark.parametrize("absolute", [False, True])
async def test_real_filesystem_count_across_provider_and_stage(file_count_contract, provider, stage, absolute):
    from agentclaw.community.kernel.file_count import FileCountQuery

    ctx = file_count_contract
    path = str(ctx.directory) if absolute else "workspace/test_ignore"
    result = await ctx.runtime.query(
        ctx.binding(provider), "replica-a",
        FileCountQuery("bot", "entity", stage, path, "contract-request"), "manager",
    )
    assert result["status"] == "success"
    assert result["file_count"] == 3
    assert result["path"] == path
    assert result["elapsed_ms"] >= 0
    assert ctx.captured[0]["path"] == path
    assert ctx.captured[0]["request_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["baas", "arca"])
@pytest.mark.parametrize("path,code", [
    ("workspace/missing", "path_not_found"),
    ("workspace/test_ignore/one.txt", "not_directory"),
    ("workspace/test_ignore/loop", "path_forbidden"),
    ("../outside", "path_forbidden"),
])
async def test_engine_failure_is_not_zero(file_count_contract, provider, path, code):
    from agentclaw.community.kernel.file_count import FileCountQuery

    ctx = file_count_contract
    result = await ctx.runtime.query(
        ctx.binding(provider), "replica-a",
        FileCountQuery("bot", "entity", "draft", path, "contract-error"), "manager",
    )
    assert result["status"] == "failed"
    assert result["file_count"] is None
    assert result["error_code"] == code
