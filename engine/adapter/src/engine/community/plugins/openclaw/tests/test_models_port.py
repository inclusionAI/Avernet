"""Port-impl tests for OpenClawPluginImpl.models_list / providers_list / default_model (transport).

Preserves the legacy engines/openclaw/tests/test_models.py transport coverage:
the models.list -> providers.list fallback (incl. the convert-empty case), the
provider flattening, and raw-dict returns. The dict->Model/Provider DTO build is
covered by core/adapters/openclaw/tests/test_models.py.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from engine.community.core.models.exceptions import DefaultModelNotConfiguredError
from engine.community.kernel.frames import ErrorShape, ResponseFrame
from engine.community.plugins.skills_pool.center_content import MountedCenterContentAdapter
from engine.community.plugins.openclaw.plugin_impl import OpenClawPluginImpl


class _FakeClient:
    """Returns canned ResponseFrames keyed by RPC method."""

    def __init__(self, responses: dict[str, ResponseFrame]):
        self.connected = True
        self._responses = responses
        self.calls: list[str] = []

    async def send_request(self, method: str, params: dict | None = None, timeout: Any = None):
        self.calls.append(method)
        if method not in self._responses:
            raise RuntimeError(f"unexpected RPC: {method}")
        return self._responses[method]


def _impl(responses) -> tuple[OpenClawPluginImpl, _FakeClient]:
    client = _FakeClient(responses)
    return OpenClawPluginImpl(center_content_adapter=MountedCenterContentAdapter(), client=client), client


async def test_models_list_returns_models_when_present():
    models = [{"id": "gpt-4"}, {"id": "claude"}]
    impl, client = _impl({"models.list": ResponseFrame(id="1", ok=True, payload={"models": models})})
    out = await impl.models_list()
    assert out == models
    assert "providers.list" not in client.calls  # no fallback when usable


async def test_models_list_falls_back_to_providers_when_empty():
    impl, client = _impl({
        "models.list": ResponseFrame(id="1", ok=True, payload={"models": []}),
        "providers.list": ResponseFrame(id="2", ok=True, payload={"providers": [
            {"id": "openai", "models": [{"id": "gpt-4"}]},
        ]}),
    })
    out = await impl.models_list()
    assert "providers.list" in client.calls
    assert any(e.get("id") == "gpt-4" and e.get("provider") == "openai" for e in out)


async def test_models_list_falls_back_when_entries_unconvertible():
    # Non-empty models.list but no entry has id/model -> must still fall back.
    impl, client = _impl({
        "models.list": ResponseFrame(id="1", ok=True, payload={"models": [{"desc": "x"}, {"meta": 1}]}),
        "providers.list": ResponseFrame(id="2", ok=True, payload={"providers": [
            {"id": "anthropic", "models": [{"id": "claude"}]},
        ]}),
    })
    out = await impl.models_list()
    assert "providers.list" in client.calls
    assert any(e.get("id") == "claude" for e in out)


async def test_providers_list_returns_raw_provider_dicts():
    providers = [{"id": "openai", "models": [{"id": "gpt-4"}]}]
    impl, _ = _impl({"providers.list": ResponseFrame(id="1", ok=True, payload={"providers": providers})})
    assert await impl.providers_list() == providers


async def test_providers_list_empty_on_not_ok():
    from engine.community.kernel.frames import ErrorShape
    impl, _ = _impl({"providers.list": ResponseFrame(id="1", ok=False, error=ErrorShape(code="X", message="e"))})
    assert await impl.providers_list() == []


# ── default_model ──────────────────────────────────────────────────────────────


def _config_get(payload: dict) -> ResponseFrame:
    return ResponseFrame(id="c", ok=True, payload=payload)


def _home_with_config(tmp_path, monkeypatch, config: Any) -> Any:
    """Point ``Path.home()`` at tmp_path and write ~/.openclaw/openclaw.json."""
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg_dir = tmp_path / ".openclaw"
    cfg_dir.mkdir()
    cfg = cfg_dir / "openclaw.json"
    cfg.write_text(config if isinstance(config, str) else json.dumps(config))
    return cfg


async def test_default_model_reads_object_form_from_runtime_config():
    impl, client = _impl({"config.get": _config_get({
        "path": "/home/admin/.openclaw/openclaw.json",
        "config": {"agents": {"defaults": {"model": {
            "primary": "openai-compatible/glm-5.2",
            "fallbacks": ["openai/gpt-4", "  ", 42],
        }}}},
    })})
    out = await impl.default_model()
    assert out == {"model": "openai-compatible/glm-5.2", "fallbacks": ["openai/gpt-4"]}
    assert client.calls == ["config.get"]


async def test_default_model_reads_plain_string_form_from_parsed():
    # Older gateways omit the runtime-shaped `config`; `parsed` still carries
    # the authored value, and the legacy plain-string form is accepted.
    impl, _ = _impl({"config.get": _config_get({
        "parsed": {"agents": {"defaults": {"model": " anthropic/claude-opus-4 "}}},
    })})
    assert await impl.default_model() == {"model": "anthropic/claude-opus-4", "fallbacks": []}


async def test_default_model_prefers_runtime_config_over_parsed():
    impl, _ = _impl({"config.get": _config_get({
        "parsed": {"agents": {"defaults": {"model": "${DEFAULT_MODEL}"}}},
        "config": {"agents": {"defaults": {"model": "bailian/qwen3.8-flash"}}},
    })})
    assert (await impl.default_model())["model"] == "bailian/qwen3.8-flash"


async def test_default_model_raises_when_gateway_config_has_no_model(tmp_path, monkeypatch):
    # The gateway answered: its config is authoritative, so an unset
    # agents.defaults.model is "not configured" — the file is NOT consulted.
    _home_with_config(tmp_path, monkeypatch, {"agents": {"defaults": {"model": "x/y"}}})
    impl, _ = _impl({"config.get": _config_get({
        "path": "/home/admin/.openclaw/openclaw.json",
        "config": {"agents": {"defaults": {"workspace": "/w"}}},
    })})
    with pytest.raises(DefaultModelNotConfiguredError, match="agents.defaults.model is not set"):
        await impl.default_model()


async def test_default_model_falls_back_to_home_file_when_rpc_errors(tmp_path, monkeypatch):
    _home_with_config(tmp_path, monkeypatch, {"agents": {"defaults": {"model": {
        "primary": "bailian/qwen3.8-flash", "fallbacks": ["openai/gpt-4"],
    }}}})
    impl, _ = _impl({"config.get": ResponseFrame(
        id="c", ok=False, error=ErrorShape(code="UNKNOWN_METHOD", message="nope"),
    )})
    assert await impl.default_model() == {
        "model": "bailian/qwen3.8-flash", "fallbacks": ["openai/gpt-4"],
    }


async def test_default_model_falls_back_to_home_file_when_rpc_raises(tmp_path, monkeypatch):
    # _FakeClient raises on an RPC it has no canned response for.
    _home_with_config(tmp_path, monkeypatch, {"agents": {"defaults": {"model": "a/b"}}})
    impl, _ = _impl({})
    assert (await impl.default_model())["model"] == "a/b"


async def test_default_model_raises_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    impl, _ = _impl({})
    with pytest.raises(DefaultModelNotConfiguredError, match="does not exist"):
        await impl.default_model()


async def test_default_model_raises_when_file_invalid_json(tmp_path, monkeypatch):
    _home_with_config(tmp_path, monkeypatch, "{not json")
    impl, _ = _impl({})
    with pytest.raises(DefaultModelNotConfiguredError, match="unreadable"):
        await impl.default_model()


async def test_default_model_raises_when_file_has_malformed_model(tmp_path, monkeypatch):
    _home_with_config(tmp_path, monkeypatch, {"agents": {"defaults": {"model": {"fallbacks": ["x/y"]}}}})
    impl, _ = _impl({})
    with pytest.raises(DefaultModelNotConfiguredError, match="is not set in"):
        await impl.default_model()
