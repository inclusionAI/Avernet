"""``/api/cli/acli/*`` — the wire contract ``ArcaAcliPort`` reads.

The operation slot is replaced by a fake: what is pinned here is the HTTP
shape — an ended operation is ``200`` (``success`` false with ``error`` when
it did not succeed), a running one ``202``, ``409`` for ``engine_busy`` and
``not_active``, bad input ``400``, and ``501`` when the engine does not offer
the capability.
"""
from __future__ import annotations

import importlib
from unittest.mock import MagicMock

import pytest
from engine.community.api.cli import router
from engine.community.core.acli.operations import (
    AcliOperation,
    OperationBusyError,
    OperationNotActiveError,
)
from engine.community.core.acli.runner import AcliError, AcliTool
from engine.community.core.engine.base import BaseEngine
from engine.community.core.engine.capability import Capability, EngineCapabilities
from engine.community.core.engine.registry import EngineRegistry
from engine.community.manager import EngineManager
from fastapi import FastAPI
from fastapi.testclient import TestClient

ACLI_CAPS = {
    Capability.CLI_ACLI_LIST,
    Capability.CLI_ACLI_INSTALL,
    Capability.CLI_ACLI_UNINSTALL,
    Capability.CLI_ACLI_CANCEL,
    Capability.CLI_ACLI_OPERATION_GET,
}
acli_module = importlib.import_module("engine.community.api.cli.acli")
OP_ID = "c" * 32


def _op(state: str = "succeeded", *, action: str = "install", **fields) -> AcliOperation:
    op = AcliOperation(
        id=OP_ID, action=action, name="deepinsight-cli", state=state,
        started_at="2026-10-21T00:00:00Z", **fields,
    )
    if not op.active:
        op._done.set()
    return op


class _FakeOperations:
    def __init__(self) -> None:
        self.next: AcliOperation = _op(result={
            "name": "deepinsight-cli", "command": "di", "description": "desc",
            "version": "1.0.9", "already_installed": False,
        })
        self.error: Exception | None = None
        self.calls: list[tuple] = []
        self.stored: dict[str, AcliOperation] = {}

    def submit(self, action: str, name: str) -> AcliOperation:
        self.calls.append((action, name))
        if self.error:
            raise self.error
        return self.next

    def cancel(self, action: str, name: str) -> AcliOperation:
        self.calls.append((f"cancel-{action}", name))
        if self.error:
            raise self.error
        return self.next

    def get(self, op_id: str) -> AcliOperation | None:
        self.calls.append(("get", op_id))
        return self.stored.get(op_id)


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
def ops(monkeypatch) -> _FakeOperations:
    fake = _FakeOperations()
    monkeypatch.setattr(acli_module, "_ACLI_OPERATIONS", fake)
    monkeypatch.setattr(acli_module, "SYNC_WAIT_SECONDS", 0.05)
    return fake


@pytest.fixture
def client():
    client = _client(ACLI_CAPS)
    try:
        yield client
    finally:
        EngineManager.reset_instance()


def test_an_ended_install_is_200_with_the_operation(client, ops):
    response = client.post("/api/cli/acli/install", json={"name": "deepinsight-cli"})

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["operation"] == {
        "id": OP_ID, "action": "install", "name": "deepinsight-cli", "state": "succeeded",
        "started_at": "2026-10-21T00:00:00Z", "finished_at": None,
        "result": {"name": "deepinsight-cli", "command": "di", "description": "desc",
                   "version": "1.0.9", "already_installed": False},
        "error": None,
    }
    assert ops.calls == [("install", "deepinsight-cli")]


def test_a_failed_operation_is_200_with_its_error(client, ops):
    ops.next = _op("failed", error={"code": "install_failed", "message": "no such cli"})

    response = client.post("/api/cli/acli/install", json={"name": "nope"})

    body = response.json()
    assert response.status_code == 200
    assert (body["success"], body["error"], body["message"]) == (
        False, "install_failed", "no such cli",
    )
    assert body["data"]["operation"]["state"] == "failed"


def test_a_cancelled_operation_reads_as_not_succeeded(client, ops):
    ops.stored[OP_ID] = _op("cancelled")

    body = client.get(f"/api/cli/acli/operations/{OP_ID}").json()

    assert (body["success"], body["error"]) == (False, "cancelled")


@pytest.mark.parametrize("run_async", [True, False])
def test_a_running_operation_is_202(client, ops, run_async):
    ops.next = _op("running")

    response = client.post(
        "/api/cli/acli/uninstall", json={"name": "deepinsight-cli", "async": run_async}
    )

    assert response.status_code == 202
    body = response.json()
    assert body["success"] is True
    assert body["data"]["operation"]["state"] == "running"
    assert ops.calls == [("uninstall", "deepinsight-cli")]


