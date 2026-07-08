"""Unit tests for the OpenClaw models ACL adapter.

Drives `OpenClawModelsAdapter` against a fake `OpenClawModelsPort` (plain object
returning canned raw dicts) — the adapter builds `Model` / `Provider` DTOs from
whatever the port returns.  The fallback logic lives in the port; these tests
verify only DTO construction and edge-case handling.
"""
from __future__ import annotations

from typing import Any

import pytest

from engine.community.core.adapters.openclaw.models import (
    OpenClawModelsAdapter,
    _capabilities_from_payload,
)


class _FakeModelsPort:
    def __init__(
        self,
        models_raw: list[dict[str, Any]] | None = None,
        providers_raw: list[dict[str, Any]] | None = None,
    ) -> None:
        self._models_raw = models_raw or []
        self._providers_raw = providers_raw or []
        self.models_list_calls = 0
        self.providers_list_calls = 0

    async def models_list(self) -> list[dict[str, Any]]:
        self.models_list_calls += 1
        return self._models_raw

    async def providers_list(self) -> list[dict[str, Any]]:
        self.providers_list_calls += 1
        return self._providers_raw


# ── _capabilities_from_payload (top-level shape; guards fb0ed2ee3 regression) ──


def test_caps_vision_true_when_input_contains_image():
    assert _capabilities_from_payload({"input": ["text", "image"]}).vision is True


def test_caps_vision_false_when_input_is_text_only():
    assert _capabilities_from_payload({"input": ["text"]}).vision is False


def test_caps_vision_false_when_input_missing():
    assert _capabilities_from_payload({}).vision is False


def test_caps_vision_false_when_input_not_a_list():
    # A non-list `input` (e.g. a bare string) must not be membership-tested.
    assert _capabilities_from_payload({"input": "image"}).vision is False


def test_caps_reasoning_passthrough():
    assert _capabilities_from_payload({"reasoning": True}).reasoning is True
    assert _capabilities_from_payload({"reasoning": False}).reasoning is False
    assert _capabilities_from_payload({}).reasoning is False


def test_caps_context_window_from_camelcase():
    assert _capabilities_from_payload({"contextWindow": 200000}).context_window == 200000


def test_caps_context_window_falls_back_to_context_tokens():
    assert _capabilities_from_payload({"contextTokens": 64000}).context_window == 64000


def test_caps_context_window_default_when_missing():
    assert _capabilities_from_payload({}).context_window == 128000


def test_caps_max_output_tokens_from_camelcase():
    assert _capabilities_from_payload({"maxTokens": 8192}).max_output_tokens == 8192


def test_caps_max_output_tokens_default_when_missing_or_falsy():
    assert _capabilities_from_payload({}).max_output_tokens == 4096
    # falsy upstream value falls back too (parity with legacy `or 4096`)
    assert _capabilities_from_payload({"maxTokens": 0}).max_output_tokens == 4096


def test_caps_static_flags_kept_true():
    caps = _capabilities_from_payload({})
    assert caps.function_calling is True
    assert caps.streaming is True
    assert caps.json_mode is True


@pytest.mark.parametrize("bad", [None, "x", 42, [], ("a",)])
def test_caps_non_dict_returns_defaults(bad):
    caps = _capabilities_from_payload(bad)
    assert caps.vision is False
    assert caps.context_window == 128000


def test_caps_nested_capabilities_key_is_ignored():
    # Regression guard for fb0ed2ee3: the gateway never sends a nested
    # `capabilities` object; only top-level fields drive the result. A nested
    # `capabilities` (and any snake_case keys) must NOT influence the output.
    caps = _capabilities_from_payload({
        "capabilities": {"vision": True, "context_window": 999},
        "context_window": 999,
        "vision": True,
    })
    assert caps.vision is False
    assert caps.context_window == 128000


# ── list_models ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_models_builds_model_dtos():
    port = _FakeModelsPort(models_raw=[
        {
            "id": "gpt-4",
            "provider": "openai",
            "name": "GPT-4",
            "display_name": "GPT-4 Turbo",
            "description": "Flagship",
            # OpenClaw puts modality hints at the TOP LEVEL of the item — there
            # is no nested `capabilities` object (see _capabilities_from_payload).
            "input": ["text", "image"],
            "reasoning": True,
            "contextWindow": 200000,
            "maxTokens": 8192,
            "enabled": True,
            "default": True,
        }
    ])
    adapter = OpenClawModelsAdapter(port)
    models = await adapter.list_models()
    assert len(models) == 1
    m = models[0]
    assert m.id == "openai/gpt-4"
    assert m.provider == "openai"
    assert m.provider_id == "gpt-4"
    assert m.name == "GPT-4"
    assert m.display_name == "GPT-4 Turbo"
    assert m.capabilities.vision is True
    assert m.capabilities.reasoning is True
    assert m.capabilities.context_window == 200000
    assert m.capabilities.max_output_tokens == 8192
    assert m.enterprise_default is True


