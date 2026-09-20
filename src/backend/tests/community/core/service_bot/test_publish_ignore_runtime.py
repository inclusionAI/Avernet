"""Fixed-provider authenticated transport and failure evidence."""

from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
import pytest
from agentclaw.community.api.publish_ignore_service import (
    PublishIgnoreCommand,
    PublishIgnoreError,
)
from agentclaw.community.plugins.community.publish_ignore_runtime import (
    HttpPublishIgnoreRuntime,
    ENDPOINT,
)


@pytest.fixture
def setup():
    response = {
        "success": True,
        "data": {"changed": True, "entry_count": 1, "revision": "a" * 64},
    }
    baas = Mock(
        list_devices_by_bot_uuid=Mock(return_value=[{"uuid": "a"}, {"uuid": "b"}])
    )
    resolver = Mock(
        resolve_for_binding_invoke=Mock(
            return_value=NS(
                conn_info={
                    "binding_id": 44, "device_uuid": "a", "device_affinity": "collaborator",
                    "url": "https://runtime.example/proxypass/target",
                    "headers": {"x-proxypass-token": "test-token"},
                }
            )
        )
    )
    transport = NS(invoke=AsyncMock(return_value=response))
    http = Mock(post=Mock(return_value=Mock(json=Mock(return_value=response))))
    runtime = HttpPublishIgnoreRuntime(baas, resolver, transport, http)
    binding = NS(id=44, device_provider="baas", device_id="baas-bot")
    command = PublishIgnoreCommand(
        "bot", "entity", "online", "add", "workspace/cache", "parent"
    )
    return runtime, binding, command


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["baas", "arca"])
async def test_provider_connection_without_signature(setup, provider):
    runtime, binding, command = setup
    binding.device_provider = provider
    targets = await runtime.targets(binding)
    result = await runtime.change(binding, targets[0], command, "collaborator")
    assert result["status"] == "changed"
    if provider == "arca":
        runtime.baas.list_devices_by_bot_uuid.assert_not_called()
        runtime.transport.invoke.assert_not_called()
        call = runtime.http.post.call_args
        assert call.args[0].endswith(ENDPOINT)
        assert call.kwargs["headers"] == {"x-proxypass-token": "test-token"}
        body = call.kwargs["json"]
    else:
        call = runtime.transport.invoke.call_args
        assert call.args[0]["device_uuid"] == "a"
        assert call.args[0]["binding_id"] == 44
        body = call.kwargs["body"]
    runtime.resolver.resolve_for_binding_invoke.assert_called_once_with(
        44, "collaborator", bot_id="bot", device_uuid="a" if provider == "baas" else None,
    )
    assert body["expected_target"] == {"bot_id": "bot", "entity_id": "entity", "stage": "online"}
    assert "authorization" not in body
    assert body["request_id"] != "parent"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure,status",
    [
        ("timeout", "unknown"),
        ("error", "failed"),
        ("rejected", "failed"),
        ("invalid", "failed"),
    ],
)
async def test_failures_are_safe(setup, failure, status, caplog):
    runtime, binding, command = setup
    if failure == "timeout":
        runtime.transport.invoke.side_effect = TimeoutError("test-token")
    if failure == "error":
        runtime.transport.invoke.side_effect = ValueError("test-token")
    if failure == "rejected":
        runtime.transport.invoke.return_value = {
            "success": False,
            "token": "test-token",
        }
    if failure == "invalid":
        runtime.transport.invoke.return_value = {"success": True, "data": {}}
    with caplog.at_level("INFO"):
        result = await runtime.change(binding, "a", command, "operator")
    assert result["status"] == status
    assert "test-token" not in str(result)
    assert "test-token" not in caplog.text


@pytest.mark.asyncio
async def test_bad_snapshot(setup):
    runtime, binding, _ = setup
    runtime.baas.list_devices_by_bot_uuid.return_value = [{"uuid": "a"}, {"uuid": "a"}]
    with pytest.raises(PublishIgnoreError):
        await runtime.targets(binding)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["baas", "arca"])
