"""Engine-authoritative operations work without any upload repository record."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agentclaw.community.core.engine_runtime.errors import EngineResourceNotFoundError, EngineUpstreamError
from agentclaw.community.core.engine_runtime.errors import EngineDeviceNotReadyError
from agentclaw.community.core.devices.services.device_context import DeviceNotBoundError, UnknownProviderError
from agentclaw.community.core.runtime_binding.errors import RuntimeBindingNotFoundError
from agentclaw.community.core.resources.service import FileTooLargeError
from agentclaw.community.core.runtime_binding.models import RuntimeBindingRequest
from agentclaw.community.core.session_resources.engine_files import EngineSessionFilesService
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterEndpointNotFoundError, DeviceAdapterHTTPStatusError,
    DeviceAdapterStreamResponse,
)


@pytest.fixture
def setup():
    bindings = Mock()
    bindings.resolve.return_value = SimpleNamespace(binding_id=73)
    resolver = Mock()
    resolver.resolve_for_binding.return_value = SimpleNamespace(
        conn_info={"headers": {"Authorization": "private-test-credential"}},
    )
    transport = SimpleNamespace(invoke=AsyncMock(), stream=AsyncMock())
    bots = Mock()
    bots.get_by_id_and_owner.return_value = {"bot_type": "personal", "call_type": "owner"}
    service = EngineSessionFilesService(bindings, resolver, transport, bots)
    target = RuntimeBindingRequest("bot", "owner", "actor", "online")
    return service, target, bindings, resolver, transport


@pytest.mark.asyncio
async def test_list_uses_selected_engine_and_projects_safe_view(setup, caplog):
    service, target, bindings, resolver, transport = setup
    transport.invoke.return_value = {"files": [{
        "resource_id": "sr_local", "display_name": "report.txt", "size_bytes": 7,
        "availability": "ready", "uploaded_at": "2026-10-29T00:00:00Z",
        "secret": "not-public",
    }]}
    with caplog.at_level("INFO"):
        files = await service.list_files(target, "session:one")
    assert files == [{"resource_id": "sr_local", "display_name": "report.txt",
                      "status": "ready", "size_bytes": 7}]
    bindings.resolve.assert_called_once_with(target)
    resolver.resolve_for_binding.assert_called_once_with(73, "actor", bot_id="bot")
    assert transport.invoke.await_args.args[1:] == ("GET", "/api/session-files")
    assert transport.invoke.await_args.kwargs["params"] == {"sessionKey": "session:one"}
    assert "session_files.engine.request" in caplog.text
    assert "session_files.engine.response" in caplog.text
    assert "session:one" in caplog.text
    assert "private-test-credential" not in caplog.text
    assert "not-public" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, {"files": {}}, {"files": [1]},
    {"files": [{"resource_id": "x", "display_name": "x", "size_bytes": 1,
                "availability": "unknown"}]}])
async def test_invalid_list_is_upstream_failure(setup, payload):
    service, target, _, _, transport = setup
    transport.invoke.return_value = payload
    with pytest.raises(EngineUpstreamError):
        await service.list_files(target, "s")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,expected", [
    (DeviceAdapterEndpointNotFoundError("missing"), EngineResourceNotFoundError),
    (DeviceAdapterHTTPStatusError(503, "private-test-credential"), EngineUpstreamError),
    (TimeoutError("private-test-credential"), TimeoutError),
    (ValueError("private-test-credential"), EngineUpstreamError),
])
async def test_list_failures_preserve_categories_without_raw_error(setup, caplog, failure, expected):
    service, target, _, _, transport = setup
    transport.invoke.side_effect = failure
    with caplog.at_level("INFO"), pytest.raises(expected):
        await service.list_files(target, "s")
    assert "session_files.engine.failure" in caplog.text
    assert "private-test-credential" not in caplog.text


@pytest.mark.asyncio
async def test_delete_targets_engine_not_database(setup):
    service, target, _, _, transport = setup
    transport.invoke.return_value = {"deleted": True, "resource_id": "sr_local"}
    assert await service.delete_file(target, "s", "sr_local") is None
    assert transport.invoke.await_args.args[1:] == ("DELETE", "/api/session-files/sr_local")
    transport.invoke.return_value = {"deleted": False, "resource_id": "sr_local"}
    with pytest.raises(EngineUpstreamError):
        await service.delete_file(target, "s", "sr_local")


@pytest.mark.asyncio
@pytest.mark.parametrize("resource_id", [".", "..", "x/y", "x\\y"])
async def test_resource_path_cannot_escape_endpoint(setup, resource_id):
    service, target, _, _, transport = setup
    with pytest.raises(EngineResourceNotFoundError):
        await service.delete_file(target, "s", resource_id)
    transport.invoke.assert_not_awaited()


async def _body():
    yield b"snapshot"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 202])
async def test_content_returns_unconsumed_response_with_session_scope(setup, status, caplog):
    service, target, _, _, transport = setup
    close = AsyncMock()
    upstream = DeviceAdapterStreamResponse(status, {"Cache-Control": "no-store"}, _body(), close)
    transport.stream.return_value = upstream
    with caplog.at_level("INFO"):
        result = await service.open_content(target, "s", "sr_local", "attachment")
    assert result is upstream
    close.assert_not_awaited()
    assert transport.stream.await_args.args[1:] == ("GET", "/api/session-files/sr_local/content")
    assert transport.stream.await_args.kwargs["params"] == {
        "sessionKey": "s", "disposition": "attachment",
    }
    assert "session_files.engine.response" in caplog.text
    assert "private-test-credential" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("status,expected", [
    (404, EngineResourceNotFoundError), (413, FileTooLargeError),
    (409, EngineResourceNotFoundError), (503, EngineUpstreamError), (302, EngineUpstreamError),
])
async def test_content_failure_closes_response(setup, status, expected, caplog):
    service, target, _, _, transport = setup
    close = AsyncMock()
    transport.stream.return_value = DeviceAdapterStreamResponse(status, {}, _body(), close)
    with caplog.at_level("INFO"), pytest.raises(expected):
        await service.open_content(target, "s", "sr_local", "inline")
    close.assert_awaited_once()
    assert "session_files.engine.failure" in caplog.text


@pytest.mark.asyncio
async def test_content_rejects_invalid_disposition_before_transport(setup):
    service, target, _, _, transport = setup
    with pytest.raises(ValueError, match="invalid_disposition"):
        await service.open_content(target, "s", "sr_local", "bad")
    transport.stream.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolution_failure_never_contacts_engine(setup, caplog):
    service, target, bindings, _, transport = setup
    bindings.resolve.side_effect = ValueError("private-test-credential")
    with caplog.at_level("INFO"), pytest.raises(EngineUpstreamError):
        await service.open_content(target, "s", "sr_local", "inline")
    transport.stream.assert_not_awaited()
    assert "session_files.engine.failure" in caplog.text
    assert "private-test-credential" not in caplog.text


@pytest.mark.asyncio
async def test_stream_transport_404_maps_to_resource_error(setup):
    service, target, _, _, transport = setup
    transport.stream.side_effect = DeviceAdapterEndpointNotFoundError("missing")
    with pytest.raises(EngineResourceNotFoundError):
        await service.open_content(target, "s", "sr_local", "inline")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,expected", [
    (RuntimeBindingNotFoundError("private-test-credential"), EngineDeviceNotReadyError),
    (DeviceNotBoundError("private-test-credential"), EngineDeviceNotReadyError),
    (UnknownProviderError("private-test-credential"), EngineUpstreamError),
])
async def test_unavailable_binding_is_not_a_file_404(setup, failure, expected, caplog):
    service, target, bindings, resolver, transport = setup
    if isinstance(failure, RuntimeBindingNotFoundError):
        bindings.resolve.side_effect = failure
    else:
        resolver.resolve_for_binding.side_effect = failure
    with caplog.at_level("INFO"), pytest.raises(expected):
        await service.list_files(target, "s")
    transport.invoke.assert_not_awaited()
    assert "session_files.engine.failure" in caplog.text
    assert "private-test-credential" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("bot_type", ["service", "personal"])
async def test_shared_runtime_keeps_reader_identity_for_friend_subject(setup, bot_type):
    service, _, bindings, resolver, transport = setup
    target = RuntimeBindingRequest("bot", "owner", "owner", "online" if bot_type == "service" else "draft")
    service._bots.get_by_id_and_owner.return_value = {"bot_type": bot_type, "call_type": "owner"}
    transport.invoke.return_value = {"files": []}
    assert await service.list_files(target, "s", session_user_id="friend") == []
    bindings.resolve.assert_called_once_with(target)
    resolver.resolve_for_binding.assert_called_once_with(73, "owner", bot_id="bot")


@pytest.mark.asyncio
async def test_only_owner_can_select_another_session_subject(setup):
    service, target, bindings, _, transport = setup
    with pytest.raises(EngineResourceNotFoundError):
        await service.list_files(target, "s", session_user_id="friend")
    bindings.resolve.assert_not_called()
    transport.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_bot_does_not_select_subject_binding(setup):
    service, _, bindings, _, transport = setup
    service._bots.get_by_id_and_owner.return_value = None
    with pytest.raises(EngineResourceNotFoundError):
        await service.list_files(RuntimeBindingRequest("bot", "owner", "owner"), "s", session_user_id="friend")
    bindings.resolve.assert_not_called()
    transport.invoke.assert_not_awaited()
