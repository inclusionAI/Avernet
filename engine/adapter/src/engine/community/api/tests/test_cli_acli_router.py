"""``/api/cli/acli/*`` — the wire contract ``ArcaAcliPort`` reads.

The runner is replaced by a fake: what is pinned here is the HTTP shape — a
refusal is ``200`` + ``success: false`` + ``error``, bad input is ``400``, and
``501`` means the engine does not offer the capability.
"""
from __future__ import annotations

import importlib
from unittest.mock import MagicMock

import pytest
from engine.community.api.cli import router
from engine.community.core.acli.runner import (
    AcliError,
    AcliMarketPage,
    AcliTool,
    AcliUninstallResult,
)
from engine.community.core.engine.base import BaseEngine
from engine.community.core.engine.capability import Capability, EngineCapabilities
from engine.community.core.engine.registry import EngineRegistry
from engine.community.manager import EngineManager
from fastapi import FastAPI
from fastapi.testclient import TestClient

ACLI_CAPS = {
    Capability.CLI_ACLI_INSTALL,
    Capability.CLI_ACLI_UNINSTALL,
    Capability.CLI_ACLI_MARKET,
}
#: The module, not the ``router`` object the package re-exports under that name.
router_module = importlib.import_module("engine.community.api.cli.router")


class _FakeRunner:
    def __init__(self) -> None:
        self.error: AcliError | None = None
        self.calls: list[tuple] = []

    async def install(self, name: str) -> AcliTool:
        self.calls.append(("install", name))
        if self.error:
            raise self.error
        return AcliTool(name=name, command="di", description="desc", version="1.0.9")

    async def uninstall(self, name: str) -> AcliUninstallResult:
        self.calls.append(("uninstall", name))
        if self.error:
            raise self.error
        return AcliUninstallResult(name=name, uninstalled=True)

    async def market(self, keyword: str, page_no: int, page_size: int) -> AcliMarketPage:
        self.calls.append(("market", keyword, page_no, page_size))
        if self.error:
            raise self.error
        return AcliMarketPage(
            items=(AcliTool("dataphin", "dataphin", "d", "1.4.14"),),
            total=3,
            has_more=False,
        )


def _engine_class(caps: set[Capability]):
    class _AcliTestEngine(BaseEngine):
        name = "acli-test"
        version = "1.0.0"
        _CAPABILITIES = EngineCapabilities(supported=set(caps))

        @property
        def capabilities(self) -> EngineCapabilities:
            return self._CAPABILITIES

        def __init__(self) -> None:
            super().__init__(None)
            self._session = MagicMock()
            self._chat = MagicMock()

    return _AcliTestEngine


def _client(caps: set[Capability]) -> TestClient:
    engine_cls = _engine_class(caps)
    EngineManager.reset_instance()
    registry = EngineRegistry()
    registry.register(engine_cls)
    manager = EngineManager(engine_cls.name, registry=registry)
    manager._active_engine = engine_cls()
    EngineManager._instance = manager
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def runner(monkeypatch) -> _FakeRunner:
    fake = _FakeRunner()
    monkeypatch.setattr(router_module, "_ACLI_RUNNER", fake)
    return fake


@pytest.fixture
def client():
    client = _client(ACLI_CAPS)
    try:
        yield client
    finally:
        EngineManager.reset_instance()


def test_install_returns_what_acli_installed(client, runner):
    body = client.post(
        "/api/cli/acli/install", json={"name": "deepinsight-cli"}
    ).json()

    assert body["success"] is True
    assert body["data"] == {
        "name": "deepinsight-cli", "command": "di", "description": "desc", "version": "1.0.9",
    }
    assert runner.calls == [("install", "deepinsight-cli")]


def test_install_refusal_is_200_with_error_code(client, runner):
    runner.error = AcliError("install_failed", "no such cli")

    response = client.post(
        "/api/cli/acli/install", json={"name": "nope"}
    )

    assert response.status_code == 200
    assert response.json() | {"warning": None} == {
        "success": False, "data": None, "message": "no such cli", "warning": None,
        "total": None, "error": "install_failed",
    }


def test_invalid_input_is_400(client, runner):
    runner.error = AcliError("invalid_input", "bad name")

    response = client.post(
        "/api/cli/acli/install", json={"name": "-x"}
    )

    assert response.status_code == 400


def test_uninstall_reports_whether_anything_was_removed(client, runner):
    body = client.post(
        "/api/cli/acli/uninstall", json={"name": "dataphin"}
    ).json()

    assert body["success"] is True
    assert body["data"] == {"name": "dataphin", "uninstalled": True}


def test_uninstall_refusal(client, runner):
    runner.error = AcliError("uninstall_failed", "busy")

    body = client.post(
        "/api/cli/acli/uninstall", json={"name": "dataphin"}
    ).json()

    assert (body["success"], body["error"], body["message"]) == (False, "uninstall_failed", "busy")


def test_market_page(client, runner):
    body = client.get(
        "/api/cli/acli/market", params={"keyword": "data", "page_no": 2, "page_size": 5}
    ).json()

    assert body["success"] is True
    assert body["data"] == {
        "items": [
            {"name": "dataphin", "command": "dataphin", "description": "d", "version": "1.4.14"}
        ],
        "total": 3,
        "has_more": False,
    }
    assert runner.calls == [("market", "data", 2, 5)]


def test_market_defaults(client, runner):
    client.get("/api/cli/acli/market")

    assert runner.calls == [("market", "", 1, 20)]


def test_market_refusal(client, runner):
    runner.error = AcliError("acli_unavailable", "acli is not installed")

    body = client.get("/api/cli/acli/market").json()

    assert (body["success"], body["error"]) == (False, "acli_unavailable")


def test_engine_without_the_capability_refuses_with_501(runner):
    client = _client(set())
    try:
        for method, path, kwargs in (
            ("post", "/api/cli/acli/install",
             {"json": {"name": "x"}}),
            ("post", "/api/cli/acli/uninstall",
             {"json": {"name": "x"}}),
            ("get", "/api/cli/acli/market", {}),
        ):
            assert getattr(client, method)(path, **kwargs).status_code == 501, path
        assert runner.calls == []
    finally:
        EngineManager.reset_instance()


def test_default_runner_is_the_shared_instance():
    assert router_module._acli() is router_module._ACLI_RUNNER


def test_callers_cannot_choose_the_install_directory(client, runner):
    """The engine owns placement; an ``install_dir`` in the body is ignored."""
    client.post("/api/cli/acli/install", json={"name": "dataphin", "install_dir": "/tmp/bin"})

    assert runner.calls == [("install", "dataphin")]


def test_install_directory_comes_from_engine_config(monkeypatch):
    from engine.community.config import DEFAULT_ACLI_INSTALL_DIR, load_acli_install_dir

    monkeypatch.delenv("ENGINE_ACLI_INSTALL_DIR", raising=False)
    assert load_acli_install_dir() == DEFAULT_ACLI_INSTALL_DIR == "/home/admin/.agentclaw/bin"
    monkeypatch.setenv("ENGINE_ACLI_INSTALL_DIR", " /opt/tools/bin ")
    assert load_acli_install_dir() == "/opt/tools/bin"
