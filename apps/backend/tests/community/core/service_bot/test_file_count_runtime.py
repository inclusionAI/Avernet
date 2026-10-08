"""Runtime boundary rejects malformed results and preserves sibling outcomes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [None, {}, {"path": "/workspace", "file_count": True, "elapsed_ms": 0},
                                     {"path": "/workspace", "file_count": -1, "elapsed_ms": 0},
                                     {"path": "/workspace", "file_count": 3, "elapsed_ms": False}])
async def test_malformed_count_is_never_success(data):
    from agentclaw.community.kernel.file_count import FileCountBinding, FileCountQuery
    from agentclaw.community.plugins.community.file_count_runtime import HttpFileCountRuntime
    runtime = HttpFileCountRuntime(
        Mock(), Mock(resolve_for_binding_invoke=Mock(return_value=SimpleNamespace(conn_info={}))),
        SimpleNamespace(invoke=AsyncMock(return_value={"success": True, "data": data})), Mock(),
    )
    result = await runtime.query(FileCountBinding(1, "baas", "device"), "replica",
                                 FileCountQuery("bot", "owner", "draft", "/workspace", "r"), "owner")
    assert result["status"] == "failed"
    assert result["file_count"] is None
    assert result["error_code"] == "invalid_engine_response"


@pytest.mark.asyncio
@pytest.mark.parametrize("code,status", [(400, "invalid_path"), (403, "path_forbidden"),
                                         (404, "path_not_found"), (408, "scan_timeout"),
                                         (409, "directory_changed"), (503, "busy"),
                                         (501, "unsupported"), (500, "scan_failed")])
async def test_engine_errors_are_safe(code, status, caplog):
    import json
    caplog.set_level("INFO")
    from agentclaw.community.kernel.file_count import FileCountBinding, FileCountQuery
    from agentclaw.community.plugins.community.file_count_runtime import HttpFileCountRuntime
    from agentclaw.community.plugin_api.device_adapter_transport import DeviceAdapterHTTPStatusError
    response = json.dumps({"detail": {"error_code": status, "token": "secret-value"}})
    runtime = HttpFileCountRuntime(
        Mock(), Mock(resolve_for_binding_invoke=Mock(return_value=SimpleNamespace(conn_info={"token": "secret-value"}))),
        SimpleNamespace(invoke=AsyncMock(side_effect=DeviceAdapterHTTPStatusError(code, response))), Mock(),
    )
    result = await runtime.query(FileCountBinding(1, "baas", "device"), "replica",
                                 FileCountQuery("bot", "owner", "draft", "/workspace", "r"), "owner")
    assert result["error_code"] == status
    assert result["status"] == ("timeout" if code == 408 else "failed")
    assert result["file_count"] is None
    assert "secret-value" not in caplog.text + str(result)
    assert "backend.file_count.engine_failure" in caplog.text
    records = [r for r in caplog.records if "backend.file_count.engine_" in r.msg]
    assert records
    fields = records[-1].args[-1]
    assert fields["instance_id"] == "replica"
    assert fields["request_id"] == "r"
    assert fields["file_count"] is None
    assert fields["error_code"] == status


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["baas", "arca"])
async def test_success_and_timeout(provider, caplog, monkeypatch):
    import asyncio
    import httpx
    caplog.set_level("INFO")
    from agentclaw.community.kernel.file_count import FileCountBinding, FileCountQuery
    from agentclaw.community.plugins.community.file_count_runtime import HttpFileCountRuntime
    deadlines = []
    real_timeout = asyncio.timeout

    def capture_deadline(delay):
        deadlines.append(delay)
        return real_timeout(delay)

    monkeypatch.setattr(asyncio, "timeout", capture_deadline)
    transport = SimpleNamespace(invoke=AsyncMock(return_value={
        "success": True, "data": {"path": "/workspace", "file_count": 0, "elapsed_ms": 0},
    }))
    http = Mock(get=Mock(return_value=httpx.Response(200, json={
        "success": True, "data": {"path": "/workspace", "file_count": 0, "elapsed_ms": 0},
    })))
    runtime = HttpFileCountRuntime(Mock(), Mock(resolve_for_binding_invoke=Mock(return_value=SimpleNamespace(
        conn_info={"url": "http://engine", "headers": {"Authorization": "secret-value"}},
    ))), transport, http)
    query = FileCountQuery("bot", "owner", "draft", "/workspace", "r")
    binding = FileCountBinding(1, provider, "device")
    result = await runtime.query(binding, "replica", query, "owner")
    assert result["status"] == "success"
    assert result["file_count"] == 0
    call = transport.invoke.call_args if provider == "baas" else http.get.call_args
    assert call.kwargs["timeout"] == 150
    assert deadlines == [150]
    transport.invoke.side_effect = TimeoutError("secret-value")
    http.get.side_effect = TimeoutError("secret-value")
    result = await runtime.query(binding, "replica", query, "owner")
    assert result["status"] == "timeout"
    assert result["file_count"] is None
    assert "secret-value" not in caplog.text
    assert "backend.file_count.engine_response" in caplog.text
    assert "backend.file_count.engine_failure" in caplog.text
    records = [r for r in caplog.records if "backend.file_count.engine_" in r.msg]
    assert all((r.args if isinstance(r.args, dict) else r.args[-1])["timeout_seconds"] == 150
               for r in records)


@pytest.mark.asyncio
@pytest.mark.parametrize("devices", [[{}], [{"uuid": "a"}, {"uuid": "a"}], None, {},
                                     [123], [{"uuid": True}], [{"uuid": 123}], [{"uuid": []}],
                                     [{"device_uuid": False, "uuid": "a"}]])
async def test_invalid_snapshot_rejected(devices):
    from agentclaw.community.kernel.file_count import FileCountBinding, FileCountError
    from agentclaw.community.plugins.community.file_count_runtime import HttpFileCountRuntime
    runtime = HttpFileCountRuntime(Mock(list_devices_by_bot_uuid=Mock(return_value=devices)), None, None, None)
    with pytest.raises(FileCountError, match="invalid_device_snapshot"):
        await runtime.targets(FileCountBinding(1, "baas", "device"))


def test_recursive_log_redaction():
    from agentclaw.community.kernel.file_count import safe_log_fields
    value = {"path": "/workspace", "nested": [{"Authorization": "secret-value", "apiKey": "secret-value",
             "Cookie": "secret-value", "credential": {"value": "secret-value"}, "sessionId": "secret-value"}]}
    result = safe_log_fields(value)
    assert result["path"] == "/workspace"
    assert "secret-value" not in str(result)
    assert value["nested"][0]["Authorization"] == "secret-value"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["baas", "arca"])
async def test_old_engine_route_is_unsupported(provider):
    import httpx
    from agentclaw.community.kernel.file_count import FileCountBinding, FileCountQuery
    from agentclaw.community.plugins.community.file_count_runtime import HttpFileCountRuntime
    from agentclaw.community.plugin_api.device_adapter_transport import DeviceAdapterEndpointNotFoundError
    runtime = HttpFileCountRuntime(None, Mock(resolve_for_binding_invoke=Mock(return_value=SimpleNamespace(
        conn_info={"url": "http://engine"}))),
        SimpleNamespace(invoke=AsyncMock(side_effect=DeviceAdapterEndpointNotFoundError("not-json secret-value"))),
        Mock(get=Mock(return_value=httpx.Response(404, json={"detail": "Not Found"}))))
    binding = FileCountBinding(1, provider, "device")
    result = await runtime.query(binding, "replica", FileCountQuery("bot", "owner", "draft", ".", "r"), "owner")
    assert result["error_code"] == "unsupported"
    assert result["file_count"] is None
    if provider == "arca":
        assert await runtime.targets(binding) == ["device"]
