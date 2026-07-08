"""Tests for aicoding bindings router."""
import json
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentclaw.community.adapters.http.dependencies import RequestContext, get_request_context
from agentclaw.community.adapters.http.aicoding.router import router as aicoding_router
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory


URL_TEMPLATE = "/api/aicoding/staff/emp001/bot/abc/aicoding.bindings.json"
DEFAULT = '{"code_repos": [], "dima_spaces": []}'


@pytest.fixture
def mock_ctx():
    return RequestContext(user_id="user_001", bot_id="default")


@pytest.fixture
def fake_path_factory(tmp_path: Path):
    pf = MagicMock(spec=WorkspacePathFactory)
    # Mirror real path_factory.get_bot_engine_dir which returns {bot_dir}/{engine_type}
    pf.get_bot_engine_dir.return_value = tmp_path / "bot_dir" / "aicoding"
    return pf


@pytest.fixture
def arca_device_fs():
    """The DeviceFileSystem the dispatcher hands back for an arca bot."""
    fs = MagicMock()
    fs.read_file = AsyncMock()
    fs.write_file = AsyncMock()
    return fs


@pytest.fixture
def client(mock_ctx, fake_path_factory, arca_device_fs):
    from fastapi_injector import attach_injector
    from injector import Injector, Module
    from agentclaw.community.core.bot_management.repository.protocol import BotRepository
    from agentclaw.community.core.devices.services.device_context_resolver import (
        DeviceContextResolver,
    )
    from agentclaw.community.di.modules.skill_center_module import DeviceFilesystemDispatcher

    resolver = MagicMock()
    dispatcher = MagicMock()
    dispatcher.dispatch.return_value = arca_device_fs

    class _M(Module):
        def configure(self, binder):
            binder.bind(BotRepository, to=MagicMock())
            binder.bind(WorkspacePathFactory, to=fake_path_factory)
            binder.bind(DeviceContextResolver, to=resolver)
            binder.bind(DeviceFilesystemDispatcher, to=dispatcher)

    app = FastAPI()
    app.include_router(aicoding_router)
    app.dependency_overrides[get_request_context] = lambda: mock_ctx
    attach_injector(app, Injector([_M()]))
    return TestClient(app, raise_server_exceptions=False)


# ==================== 8.1 端到端行为测试(粗粒度 mock,5 条) ====================


class TestEndToEnd:
    def test_put_then_get_roundtrip(self, client):
        """Write valid JSON, then read it back unchanged."""
        payload = {"code_repos": [{"url": "git@example.com:foo/bar.git"}], "dima_spaces": []}
        body_str = json.dumps(payload)
        store: dict[str, str] = {}

        async def fake_write(path, content, *a, **kw):
            store[str(path)] = content

        async def fake_read(path, *a, **kw):
            return store.get(str(path), "")

        with patch("agentclaw.community.adapters.http.aicoding.router._write", new=fake_write), \
             patch("agentclaw.community.adapters.http.aicoding.router._read", new=fake_read):
            put = client.put(URL_TEMPLATE, json={"content": body_str})
            assert put.status_code == 200, put.text

            get = client.get(URL_TEMPLATE)
            assert get.status_code == 200
            assert get.json()["content"] == body_str
            assert get.json()["file_type"] == "aicoding.bindings.json"

    def test_put_invalid_json_returns_400(self, client):
        """Malformed JSON body must be rejected before write."""
        with patch("agentclaw.community.adapters.http.aicoding.router._write", new=AsyncMock()) as w:
            resp = client.put(URL_TEMPLATE, json={"content": "{not json"})
        assert resp.status_code == 400
        assert "Invalid JSON" in resp.json()["detail"]
        w.assert_not_called()

    def test_get_missing_file_returns_default(self, client):
        """When the file doesn't exist, GET returns the default empty JSON."""
        async def fake_read(*a, **kw):
            return ""

        with patch("agentclaw.community.adapters.http.aicoding.router._read", new=fake_read):
            resp = client.get(URL_TEMPLATE)
        assert resp.status_code == 200
        assert resp.json()["content"] == DEFAULT
        assert json.loads(resp.json()["content"]) == {"code_repos": [], "dima_spaces": []}

    def test_get_existing_file_skips_default(self, client):
        """When the file exists with non-empty content, default fallback must NOT trigger."""
        actual = '{"code_repos":[{"id":1}],"dima_spaces":[{"id":"s1"}]}'

        async def fake_read(*a, **kw):
            return actual

        with patch("agentclaw.community.adapters.http.aicoding.router._read", new=fake_read):
            resp = client.get(URL_TEMPLATE)
        assert resp.status_code == 200
        assert resp.json()["content"] == actual
        assert resp.json()["content"] != DEFAULT

    def test_put_invalid_entity_type_returns_400(self, client):
        """Invalid entity_type in URL → 400, no write."""
        with patch("agentclaw.community.adapters.http.aicoding.router._write", new=AsyncMock()) as w:
            resp = client.put(
                "/api/aicoding/foo/emp001/bot/abc/aicoding.bindings.json",
                json={"content": '{}'},
            )
        assert resp.status_code == 400
        assert "Invalid entity_type" in resp.json()["detail"]
        w.assert_not_called()


# ==================== 8.2 Arca/本地分支选择测试(细粒度 mock,2 条 — S1) ====================


class TestArcaBranchSelection:
    def test_read_takes_arca_branch_when_device_is_arca(self, client, arca_device_fs):
        """When device_provider is 'arca', _read goes through the device-fs seam."""
        actual = '{"code_repos":[{"id":99}],"dima_spaces":[]}'
        arca_device_fs.read_file.return_value = actual.encode("utf-8")

        with patch("agentclaw.community.core.devices.services.device_info.get_device_info") as mock_info:
            mock_info.return_value = ("arca", "sandbox_xyz")
            resp = client.get(URL_TEMPLATE)

        assert resp.status_code == 200
        assert resp.json()["content"] == actual
        arca_device_fs.read_file.assert_awaited_once()
        # device_fs.read_file(file_path_str)
        args, _ = arca_device_fs.read_file.call_args
        assert args[0].endswith("aicoding/workspace/aicoding.bindings.json")

    def test_write_takes_arca_branch_when_device_is_arca(self, client, arca_device_fs):
        """When device_provider is 'arca', _write goes through the device-fs seam."""
        body_str = '{"code_repos":[],"dima_spaces":[{"id":"s9"}]}'

        with patch("agentclaw.community.core.devices.services.device_info.get_device_info") as mock_info:
            mock_info.return_value = ("arca", "sandbox_xyz")
            resp = client.put(URL_TEMPLATE, json={"content": body_str})

        assert resp.status_code == 200
        arca_device_fs.write_file.assert_awaited_once()
        # device_fs.write_file(file_path_str, content_bytes)
        args, _ = arca_device_fs.write_file.call_args
        assert args[0].endswith("aicoding/workspace/aicoding.bindings.json")
        assert args[1] == body_str.encode("utf-8")
