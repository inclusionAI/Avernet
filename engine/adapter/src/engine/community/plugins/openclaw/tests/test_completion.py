"""Native completion contract: live routing, no secret export or chat side effects."""

import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from engine.community.core.adapters.openclaw.models import OpenClawModelsAdapter
from engine.community.core.models.completion import (
    CompletionRequest,
    ModelCompletionService,
)
from engine.community.plugin_api.openclaw.completion import OpenClawCompletionPort
from engine.community.plugins.openclaw._completion import _CompletionPortMixin


def config():
    return {
        "agents": {"defaults": {"model": {"primary": "fixture/first"}}},
        "models": {
            "providers": {
                "fixture": {
                    "api": "openai-completions",
                    "baseUrl": "https://model.example/v1",
                    "apiKey": "fixture-secret",
                    "models": [{"id": "first"}, {"id": "second"}],
                }
            }
        },
    }


@pytest.fixture
def native(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    data = config()
    path.write_text(json.dumps(data))
    calls = []
    response = {
        "choices": [{"message": {"content": "answer"}}],
        "usage": {"total_tokens": 12},
    }

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=response)

    original = httpx.AsyncClient

    def client(**options):
        assert options["trust_env"] is False and options["follow_redirects"] is False
        return original(**options, transport=httpx.MockTransport(handle))

    monkeypatch.setattr(httpx, "AsyncClient", client)

    class Native(_CompletionPortMixin):
        async def get_model_config(self):
            parsed = json.loads(path.read_text())
            parsed["models"]["providers"]["fixture"]["apiKey"] = "__OPENCLAW_REDACTED__"
            return {"exists": True, "valid": True, "path": str(path), "parsed": parsed}

    return SimpleNamespace(
        port=Native(), path=path, data=data, calls=calls, response=response
    )


@pytest.mark.asyncio
async def test_native_and_adapter_contract_live_default_rotation_and_override(native):
    assert isinstance(native.port, OpenClawCompletionPort)
    adapter = OpenClawModelsAdapter(native.port)
    assert isinstance(adapter, ModelCompletionService)
    request = CompletionRequest(messages=[{"role": "user", "content": "probe"}])
    result = await adapter.complete_model(request)
    assert result.model_dump() == {
        "text": "answer",
        "model": "fixture/first",
        "tokens": 12,
    }
    native.data["agents"]["defaults"]["model"]["primary"] = "fixture/second"
    native.data["models"]["providers"]["fixture"]["apiKey"] = "rotated-secret"
    native.path.write_text(json.dumps(native.data))
    assert (await adapter.complete_model(request)).model == "fixture/second"
    assert native.calls[-1].headers["authorization"] == "Bearer rotated-secret"
    result = await adapter.complete_model(
        request.model_copy(update={"model": "fixture/first"})
    )
    assert result.model == "fixture/first"
    assert len(native.calls) == 3
    assert set(json.loads(native.calls[0].content)) == {
        "model",
        "messages",
        "max_tokens",
        "stream",
    }
    assert "secret" not in result.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("api", "anthropic-messages"),
        ("auth", "oauth"),
        ("authHeader", False),
        ("headers", {"X-Key": "fixture-secret"}),
        ("apiKey", "${SOME_KEY}"),
        ("apiKey", {"source": "env", "id": "KEY"}),
        ("baseUrl", "http://model.example"),
        ("baseUrl", "https://user:secret@model.example"),
        ("baseUrl", "https://model.example?key=secret"),
    ],
)
async def test_unsupported_never_calls_or_falls_back(native, field, value):
    native.data["models"]["providers"]["fixture"][field] = value
    native.path.write_text(json.dumps(native.data))
    with pytest.raises(NotImplementedError, match="not supported") as exc:
        await native.port.complete_model(
            CompletionRequest(messages=[{"role": "user", "content": "probe"}])
        )
    assert "secret" not in str(exc.value) and not native.calls


@pytest.mark.asyncio
async def test_unconfigured_override_refused(native):
    with pytest.raises(NotImplementedError):
        await native.port.complete_model(
            CompletionRequest(
                messages=[{"role": "user", "content": "probe"}], model="fixture/missing"
            )
        )
    assert not native.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("usage", [None, {"total_tokens": True}, {"total_tokens": 0}])
async def test_invalid_usage_is_sanitized(native, usage):
    native.response["usage"] = usage
    with pytest.raises(RuntimeError, match="completion unavailable"):
        await native.port.complete_model(
            CompletionRequest(messages=[{"role": "user", "content": "probe"}])
        )


@pytest.mark.asyncio
async def test_gateway_route_and_raw_file_must_agree(native):
    parsed = copy.deepcopy(native.data)
    parsed["models"]["providers"]["fixture"]["baseUrl"] = "https://other.example/v1"
    native.port.get_model_config = AsyncMock(
        return_value={
            "exists": True,
            "valid": True,
            "path": str(native.path),
            "parsed": parsed,
        }
    )
    with pytest.raises(NotImplementedError):
        await native.port.complete_model(
            CompletionRequest(messages=[{"role": "user", "content": "probe"}])
        )
    assert not native.calls


@pytest.mark.parametrize(
    "raw", ['{"x":1,"x":2}', '{"nested":{"$include":"secret"}}', "[1]"]
)
def test_ambiguous_or_included_config_refused(tmp_path, raw):
    path = tmp_path / "config.json"
    path.write_text(raw)
    with pytest.raises(NotImplementedError):
        _CompletionPortMixin._read_config(str(path))


@pytest.mark.asyncio
async def test_optional_native_port_not_implemented_is_explicit():
    with pytest.raises(NotImplementedError):
        await OpenClawModelsAdapter(object()).complete_model(
            CompletionRequest(messages=[{"role": "user", "content": "probe"}])
        )
