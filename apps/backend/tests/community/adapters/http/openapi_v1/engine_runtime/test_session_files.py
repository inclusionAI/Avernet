"""Public session-file adapter tests: no topology fields leave this boundary."""

from __future__ import annotations

import inspect
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.sessions import router
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.api.expert_chat_service import ExpertChatServiceProtocol
from agentclaw.community.api.human_bot_friendship_service import HumanBotFriendshipServiceProtocol
from agentclaw.community.core.expert_chat.errors import BotNotFoundError
from agentclaw.community.api.tc_resource_ready_observer import (
    TcResourceReadyObserverProtocol,
)
from agentclaw.community.api.session_resource_service import SessionResourceServiceProtocol
from agentclaw.community.api.engine_session_files import EngineSessionFilesProtocol
from agentclaw.community.core.runtime_binding.service import RuntimeBindingResolutionService
from agentclaw.community.core.session_resources.types import SessionResourceStatus
from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
    user_scoped_client,
)

from .conftest import BOT, OWNER, FakeRelay, bind_seam_from_relay, fails, ok

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
    def __init__(self, content: bytes = b"file-bytes") -> None:
        self.closed = False
        self.content = content

    def __aiter__(self):
        return self._chunks()

    async def _chunks(self):
        yield self.content


class _Upstream:
    def __init__(
        self,
        *,
        status_code: int = 200,
        content: bytes = b"file-bytes",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.body = _Body(content)
        self.headers = headers or {
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

    async def open_session_file_content(self, **kwargs):
        self.calls.append(("session_file_content", kwargs))
        if failure := self.failures.get("content"):
            raise failure
        return self._record(status=SessionResourceStatus.READY), self.upstream

    def delete(self, **kwargs):
        self.calls.append(("delete", kwargs))
        return self._record(status=SessionResourceStatus.DELETED)



class _Observer:
    def __init__(self) -> None:
        self.records = []

    def notify_in_background(self, resource) -> None:
        self.records.append(resource)


class _EngineFiles:
    def __init__(self, resources):
        self.resources = resources

    async def list_files(self, target, session_key, *, session_user_id=None):
        self.resources.calls.append(("list", {
            "owner_id": target.owner_id, "bot_id": target.bot_id,
            "session_key": session_key, "actor_user_id": target.actor_user_id,
            "stage": target.stage,
            "session_user_id": session_user_id,
        }))
        return [{"resource_id": record.resource_id, "display_name": record.display_name,
                 "status": record.status.value, "size_bytes": record.size_bytes}
                for record in self.resources.ready_records]

    async def open_content(self, target, session_key, resource_id, disposition, *, session_user_id=None):
        _, upstream = await self.resources.open_session_file_content(
            target=target, session_key=session_key, resource_id=resource_id, disposition=disposition,
        )
        return upstream

    async def delete_file(self, target, session_key, resource_id):
        self.resources.calls.append(("delete", {
            "target": target, "session_key": session_key, "resource_id": resource_id,
        }))


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
def observer() -> _Observer:
    return _Observer()


@pytest.fixture
def client(relay, resources, runtime_bindings, observer, friendships, expert):
    class _Bindings(Module):
        def configure(self, binder):
            binder.bind(EngineRuntimeRelayProtocol, to=relay)
            binder.bind(HumanBotFriendshipServiceProtocol, to=friendships)
            binder.bind(ExpertChatServiceProtocol, to=expert)
            binder.bind(SessionResourceServiceProtocol, to=resources)
            binder.bind(EngineSessionFilesProtocol, to=_EngineFiles(resources))
            binder.bind(RuntimeBindingResolutionService, to=runtime_bindings)
            binder.bind(TcResourceReadyObserverProtocol, to=observer)
            # Mutations/content retain Check(MEMBER); list checks operator
            # access or an explicitly addressed, owned friend session.
            bind_seam_from_relay(binder, relay)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": OWNER}
    mount_public_error_handlers(app)
    attach_injector(app, Injector([_Bindings()]))
    return user_scoped_client(app, OWNER)


def _base() -> str:
    return f"/openapi/v1/bots/{BOT}/sessions/{SESSION_ID}/files"


@pytest.fixture
def friend_client(client, friendships):
    friendships.allowed = True
    client.app.dependency_overrides[require_principal] = lambda: {"user_id": "friend-1"}
    return user_scoped_client(client.app, "friend-1")


def test_friend_list_checks_friendship_and_session_ownership_before_resources(
    friend_client, resources, friendships, expert, relay,
):
    data = ok(friend_client.get(_base(), params={"owner_id": OWNER, "f_user_id": "friend-1"}))
    assert data["files"][0]["resource_id"] == "sr_1"
    assert friendships.calls[0]["human_id"] == "friend-1"
    owned = next(call for call in expert.calls if call[0] == "get_owned_chat_session")
    assert owned[1][:4] == ("friend-1", BOT, OWNER, SESSION_ID)
    assert owned[2]["bcn_friend_authorized"] is True
    assert resources.calls == [("list", {
        "owner_id": OWNER, "bot_id": BOT, "session_key": SESSION_ID,
        "actor_user_id": "friend-1", "stage": "draft", "session_user_id": "friend-1",
    })]
    assert relay.attempts == []


def test_owner_can_list_one_verified_friends_session(client, friendships, expert):
    friendships.allowed = True
    assert client.get(_base(), params={"f_user_id": "friend-1"}).status_code == 200
    owned = next(call for call in expert.calls if call[0] == "get_owned_chat_session")
    assert owned[1][:4] == ("friend-1", BOT, OWNER, SESSION_ID)


@pytest.mark.parametrize("selector", [None, "online"])
def test_friend_service_files_use_resolved_online_stage(client, friendships, expert, resources, selector):
    from unittest.mock import Mock

    friendships.allowed = True
    expert.resolve_chat_stage = Mock(return_value="online")
    params = {"f_user_id": "friend-1"}
    if selector is not None:
        params["stage"] = selector
    ok(client.get(_base(), params=params))
    assert resources.calls[-1][1]["stage"] == "online"
    assert resources.calls[-1][1]["session_user_id"] == "friend-1"
    assert client.get(_base() + "/sr_1/content", params=params).status_code == 200
    assert resources.calls[-1][1]["target"].stage == "online"


def test_owner_reads_friend_caller_files_from_friend_binding(
    client, friendships, expert, resources,
):
    from unittest.mock import Mock, AsyncMock
    from agentclaw.community.core.session_resources.engine_files import EngineSessionFilesService

    friendships.allowed = True
    expert.resolve_chat_stage = Mock(return_value="online")
    bot_repository = Mock()
    bot_repository.get_by_id_and_owner.return_value = {
        "id": 1, "bot_id": BOT, "owner_id": OWNER,
        "bot_type": "service", "call_type": "caller",
    }
    instances = Mock()

    def instance(user_id, bot_id, owner_id):
        assert (user_id, bot_id, owner_id) == ("friend-1", BOT, OWNER)
        return {"status": "success", "ext": {"binding_id": 82}}

    instances.get_instance.side_effect = instance
    binding_repository = Mock()
    binding_repository.get_by_id.return_value = SimpleNamespace(
        id=82, status="ACTIVE", env="test", entity_id=OWNER,
        applied_by="friend-1", apply_reason=f"caller_instance:{BOT}", device_provider="baas",
    )
    bindings = RuntimeBindingResolutionService(
        bot_repository=bot_repository, binding_repository=binding_repository,
        publish_repository=Mock(), caller_instance_repository=instances,
        environment_provider=lambda: "test",
    )
    resolver = Mock()
    resolver.resolve_for_binding.return_value = SimpleNamespace(conn_info={})
    transport = SimpleNamespace(
        invoke=AsyncMock(return_value={"files": [{
            "resource_id": "workspace_friend", "display_name": "friend.txt",
            "size_bytes": 10, "availability": "ready",
        }]}), stream=AsyncMock(return_value=_Upstream()),
    )
    service = EngineSessionFilesService(bindings, resolver, transport, bot_repository)
    client.app.state.injector.binder.bind(EngineSessionFilesProtocol, to=service)
    assert ok(client.get(_base(), params={"f_user_id": "friend-1"}))["files"][0]["resource_id"] == "workspace_friend"
    assert client.get(_base() + "/workspace_friend/content", params={"f_user_id": "friend-1"}).status_code == 200
    assert instances.get_instance.call_count == 2
    assert all(call.args == (82, "friend-1") for call in resolver.resolve_for_binding.call_args_list)
    assert resources.calls == []


@pytest.mark.parametrize("operation", ["list", "content", "delete"])
def test_real_engine_boundary_does_not_leak_exception_credentials(client, operation, caplog):
    from unittest.mock import Mock, AsyncMock
    from agentclaw.community.core.session_resources.engine_files import EngineSessionFilesService
    from agentclaw.community.plugin_api.device_adapter_transport import DeviceAdapterHTTPStatusError

    secret = "Authorization: Bearer private-token Cookie=private-cookie Signature=private-signature"
    transport = SimpleNamespace(
        invoke=AsyncMock(side_effect=DeviceAdapterHTTPStatusError(503, secret)),
        stream=AsyncMock(side_effect=ValueError(secret)),
    )
    bindings = Mock()
    bindings.resolve.return_value = SimpleNamespace(binding_id=101)
    resolver = Mock()
    resolver.resolve_for_binding.return_value = SimpleNamespace(conn_info={})
    client.app.state.injector.binder.bind(
        EngineSessionFilesProtocol, to=EngineSessionFilesService(bindings, resolver, transport, Mock()),
    )
    with caplog.at_level("INFO"):
        if operation == "delete":
            response = client.delete(_base() + "/sr_1")
        else:
            response = client.get(_base() + ("/sr_1/content" if operation == "content" else ""))
    assert response.status_code == 502
    for value in ("private-token", "private-cookie", "private-signature"):
        assert value not in caplog.text
        assert value not in response.text
    assert "session_files.engine.failure" in caplog.text


def test_real_engine_preview_too_large_stays_413(client):
    from unittest.mock import Mock, AsyncMock
    from agentclaw.community.core.resources.service import FileTooLargeError

    service = SimpleNamespace(open_content=AsyncMock(side_effect=FileTooLargeError("File too large for preview")),
                              list_files=Mock(), delete_file=Mock())
    client.app.state.injector.binder.bind(EngineSessionFilesProtocol, to=service)
    assert client.get(_base() + "/sr_1/content").status_code == 413


def test_friendship_source_failure_never_reads_resources(friend_client, friendships, resources):
    from unittest.mock import Mock
    from agentclaw.community.core.bot_chat.bcn_friendship import FriendshipSourceUnavailableError

    friendships.is_friend = Mock(side_effect=FriendshipSourceUnavailableError("unavailable"))
    response = friend_client.get(_base(), params={"owner_id": OWNER, "f_user_id": "friend-1"})
    assert response.status_code == 409
    assert resources.calls == []


@pytest.mark.parametrize("reason", ["not_friend", "foreign_identity", "foreign_session", "missing_parameter"])
def test_friend_list_refuses_unauthorized_access_without_reading_resources(
    friend_client, resources, friendships, expert, reason,
):
    from unittest.mock import AsyncMock

    params = {"owner_id": OWNER, "f_user_id": "friend-1"}
    if reason == "not_friend":
        friendships.allowed = False
    elif reason == "foreign_identity":
        params["f_user_id"] = "friend-2"
    elif reason == "foreign_session":
        expert.get_owned_chat_session = AsyncMock(side_effect=BotNotFoundError("foreign session"))
    elif reason == "missing_parameter":
        del params["f_user_id"]
    assert fails(friend_client.get(_base(), params=params), 404)["message"] == "Not found"
    assert resources.calls == []


def test_friend_file_list_supports_online(friend_client, resources):
    response = friend_client.get(_base(), params={
        "owner_id": OWNER, "f_user_id": "friend-1", "stage": "online",
    })
    assert response.status_code == 200
    assert resources.calls[0][0] == "list"


def test_friend_file_list_rejects_verify_before_reading_resources(friend_client, resources):
    response = friend_client.get(_base(), params={
        "owner_id": OWNER, "f_user_id": "friend-1", "stage": "verify",
    })
    assert response.status_code == 409
    assert resources.calls == []


@pytest.mark.parametrize("suffix,method", [("/sr_1", "delete"), ("/upload-intents", "post")])
def test_friend_parameter_does_not_grant_other_file_permissions(friend_client, resources, suffix, method):
    response = getattr(friend_client, method)(
        _base() + suffix, params={"owner_id": OWNER, "f_user_id": "friend-1"},
    )
    assert response.status_code == 404
    assert resources.calls == []


@pytest.mark.parametrize("suffix", ["", "/sr_1/content", "/sr_1/materialize-status"])
def test_online_friend_file_reads_share_owned_session_authorization(friend_client, expert, resources, suffix):
    response = friend_client.get(_base() + suffix, params={
        "owner_id": OWNER, "f_user_id": "friend-1", "stage": "online",
    })
    assert response.status_code == 200, response.text
    assert any(call[0] == "get_owned_chat_session" for call in expert.calls)
    assert len(resources.calls) == 1


@pytest.mark.parametrize("suffix", ["", "/sr_1/content", "/sr_1/materialize-status"])
def test_foreign_session_cannot_read_friend_files(friend_client, expert, resources, suffix):
    from unittest.mock import AsyncMock

    expert.get_owned_chat_session = AsyncMock(side_effect=BotNotFoundError("foreign"))
    response = friend_client.get(_base() + suffix, params={
        "owner_id": OWNER, "f_user_id": "friend-1", "stage": "online",
    })
    assert response.status_code == 404
    assert resources.calls == []


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


def test_ready_list_and_delete_use_the_engine_session_file_service(
    client, relay, resources
):
    ready = ok(client.get(_base()))
    assert ready["files"][0]["status"] == "ready"
    assert resources.calls[-1][0] == "list"
    assert resources.calls[-1][1]["actor_user_id"] == OWNER

    ok(client.delete(_base() + "/sr_1"))
    assert resources.calls[-1][0] == "delete"
    assert relay.attempts == []


def test_generated_file_roundtrip_reaches_real_engine_without_upload_record(
    client, resources, runtime_bindings, tmp_path, monkeypatch,
):
    """Register on Engine, access through public API, retain the source after deletion."""
    import httpx
    from unittest.mock import Mock

    from agentclaw.community.core.session_resources.engine_files import EngineSessionFilesService
    from agentclaw.community.plugin_api.device_adapter_transport import DeviceAdapterStreamResponse

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[8] / "engine/adapter/src"))
    engine_router = importlib.import_module("engine.community.api.session_files.router").router
    engine_service_type = importlib.import_module("engine.community.core.session_files.service").SessionFileService
    export_service_type = importlib.import_module("engine.community.core.session_files.export_service").SessionFileExportService
    engine_service = engine_service_type(workspace_root_provider=lambda: tmp_path)
    engine_app = FastAPI()
    engine_injector = Injector()
    engine_injector.binder.bind(engine_service_type, to=engine_service)
    engine_injector.binder.bind(export_service_type, to=SimpleNamespace())
    attach_injector(engine_app, engine_injector)
    engine_app.include_router(engine_router)
    source = tmp_path / "report.txt"
    source.write_bytes(b"chat-generated-snapshot")

    class _Transport:
        async def invoke(self, _connection, method, path, *, params, timeout):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=engine_app), base_url="http://engine.test") as http:
                response = await http.request(method, path, params=params)
            response.raise_for_status()
            return response.json()

        async def stream(self, _connection, method, path, *, params, timeout):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=engine_app), base_url="http://engine.test") as http:
                response = await http.request(method, path, params=params)

            async def body():
                yield response.content

            return DeviceAdapterStreamResponse(response.status_code, response.headers, body(), response.aclose)

    resolver = Mock()
    resolver.resolve_for_binding.return_value = SimpleNamespace(conn_info={})
    service = EngineSessionFilesService(runtime_bindings, resolver, _Transport(), Mock())
    client.app.state.injector.binder.bind(EngineSessionFilesProtocol, to=service)
    from fastapi.testclient import TestClient

    with TestClient(engine_app) as engine_client:
        registration = engine_client.post("/api/session-files", params={"sessionKey": SESSION_ID},
                                         json={"workspace_relative_path": "report.txt"})
        assert registration.status_code == 200, registration.text
        view = registration.json()
        resource_id = view["resource_id"]
        direct = engine_client.get("/api/session-files", params={"sessionKey": SESSION_ID}).json()
        assert direct["files"] == [view]

    public = ok(client.get(_base()))["files"]
    assert public[0]["resource_id"] == resource_id
    assert public[0]["status"] == "ready"
    content_url = _base() + f"/{resource_id}/content"
    preview = client.get(content_url)
    assert preview.status_code == 200 and preview.content == source.read_bytes()
    download = client.get(content_url, params={"disposition": "attachment"})
    assert download.status_code == 200 and download.content == source.read_bytes()
    assert download.headers["content-disposition"].startswith("attachment;")
    assert ok(client.get(_base().replace(SESSION_ID, "another-session")))["files"] == []
    ok(client.delete(_base() + f"/{resource_id}"))
    assert ok(client.get(_base()))["files"] == []
    assert source.read_bytes() == b"chat-generated-snapshot"
    assert resources.calls == []


