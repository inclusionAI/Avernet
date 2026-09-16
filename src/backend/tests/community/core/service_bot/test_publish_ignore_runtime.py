"""Fixed-provider transport, asymmetric authorization and failure evidence."""

import base64
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PrivateFormat,
    NoEncryption,
)
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
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode()
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
    runtime = HttpPublishIgnoreRuntime(baas, resolver, transport, http, pem)
    binding = NS(id=44, device_provider="baas", device_id="baas-bot")
    command = PublishIgnoreCommand(
        "bot", "entity", "online", "add", "workspace/cache", "parent"
    )
    return runtime, binding, command, key


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["baas", "arca"])
async def test_provider_and_signature(setup, provider):
    runtime, binding, command, key = setup
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
    auth = body.pop("authorization")
    encoded = json.dumps(
        {**body, "timestamp": auth["timestamp"]},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    key.public_key().verify(base64.b64decode(auth["signature"]), encoded)
    assert body["request_id"] != "parent"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure,status",
    [
        ("timeout", "unknown"),
        ("error", "failed"),
        ("rejected", "failed"),
        ("invalid", "failed"),
        ("missing_key", "failed"),
    ],
)
async def test_failures_are_safe(setup, failure, status, caplog):
    runtime, binding, command, _ = setup
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
    if failure == "missing_key":
        runtime.secret = ""
    with caplog.at_level("INFO"):
        result = await runtime.change(binding, "a", command, "operator")
    assert result["status"] == status
    assert "test-token" not in str(result)
    assert "test-token" not in caplog.text


@pytest.mark.asyncio
async def test_bad_snapshot(setup):
    runtime, binding, _, _ = setup
    runtime.baas.list_devices_by_bot_uuid.return_value = [{"uuid": "a"}, {"uuid": "a"}]
    with pytest.raises(PublishIgnoreError):
        await runtime.targets(binding)


@pytest.mark.asyncio
async def test_boundary_logs_and_invalid_response_fields(setup, monkeypatch):
    from agentclaw.community.plugins.community import publish_ignore_runtime as module

    runtime, binding, command, _ = setup
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
