"""Public session-file adapter tests: no topology fields leave this boundary."""

from __future__ import annotations

import inspect
import importlib
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.sessions import router
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.api.session_resource_service import SessionResourceServiceProtocol
from agentclaw.community.core.runtime_binding.service import RuntimeBindingResolutionService
from agentclaw.community.core.session_resources.types import SessionResourceStatus
from tests.community.adapters.http.openapi_v1.conftest import user_scoped_client

from .conftest import BOT, OWNER, FakeRelay, fails, ok

SESSION_ID = "session:file-test:user:1"


def test_session_file_endpoints_are_declared_in_the_existing_session_router():
    router_module = importlib.import_module(
        "agentclaw.community.adapters.http.openapi_v1.engine_runtime.sessions.router"
    )

    source = inspect.getsource(router_module)

    assert "session_files_router" not in source
    assert "include_router(" not in source
    assert "SessionFileTargetResolverProtocol" not in source
    assert "_session_file_context" not in source


class _Body:
    def __init__(self) -> None:
        self.closed = False

    def __aiter__(self):
        return self._chunks()

    async def _chunks(self):
        yield b"file-bytes"


class _Upstream:
    def __init__(self) -> None:
        self.body = _Body()
        self.headers = {
            "content-type": "text/plain",
            "content-length": "10",
            "x-internal": "not-forwarded",
            "content-disposition": "attachment; filename=bad\r\nX: y",
        }
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _Resources:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.upstream = _Upstream()
        self.ready_records = [self._record(status=SessionResourceStatus.READY)]
        self.failures: dict[str, Exception] = {}

    @staticmethod
    def _record(resource_id: str = "sr_1", status=SessionResourceStatus.UPLOAD_URL_ISSUED):
        return SimpleNamespace(
            resource_id=resource_id,
            display_name="report.txt",
            status=status,
            size_bytes=10,
            client_content_hash="hash",
            task_version=1,
            error_code=None,
            filename="report.txt",
            transfer_id="tr_1",
        )

    def create_upload_intent(self, **kwargs):
        self.calls.append(("intent", kwargs))
        return SimpleNamespace(
            resource=self._record(),
            grant=SimpleNamespace(
                upload_url="https://upload.example.test/one",
                transfer_id="tr_1",
                upload_type="SINGLE",
                http_method="PUT",
                expires_at=None,
                upload_session_id=None,
                part_size=None,
                part_count=None,
                parts=None,
            ),
        )

    def complete_upload(self, **kwargs):
        self.calls.append(("complete", kwargs))
        if failure := self.failures.get("complete"):
            raise failure
        return self._record(status=SessionResourceStatus.DEVICE_SYNCING)

    def get_status(self, **kwargs):
        self.calls.append(("status", kwargs))
        if failure := self.failures.get("status"):
            raise failure
        return self._record()

    def list_resources(self, **kwargs):
        self.calls.append(("list", kwargs))
        return self.ready_records

    async def open_content(self, **kwargs):
        self.calls.append(("content", kwargs))
        return self._record(status=SessionResourceStatus.READY), self.upstream

    def delete(self, **kwargs):
        self.calls.append(("delete", kwargs))
        return self._record(status=SessionResourceStatus.DELETED)



class _RuntimeBindings:
    def __init__(self) -> None:
        self.requests: list[object] = []

    def resolve(self, request):
        self.requests.append(request)
        return SimpleNamespace(binding_id=101)


@pytest.fixture
def relay() -> FakeRelay:
    return FakeRelay()


@pytest.fixture
def resources() -> _Resources:
    return _Resources()


@pytest.fixture
def runtime_bindings() -> _RuntimeBindings:
    return _RuntimeBindings()


@pytest.fixture
def client(relay, resources, runtime_bindings):
    class _Bindings(Module):
        def configure(self, binder):
            binder.bind(EngineRuntimeRelayProtocol, to=relay)
            binder.bind(SessionResourceServiceProtocol, to=resources)
            binder.bind(RuntimeBindingResolutionService, to=runtime_bindings)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": OWNER}
    attach_injector(app, Injector([_Bindings()]))
    return user_scoped_client(app, OWNER)


