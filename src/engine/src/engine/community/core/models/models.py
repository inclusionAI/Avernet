"""
Model Catalogue

Pydantic models describing the LLM catalogue exposed by an engine. Engines
that back multiple models (OpenRouter-style relays, aicoding via
teamclaw-aicoding-relay, …) surface them through :class:`ModelsService`
so the frontend can present a picker and so ``chat.send`` can carry a
``model`` selector.

Wire-format parity
------------------
The field names follow the teamclaw-aicoding-relay wire format (snake_case
on ``models.list`` / ``providers.list`` payloads). That keeps the plugin
adapter trivial and avoids a mapping table that the frontend has to
re-invert.

The ``provider_id``, ``description``, ``pricing``, ``enterprise_*``,
``release_date`` and ``deprecated`` fields are carried as Optional so
slim engines (aicoding) can leave them ``None`` while richer engines
(openclaw via gateway) populate them.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ModelCapabilities(BaseModel):
    """Declarable per-model knobs exposed on the wire.

    The relay attaches this block to every entry in ``models.list`` so the
    frontend can dim/hide models that lack a required capability (e.g. a
    vision upload on a text-only model). All fields are optional to keep
    the model permissive under schema evolution.
    """

    context_window: int | None = None
    max_output_tokens: int | None = None
    vision: bool | None = None
    function_calling: bool | None = None
    reasoning: bool | None = None
    streaming: bool | None = None
    json_mode: bool | None = None


class ModelPricing(BaseModel):
    """Per-1K-token pricing surfaced by gateways that publish billing info."""

    input_price: float | None = None
    output_price: float | None = None
    cache_read_price: float | None = None
    cache_write_price: float | None = None


class Model(BaseModel):
    """A single model entry in the catalogue.

    ``provider`` is the provider id (matches :class:`Provider.id`). For
    relays that do not group by provider (single-vendor setups), the
    plugin may synthesise a placeholder such as ``"default"``.

    ``provider_id`` carries the upstream-native id (pre-normalisation),
    needed when relays expect the original form on chat.send.
    """

    id: str
    provider: str
    provider_id: str | None = None
    name: str | None = None
    display_name: str | None = None
    description: str | None = None
    enabled: bool = True
    default: bool = False
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)
    pricing: ModelPricing | None = None
    provider_category: str | None = None
    enterprise_enabled: bool = True
    enterprise_default: bool = False
    release_date: str | None = None
    deprecated: bool = False


class Provider(BaseModel):
    """A catalogue provider (vendor grouping of models)."""

    id: str
    name: str
    enabled: bool = True
    models: list[Model] = Field(default_factory=list)


__all__ = ["Model", "ModelCapabilities", "ModelPricing", "Provider"]
