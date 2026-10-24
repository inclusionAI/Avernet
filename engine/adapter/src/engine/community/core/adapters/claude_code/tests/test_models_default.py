"""Unit tests for ``ClaudeCodeModelsAdapter.get_default_model``.

Kept out of ``test_adapters.py`` (already past the 1,000-line limit). Drives
the adapter against a fake ``ClaudeCodeModelsPort`` returning the relay's raw
``models.default`` payload and checks catalogue matching + provenance mapping.
"""
from __future__ import annotations

from engine.community.core.adapters.claude_code.models import ClaudeCodeModelsAdapter
from engine.community.core.engine.context import AuthContext


def _auth(token: str) -> AuthContext:
    return AuthContext(token=token)


class _FakeModelsPort:
    catalogue: list[dict] = [
        {"id": "m1", "provider": "anthropic", "name": "Claude",
         "display_name": "Claude 3", "enabled": True, "default": True,
         "capabilities": {"context_window": 200000, "vision": True}},
    ]
    default_raw: dict | None = {
        "model": "m1", "source": "settings.json",
        "path": "/home/admin/.claude/settings.json",
    }

    def __init__(self) -> None:
        self.default_token: str | None = "unset"

    async def models_list(self, token=None) -> list[dict]:
        return list(self.catalogue)

    async def models_list_providers(self, token=None) -> list[dict]:
        return []

    async def models_default(self, token=None) -> dict | None:
        self.default_token = token
        return self.default_raw


async def test_default_model_matches_catalogue_entry():
    port = _FakeModelsPort()
    out = await ClaudeCodeModelsAdapter(port).get_default_model(auth=_auth("t"))
    assert out is not None
    assert out.model.id == "m1"
    assert out.model.provider == "anthropic"
    assert out.model.display_name == "Claude 3"      # from the catalogue
    assert out.model.capabilities.vision is True
    assert out.source == "settings.json"
    assert out.source_detail == "/home/admin/.claude/settings.json#env.ANTHROPIC_MODEL"
    assert out.fallbacks == []
    assert port.default_token == "t"                 # token forwarded to the port


async def test_default_model_synthesises_entry_when_not_in_catalogue():
    port = _FakeModelsPort()
    port.default_raw = {"model": " GLM-5.1 ", "source": "env"}
    out = await ClaudeCodeModelsAdapter(port).get_default_model()
    assert out is not None
    assert out.model.id == "GLM-5.1"
    assert out.model.provider == ""
    assert out.model.display_name == "GLM-5.1"
    assert out.source == "env"
    assert out.source_detail == "RELAY_DEFAULT_MODEL"
    assert port.default_token is None


async def test_default_model_bare_id_matches_prefixed_catalogue_entry():
    port = _FakeModelsPort()
    port.catalogue = [{"id": "anthropic/claude-sonnet-4-5", "provider": "anthropic",
                       "display_name": "Sonnet 4.5"}]
    port.default_raw = {"model": "claude-sonnet-4-5", "source": "fallback"}
    out = await ClaudeCodeModelsAdapter(port).get_default_model()
    assert out is not None
    assert out.model.id == "anthropic/claude-sonnet-4-5"
    assert out.model.display_name == "Sonnet 4.5"
    assert out.source_detail == "relay built-in fallback"


async def test_default_model_prefixed_id_keeps_provider_when_unknown():
    port = _FakeModelsPort()
    port.default_raw = {"model": "glink/claude-opus-4-6", "source": "model-provider-settings-source",
                        "path": "/opt/claude-settings.json"}
    out = await ClaudeCodeModelsAdapter(port).get_default_model()
    assert out.model.id == "glink/claude-opus-4-6"
    assert out.model.provider == "glink"
    assert out.source_detail == "/opt/claude-settings.json#env.ANTHROPIC_MODEL"


async def test_default_model_none_when_relay_lacks_method():
    port = _FakeModelsPort()
    port.default_raw = None
    assert await ClaudeCodeModelsAdapter(port).get_default_model() is None