@pytest.mark.asyncio
async def test_list_models_normalizes_double_colon_id():
    port = _FakeModelsPort(models_raw=[{"id": "openai::gpt-4", "provider": "openai"}])
    adapter = OpenClawModelsAdapter(port)
    models = await adapter.list_models()
    assert models[0].id == "openai/gpt-4"


@pytest.mark.asyncio
async def test_list_models_normalizes_bare_model_id():
    port = _FakeModelsPort(models_raw=[{"id": "gpt-4", "provider": "openai"}])
    adapter = OpenClawModelsAdapter(port)
    models = await adapter.list_models()
    assert models[0].id == "openai/gpt-4"


@pytest.mark.asyncio
async def test_list_models_skips_empty_id_entry():
    port = _FakeModelsPort(models_raw=[
        {"id": "", "provider": "openai"},
        {"id": "claude-3", "provider": "anthropic"},
    ])
    adapter = OpenClawModelsAdapter(port)
    models = await adapter.list_models()
    assert len(models) == 1
    assert models[0].provider == "anthropic"


@pytest.mark.asyncio
async def test_list_models_returns_empty_on_empty_port():
    port = _FakeModelsPort(models_raw=[])
    adapter = OpenClawModelsAdapter(port)
    models = await adapter.list_models()
    assert models == []
    assert port.models_list_calls == 1


@pytest.mark.asyncio
async def test_list_models_default_capabilities_when_missing():
    port = _FakeModelsPort(models_raw=[{"id": "m1", "provider": "p1"}])
    adapter = OpenClawModelsAdapter(port)
    models = await adapter.list_models()
    caps = models[0].capabilities
    assert caps.context_window == 128000
    assert caps.streaming is True


# ── list_providers ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_providers_builds_provider_dtos_with_nested_models():
    port = _FakeModelsPort(providers_raw=[
        {
            "id": "openai",
            "name": "OpenAI",
            "enabled": True,
            "models": [
                {"id": "gpt-4", "name": "GPT-4"},
                {"id": "gpt-3.5", "name": "GPT-3.5"},
            ],
        }
    ])
    adapter = OpenClawModelsAdapter(port)
    providers = await adapter.list_providers()
    assert len(providers) == 1
    p = providers[0]
    assert p.id == "openai"
    assert p.name == "OpenAI"
    assert p.enabled is True
    assert len(p.models) == 2
    assert p.models[0].id == "openai/gpt-4"
    assert p.models[1].id == "openai/gpt-3.5"


@pytest.mark.asyncio
async def test_list_providers_derives_nested_model_capabilities():
    # The providers path must derive capabilities per model (not leave them
    # all-None). Guards the second facet of the fb0ed2ee3 regression.
    port = _FakeModelsPort(providers_raw=[
        {
            "id": "openai",
            "name": "OpenAI",
            "models": [
                {"id": "gpt-4o", "input": ["text", "image"], "contextWindow": 200000},
                {"id": "gpt-4", "input": ["text"]},
            ],
        }
    ])
    adapter = OpenClawModelsAdapter(port)
    providers = await adapter.list_providers()
    m0, m1 = providers[0].models
    assert m0.capabilities.vision is True
    assert m0.capabilities.context_window == 200000
    assert m1.capabilities.vision is False


@pytest.mark.asyncio
async def test_list_providers_skips_empty_model_ids():
    port = _FakeModelsPort(providers_raw=[
        {
            "id": "prov",
            "models": [{"id": ""}, {"id": "m1"}],
        }
    ])
    adapter = OpenClawModelsAdapter(port)
    providers = await adapter.list_providers()
    assert len(providers[0].models) == 1
    assert providers[0].models[0].id == "prov/m1"


@pytest.mark.asyncio
async def test_list_providers_empty_on_empty_port():
    port = _FakeModelsPort(providers_raw=[])
    adapter = OpenClawModelsAdapter(port)
    providers = await adapter.list_providers()
    assert providers == []
    assert port.providers_list_calls == 1


@pytest.mark.asyncio
async def test_list_providers_uses_id_when_name_missing():
    port = _FakeModelsPort(providers_raw=[{"id": "p1", "models": []}])
    adapter = OpenClawModelsAdapter(port)
    providers = await adapter.list_providers()
    assert providers[0].name == "p1"
