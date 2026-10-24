"""Port-impl tests for OpenClawPluginImpl.models_list / providers_list / default_model (transport).

Preserves the legacy engines/openclaw/tests/test_models.py transport coverage:
the models.list -> providers.list fallback (incl. the convert-empty case), the
provider flattening, and raw-dict returns. The dict->Model/Provider DTO build is
covered by core/adapters/openclaw/tests/test_models.py.
"""
from __future__ import annotations

import json
from typing import Any

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


async def test_default_model_reads_object_form_from_runtime_config():
    impl, client = _impl({"config.get": _config_get({
        "path": "/home/admin/.openclaw/openclaw.json",
        "config": {"agents": {"defaults": {"model": {
            "primary": "openai-compatible/glm-5.2",
            "fallbacks": ["openai/gpt-4", "  ", 42],
        }}}},
    })})
    out = await impl.default_model()
    assert out == {
        "model": "openai-compatible/glm-5.2",
        "fallbacks": ["openai/gpt-4"],
        "source": "openclaw.config.get",
        "source_detail": "/home/admin/.openclaw/openclaw.json#agents.defaults.model",
    }
    assert client.calls == ["config.get"]


async def test_default_model_reads_plain_string_form_from_parsed():
    # Older gateways omit the runtime-shaped `config`; `parsed` still carries
    # the authored value, and the legacy plain-string form is accepted.
    impl, _ = _impl({"config.get": _config_get({
        "parsed": {"agents": {"defaults": {"model": " anthropic/claude-opus-4 "}}},
    })})
    out = await impl.default_model()
    assert out["model"] == "anthropic/claude-opus-4"
    assert out["fallbacks"] == []
    assert out["source_detail"] == "agents.defaults.model"


async def test_default_model_prefers_runtime_config_over_parsed():
    impl, _ = _impl({"config.get": _config_get({
        "parsed": {"agents": {"defaults": {"model": "${DEFAULT_MODEL}"}}},
        "config": {"agents": {"defaults": {"model": "bailian/qwen3.8-flash"}}},
    })})
    assert (await impl.default_model())["model"] == "bailian/qwen3.8-flash"


async def test_default_model_none_when_gateway_config_has_no_model(tmp_path, monkeypatch):
    # The gateway answered: its config is authoritative, so an unset
    # agents.defaults.model is "no default" — the file is NOT consulted.
    cfg = tmp_path / "openclaw.json"
    cfg.write_text(json.dumps({"agents": {"defaults": {"model": "x/y"}}}))
    monkeypatch.setenv("OPENCLAW_CONFIG_PATH", str(cfg))
    impl, _ = _impl({"config.get": _config_get({
        "config": {"agents": {"defaults": {"workspace": "/w"}}},
    })})
    assert await impl.default_model() is None


async def test_default_model_falls_back_to_file_when_rpc_errors(tmp_path, monkeypatch):
    cfg = tmp_path / "openclaw.json"
    cfg.write_text(json.dumps({"agents": {"defaults": {"model": {
        "primary": "bailian/qwen3.8-flash", "fallbacks": ["openai/gpt-4"],
    }}}}))
    monkeypatch.setenv("OPENCLAW_CONFIG_PATH", str(cfg))
    impl, client = _impl({"config.get": ResponseFrame(
        id="c", ok=False, error=ErrorShape(code="UNKNOWN_METHOD", message="nope"),
    )})
    out = await impl.default_model()
    assert out == {
        "model": "bailian/qwen3.8-flash",
        "fallbacks": ["openai/gpt-4"],
        "source": "openclaw.json",
        "source_detail": f"{cfg}#agents.defaults.model",
    }


async def test_default_model_falls_back_to_file_when_rpc_raises(tmp_path, monkeypatch):
    # _FakeClient raises on an RPC it has no canned response for.
    cfg = tmp_path / "openclaw.json"
    cfg.write_text(json.dumps({"agents": {"defaults": {"model": "a/b"}}}))
    monkeypatch.setenv("OPENCLAW_CONFIG_PATH", str(cfg))
    impl, _ = _impl({})
    assert (await impl.default_model())["model"] == "a/b"


async def test_default_model_file_path_from_state_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENCLAW_CONFIG_PATH", raising=False)
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(tmp_path))
    (tmp_path / "openclaw.json").write_text(json.dumps({"agents": {"defaults": {"model": "p/m"}}}))
    impl, _ = _impl({})
    out = await impl.default_model()
    assert out["model"] == "p/m"
    assert out["source_detail"].startswith(str(tmp_path))


async def test_default_model_none_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_CONFIG_PATH", str(tmp_path / "absent.json"))
    impl, _ = _impl({})
    assert await impl.default_model() is None


async def test_default_model_none_when_file_invalid_json(tmp_path, monkeypatch):
    cfg = tmp_path / "openclaw.json"
    cfg.write_text("{not json")
    monkeypatch.setenv("OPENCLAW_CONFIG_PATH", str(cfg))
    impl, _ = _impl({})
    assert await impl.default_model() is None


async def test_default_model_none_when_file_has_malformed_model(tmp_path, monkeypatch):
    cfg = tmp_path / "openclaw.json"
    cfg.write_text(json.dumps({"agents": {"defaults": {"model": {"fallbacks": ["x/y"]}}}}))
    monkeypatch.setenv("OPENCLAW_CONFIG_PATH", str(cfg))
    impl, _ = _impl({})
    assert await impl.default_model() is None