def test_a_busy_engine_is_409_with_the_active_operation(client, ops):
    ops.error = OperationBusyError(_op("running", action="uninstall"))

    response = client.post("/api/cli/acli/install", json={"name": "dataphin"})

    assert response.status_code == 409
    body = response.json()
    assert (body["success"], body["error"]) == (False, "engine_busy")
    assert body["data"]["operation"]["action"] == "uninstall"


def test_invalid_input_is_400(client, ops):
    ops.error = AcliError("invalid_input", "bad name")

    assert client.post("/api/cli/acli/install", json={"name": "-x"}).status_code == 400


def test_an_operation_that_could_not_start_is_200_refusal(client, ops):
    ops.error = AcliError("install_failed", "cannot record the acli operation")

    body = client.post("/api/cli/acli/install", json={"name": "dataphin"}).json()

    assert (body["success"], body["error"], body["data"]) == (False, "install_failed", None)


@pytest.mark.parametrize("action", ["install", "uninstall"])
def test_cancel_returns_the_operation(client, ops, action):
    ops.next = _op("cancelling", action=action)

    response = client.post(f"/api/cli/acli/{action}/cancel", json={"name": "deepinsight-cli"})

    assert response.status_code == 202
    assert response.json()["data"]["operation"]["state"] == "cancelling"
    assert ops.calls == [(f"cancel-{action}", "deepinsight-cli")]


def test_cancel_with_nothing_running_is_409_not_active(client, ops):
    ops.error = OperationNotActiveError("no install of x is running")

    response = client.post("/api/cli/acli/install/cancel", json={"name": "x", "async": True})

    assert response.status_code == 409
    assert response.json()["error"] == "not_active"


def test_get_operation(client, ops):
    ops.stored[OP_ID] = _op("running")

    response = client.get(f"/api/cli/acli/operations/{OP_ID}")

    assert response.status_code == 202
    assert response.json()["data"]["operation"]["id"] == OP_ID


def test_an_unknown_operation_is_404_inside_the_envelope(client, ops):
    response = client.get(f"/api/cli/acli/operations/{'d' * 32}")

    assert response.status_code == 404
    assert response.json()["error"] == "operation_not_found"


class _ListingRunner:
    def __init__(self, error: AcliError | None = None) -> None:
        self.error = error

    async def list_tools(self) -> list[AcliTool]:
        if self.error:
            raise self.error
        return [AcliTool(name="deepinsight-cli", command="di", description="d", version="1.0.9")]


def test_tools_lists_what_acli_installed(client, monkeypatch):
    monkeypatch.setattr(acli_module, "_ACLI_RUNNER", _ListingRunner())

    response = client.get("/api/cli/acli/tools")

    assert response.status_code == 200
    assert response.json()["data"] == {"tools": [
        {"name": "deepinsight-cli", "command": "di", "description": "d", "version": "1.0.9"},
    ]}


def test_a_failed_listing_is_a_200_refusal(client, monkeypatch):
    monkeypatch.setattr(
        acli_module, "_ACLI_RUNNER", _ListingRunner(AcliError("list_failed", "registry busy"))
    )

    body = client.get("/api/cli/acli/tools").json()

    assert (body["success"], body["error"], body["message"]) == (
        False, "list_failed", "registry busy",
    )


def test_engine_without_the_capability_refuses_with_501(ops):
    client = _client(set())
    try:
        for method, path, kwargs in (
            ("post", "/api/cli/acli/install", {"json": {"name": "x"}}),
            ("post", "/api/cli/acli/uninstall", {"json": {"name": "x"}}),
            ("post", "/api/cli/acli/install/cancel", {"json": {"name": "x"}}),
            ("post", "/api/cli/acli/uninstall/cancel", {"json": {"name": "x"}}),
            ("get", f"/api/cli/acli/operations/{OP_ID}", {}),
            ("get", "/api/cli/acli/tools", {}),
        ):
            assert getattr(client, method)(path, **kwargs).status_code == 501, path
        assert ops.calls == []
    finally:
        EngineManager.reset_instance()


def test_callers_cannot_choose_the_install_directory(client, ops):
    """The engine owns placement; an ``install_dir`` in the body is ignored."""
    client.post("/api/cli/acli/install", json={"name": "dataphin", "install_dir": "/tmp/bin"})

    assert ops.calls == [("install", "dataphin")]


def test_the_runner_installs_into_the_fixed_engine_directory():
    """The one directory the agent image puts on ``PATH``; not configurable."""
    from engine.community.config import ACLI_INSTALL_DIR

    assert ACLI_INSTALL_DIR == "/home/admin/.agentclaw/bin"
    assert str(acli_module._ACLI_RUNNER._install_dir) == ACLI_INSTALL_DIR
    assert acli_module._operations() is acli_module._ACLI_OPERATIONS