def test_content_filters_headers_and_closes_the_upstream(client, resources):
    response = client.get(_base() + "/sr_1/content")
    assert response.status_code == 200
    assert response.content == b"file-bytes"
    assert response.headers["content-type"] == "text/plain"
    assert "x-internal" not in response.headers
    assert "X: y" not in response.headers.get("content-disposition", "")
    assert resources.upstream.closed is True
    assert resources.calls[-1][0] == "session_file_content"


def test_content_preserves_engine_download_preparation_response(client, resources):
    resources.upstream = _Upstream(
        status_code=202,
        content=b'{"status":"preparing_download","retry_after_seconds":2}',
        headers={
            "content-type": "application/json",
            "retry-after": "2",
            "cache-control": "no-store",
            "x-internal": "not-forwarded",
        },
    )

    response = client.get(_base() + "/sr_1/content?disposition=attachment")

    assert response.status_code == 202
    assert response.json() == {
        "status": "preparing_download",
        "retry_after_seconds": 2,
    }
    assert response.headers["retry-after"] == "2"
    assert response.headers["cache-control"] == "no-store"
    assert "content-disposition" not in response.headers
    assert "x-internal" not in response.headers


def test_content_rejects_unsafe_export_control_headers(client, resources):
    resources.upstream = _Upstream(
        status_code=202,
        content=b'{"status":"preparing_download","retry_after_seconds":2}',
        headers={
            "content-type": "application/json",
            "retry-after": "soon",
            "cache-control": "public, max-age=3600",
        },
    )

    response = client.get(_base() + "/sr_1/content?disposition=attachment")

    assert response.status_code == 202
    assert "retry-after" not in response.headers
    assert "cache-control" not in response.headers