async def test_query_uses_get_with_pinned_target_and_safe_logs(setup, provider, caplog):
    from agentclaw.community.kernel.publish_ignore import PublishIgnoreQuery

    runtime, binding, _ = setup
    binding.device_provider = provider
    response = {"success": True, "data": {
        "paths": ["workspace/bin", "cache"], "entry_count": 2, "revision": "a" * 64,
    }}
    runtime.transport.invoke.return_value = response
    runtime.http.get.return_value = Mock(json=Mock(return_value=response))
    query = PublishIgnoreQuery("bot", "entity", "draft", "parent")
    with caplog.at_level("INFO"):
        result = await runtime.query(binding, "a", query, "actor")
    assert result["status"] == "success" and result["paths"] == ["workspace/bin", "cache"]
    call = runtime.transport.invoke.call_args if provider == "baas" else runtime.http.get.call_args
    assert call.kwargs["params"]["stage"] == "draft"
    assert call.kwargs["params"]["request_id"] != "parent"
    assert "body" not in call.kwargs and "json" not in call.kwargs
    if provider == "baas":
        assert call.args[1] == "GET"
    assert "test-token" not in caplog.text
    assert "backend.publish_ignore.engine_query_response" in caplog.text
    events = [record for record in caplog.records if "backend.publish_ignore.engine_query_" in record.msg]
    assert len(events) == 2
    for record in events:
        fields = record.args
        assert fields["method"] == "GET" and fields["route"] == "/api/bot/publish-ignore"
        assert fields["bot_id"] == "bot" and fields["entity_id"] == "entity"
        assert fields["stage"] == "draft" and fields["operator_id"] == "actor"
        assert fields["request_id"] == "parent" and fields["engine_request_id"] == call.kwargs["params"]["request_id"]
        assert fields["provider"] == provider and fields["target_id"] == "a"
        assert fields["elapsed_ms"] >= 0
    assert events[-1].args["paths"] == result["paths"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    {}, {"paths": []}, {"paths": "cache", "entry_count": 1, "revision": "a" * 64},
    {"paths": [1], "entry_count": 1, "revision": "a" * 64},
    {"paths": [], "entry_count": True, "revision": "a" * 64},
    {"paths": [], "entry_count": 1, "revision": "a" * 64},
    {"paths": [], "entry_count": 0, "revision": "secret-value"},
])
async def test_query_rejects_invalid_snapshot(setup, bad, caplog):
    from agentclaw.community.kernel.publish_ignore import PublishIgnoreQuery

    runtime, binding, _ = setup
    runtime.transport.invoke.return_value = {"success": True, "data": bad}
    result = await runtime.query(binding, "a", PublishIgnoreQuery("bot", "entity", "online", "q"), "actor")
    assert result["status"] == "failed" and "paths" not in result
    assert "secret-value" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["baas", "arca"])
@pytest.mark.parametrize("failure,status", [("timeout", "unknown"), ("error", "failed"),
                                          ("rejected", "failed")])
async def test_query_upstream_failure_has_no_fake_empty_paths(setup, provider, failure, status, caplog):
    from agentclaw.community.kernel.publish_ignore import PublishIgnoreQuery
    runtime, binding, _ = setup
    binding.device_provider = provider
    if failure == "rejected":
        runtime.transport.invoke.return_value = {"success": False, "token": "test-token"}
        runtime.http.get.return_value = Mock(json=Mock(return_value={"success": False}))
    else:
        error = TimeoutError("test-token") if failure == "timeout" else ValueError("test-token")
        runtime.transport.invoke.side_effect = error
        runtime.http.get.side_effect = error
    result = await runtime.query(binding, "a", PublishIgnoreQuery("bot", "entity", "online", "q"), "actor")
    assert result["status"] == status and "paths" not in result
    assert "test-token" not in caplog.text + str(result)
    event = next(record for record in caplog.records if "backend.publish_ignore.engine_query_failure" in record.msg)
    assert event.args["request_id"] == "q" and event.args["provider"] == provider
    assert event.args["status"] == status and event.args["elapsed_ms"] >= 0
    assert event.args["error_code"] == result["error_code"]
    assert event.args["error_type"] == result["error_type"]


@pytest.mark.asyncio
async def test_query_large_paths_are_returned_but_summarized_in_logs(setup, caplog):
    from agentclaw.community.kernel.publish_ignore import PublishIgnoreQuery
    runtime, binding, _ = setup
    paths = ["large-rule-" + "x" * 4096]
    runtime.transport.invoke.return_value = {
        "success": True, "data": {"paths": paths, "entry_count": 1, "revision": "a" * 64},
    }
    with caplog.at_level("INFO"):
        result = await runtime.query(binding, "a", PublishIgnoreQuery("bot", "entity", "draft", "q"), "actor")
    assert result["paths"] == paths
    assert paths[0] not in caplog.text and "paths_omitted" in caplog.text


@pytest.mark.asyncio
async def test_boundary_logs_and_invalid_response_fields(setup, monkeypatch):
    from agentclaw.community.plugins.community import publish_ignore_runtime as module

    runtime, binding, command = setup
    audit = Mock()
    monkeypatch.setattr(module, "logger", audit)
    await runtime.change(binding, "a", command, "operator")
    assert (
        audit.info.call_args_list[0].args[0]
        == "backend.publish_ignore.engine_request %s"
    )
    response = audit.info.call_args_list[1].args[1]
    assert response["operator_id"] == "operator" and response["stage"] == "online"
    assert response["status"] == "changed" and "elapsed_ms" in response
    runtime.transport.invoke.return_value["data"]["revision"] = "private-test-value"
    result = await runtime.change(binding, "a", command, "operator")
    assert result["status"] == "failed"
    assert audit.warning.call_args.args[0] == "backend.publish_ignore.engine_failure %s"
    assert "private-test-value" not in str(audit.mock_calls)
    runtime.transport.invoke.return_value["data"].update(
        revision="a" * 64, entry_count=-1
    )
    assert (await runtime.change(binding, "a", command, "operator"))[
        "status"
    ] == "failed"