def _base() -> str:
    return f"/openapi/v1/bots/{BOT}/sessions/{SESSION_ID}/files"


def test_upload_rejects_all_internal_routing_fields(client, resources):
    response = client.post(
        "/openapi/v1/bots/b1/sessions/s/files/upload-intents",
        json={"binding_id": 1, "files": [{"filename": "a.txt"}]},
    )
    assert response.status_code == 422
    assert resources.calls == []


def test_upload_resolves_binding_then_calls_legacy_service(
    client, relay, resources, runtime_bindings
):
    response = client.post(_base() + "/upload-intents", json={"files": [{"filename": "report.txt"}]})
    assert response.status_code == 201, response.json()
    data = response.json()["data"]
    assert data["files"][0]["resource_id"] == "sr_1"
    assert "binding_id" not in str(data)
    assert "device_uuid" not in str(data)
    assert "owner_id" not in str(data)
    assert "caller_id" not in str(data)
    assert data["files"][0]["upload_url"] == "https://upload.example.test/one"
    assert resources.calls[0][0] == "intent"
    assert resources.calls[0][1]["binding_id"] == 101
    assert resources.calls[0][1]["scope_type"] == "openapi_session"
    assert runtime_bindings.requests[0].bot_id == BOT
    assert runtime_bindings.requests[0].owner_id == OWNER
    assert runtime_bindings.requests[0].actor_user_id == OWNER
    assert relay.attempts == []


def test_ready_list_and_delete_use_the_legacy_session_resource_service(
    client, relay, resources
):
    ready = ok(client.get(_base()))
    assert ready["files"][0]["status"] == "ready"
    assert resources.calls[-1][0] == "list"
    assert resources.calls[-1][1]["ready_only"] is True

    ok(client.delete(_base() + "/sr_1"))
    assert resources.calls[-1][0] == "delete"
    assert relay.attempts == []


def test_content_filters_headers_and_closes_the_upstream(client, resources):
    response = client.get(_base() + "/sr_1/content")
    assert response.status_code == 200
    assert response.content == b"file-bytes"
    assert response.headers["content-type"] == "text/plain"
    assert "x-internal" not in response.headers
    assert "X: y" not in response.headers.get("content-disposition", "")
    assert resources.upstream.closed is True
    assert resources.calls[-1][0] == "content"


def test_complete_and_status_return_public_resources(client, resources):
    complete = ok(client.post(
        _base() + "/upload-complete",
        json={"resource_id": "sr_1", "transfer_id": "tr_1"},
    ))
    status = ok(client.get(_base() + "/sr_1/materialize-status"))

    assert complete["resource_id"] == "sr_1"
    assert status["resource_id"] == "sr_1"
    assert [name for name, _ in resources.calls] == ["complete", "status"]


def test_openapi_never_exposes_raw_materialization_error_details(client, resources):
    resources.ready_records = [
        SimpleNamespace(
            **{
                **resources._record().__dict__,
                "error_code": "engine path /private/device-a failed",
            }
        )
    ]

    data = ok(client.get(_base()))

    assert data["files"][0]["error_code"] == "materialization_failed"
    assert "private/device-a" not in str(data)


@pytest.mark.parametrize(
    ("operation", "request_data"),
    [
        ("complete", ("post", "/upload-complete", {"resource_id": "sr_1", "transfer_id": "tr_1"})),
        ("status", ("get", "/sr_1/materialize-status", None)),
    ],
)
def test_resource_lifecycle_failures_use_not_found_envelope(
    client, resources, operation, request_data
):
    resources.failures[operation] = ValueError("raw-resource-state")
    method, suffix, body = request_data

    call = getattr(client, method)
    response = call(_base() + suffix, json=body) if body is not None else call(_base() + suffix)

    error = fails(response, 404)
    assert error["message"] == "Not found"