def test_content_preserves_engine_external_download_response(client, resources):
    resources.upstream = _Upstream(
        content=(
            b'{"status":"ready","delivery":"external_url",'
            b'"download_url":"https://download.example.test/file",'
            b'"expires_at":"2026-08-22T12:00:00Z",'
            b'"filename":"report.txt","size_bytes":52428800}'
        ),
        headers={
            "content-type": "application/json; charset=utf-8",
            "cache-control": "no-store",
        },
    )

    response = client.get(_base() + "/sr_1/content?disposition=attachment")

    assert response.status_code == 200
    assert response.json()["delivery"] == "external_url"
    assert response.headers["cache-control"] == "no-store"
    assert "content-disposition" not in response.headers


def test_content_maps_engine_preview_limit_to_openapi_413(client, resources):
    resources.failures["content"] = ValueError("resource_preview_too_large")

    response = client.get(_base() + "/sr_1/content")

    assert response.status_code == 413
    assert response.json()["message"] == "File too large for preview"


def test_content_maps_unavailable_engine_to_openapi_502(client, resources):
    resources.failures["content"] = ValueError("engine_content_unavailable")

    response = client.get(_base() + "/sr_1/content")

    assert response.status_code == 502
    assert response.json()["message"] == "Engine service error"


def test_content_masks_missing_engine_resource_as_openapi_404(client, resources):
    resources.failures["content"] = ValueError("resource_missing")

    response = client.get(_base() + "/sr_1/content")

    assert response.status_code == 404
    assert response.json()["message"] == "Not found"


def test_complete_and_status_return_public_resources(client, resources, observer):
    complete = ok(client.post(
        _base() + "/upload-complete",
        json={"resource_id": "sr_1", "transfer_id": "tr_1"},
    ))
    status = ok(client.get(_base() + "/sr_1/materialize-status"))

    assert complete["resource_id"] == "sr_1"
    assert status["resource_id"] == "sr_1"
    assert [name for name, _ in resources.calls] == ["complete", "status"]
    assert [record.status for record in observer.records] == [
        SessionResourceStatus.DEVICE_SYNCING,
        SessionResourceStatus.UPLOAD_URL_ISSUED,
    ]


def test_engine_list_does_not_expose_backend_materialization_error_details(client, resources):
    resources.ready_records = [
        SimpleNamespace(
            **{
                **resources._record().__dict__,
                "error_code": "engine path /private/device-a failed",
            }
        )
    ]

    data = ok(client.get(_base()))

    assert data["files"][0]["error_code"] is None
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
