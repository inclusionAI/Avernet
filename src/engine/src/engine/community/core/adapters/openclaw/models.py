"""OpenClaw models ACL adapter.

Implements the core `ModelsService` by delegating to an injected
`OpenClawModelsPort` and translating the port's raw model/provider dicts →
`Model` / `Provider` DTOs.

The dict→DTO builder functions (`_normalize_model_id`, `_capabilities_from_payload`,
`_model_from_entry`, `_provider_from_payload`) are relocated intact from
`engines/openclaw/models.py`.  The `models.list → providers.list` fallback
logic lives in the port impl; by the time the adapter sees the list it is
always a flat list of model dicts (for `list_models`) or provider dicts (for
`list_providers`).

Models is token-agnostic: no token is passed; the port impl uses `_default_client`.
The adapter therefore does NOT extract `auth.token`.
"""
from __future__ import annotations

import logging
from typing import Any

from engine.community.core.engine.context import AuthContext
from engine.community.core.models.models import Model, ModelCapabilities, Provider
from engine.community.core.models.protocol import ModelsService
from engine.community.plugin_api.openclaw.models_port import OpenClawModelsPort

log = logging.getLogger("openclaw-models-adapter")


_DEFAULT_CAPABILITIES = ModelCapabilities(
    context_window=128000,
    max_output_tokens=4096,
    vision=False,
    function_calling=True,
    reasoning=False,
    streaming=True,
    json_mode=True,
)


def _normalize_model_id(model_id: str, provider: str) -> str:
    """Return canonical ``provider/model`` form.

    OpenClaw RPC may return any of: ``provider/model`` (canonical),
    ``provider::model`` (Moltis double-colon), ``provider:model`` (single
    colon), or bare ``model``.  Last form synthesises the prefix.

    Relocated from `engines/openclaw/models.py:_normalize_model_id`.
    """
    if "/" in model_id:
        return model_id
    if "::" in model_id:
        return model_id.replace("::", "/", 1)
    if ":" in model_id:
        return model_id.replace(":", "/", 1)
    return f"{provider}/{model_id}"


def _capabilities_from_payload(item: Any) -> ModelCapabilities:
    """Derive `ModelCapabilities` from an OpenClaw model entry.

    OpenClaw's `models.list` / `providers.list` payload puts modality and
    routing hints at the **top level** of each model item (there is no nested
    `capabilities` object). The relevant fields per upstream's
    ``ModelCatalogModel`` (gateway/model-catalog/types.ts):

    - ``input``         : ("text"|"image"|"document")[]   → ``vision`` true iff "image" present
    - ``reasoning``     : boolean
    - ``contextWindow`` : number                          (fallback ``contextTokens``)
    - ``maxTokens``     : number

    ``function_calling`` / ``streaming`` / ``json_mode`` have no dedicated
    upstream field; we keep the defaults (True) rather than silently flipping
    them off.

    Relocated from `engines/openclaw/models.py:_capabilities_from_payload`
    (post-`fb0ed2ee3`): read the whole item from its top-level fields, NOT a
    nested `capabilities` key the gateway never sends.
    """
    if not isinstance(item, dict):
        return _DEFAULT_CAPABILITIES
    inputs = item.get("input") if isinstance(item.get("input"), list) else []
    return ModelCapabilities(
        context_window=(
            item.get("contextWindow")
            or item.get("contextTokens")
            or 128000
        ),
        max_output_tokens=item.get("maxTokens") or 4096,
        vision="image" in inputs,
        function_calling=True,
        reasoning=bool(item.get("reasoning", False)),
        streaming=True,
        json_mode=True,
    )


def _model_from_entry(item: dict[str, Any]) -> Model | None:
    """Build a `Model` from one entry of a `models.list` payload dict.

    Accepts entries produced by either `models.list` (direct) or the flattened
    `providers.list` path (where `provider` is pre-embedded by the port impl).

    Relocated from `engines/openclaw/models.py:_model_from_models_list_entry`.
    """
    model_id = item.get("id") or item.get("model") or ""
    if not model_id:
        return None
    provider = item.get("provider", "unknown")
    rpc_name = item.get("name")
    rpc_display = item.get("display_name") or rpc_name
    return Model(
        id=_normalize_model_id(model_id, provider),
        provider=provider,
        provider_id=model_id,
        name=rpc_name,
        display_name=rpc_display,
        description=item.get("description"),
        capabilities=_capabilities_from_payload(item),
        enterprise_enabled=bool(item.get("enabled", True)),
        enterprise_default=bool(item.get("default", False)),
    )


def _models_from_provider_entry(provider_data: dict[str, Any]) -> list[Model]:
    """Build `Model` entries by flattening a single `providers.list` entry.

    Relocated from `engines/openclaw/models.py:_models_from_providers_list_entry`.
    """
    provider = provider_data.get("id") or provider_data.get("name") or "unknown"
    out: list[Model] = []
    for model_item in provider_data.get("models", []) or []:
        if isinstance(model_item, dict):
            model_id = model_item.get("id") or model_item.get("name") or ""
            rpc_name = model_item.get("name")
            rpc_display = model_item.get("display_name") or rpc_name
            description = model_item.get("description")
        else:
            model_id = str(model_item)
            rpc_name = None
            rpc_display = None
            description = None
        if not model_id:
            continue
        out.append(
            Model(
                id=_normalize_model_id(model_id, provider),
                provider=provider,
                provider_id=model_id,
                name=rpc_name,
                display_name=rpc_display,
                description=description,
                capabilities=_capabilities_from_payload(model_item),
                enterprise_enabled=bool(provider_data.get("enabled", True)),
            )
        )
    return out


def _provider_from_payload(raw: dict[str, Any]) -> Provider:
    """Build a `Provider` from a raw `providers.list` entry dict.

    Relocated from `engines/openclaw/models.py:_provider_from_payload`.
    """
    pid = str(raw.get("id") or raw.get("name") or "")
    return Provider(
        id=pid,
        name=str(raw.get("name") or pid),
        enabled=bool(raw.get("enabled", True)),
        models=_models_from_provider_entry(raw),
    )


class OpenClawModelsAdapter(ModelsService):
    """`ModelsService` over the OpenClaw native port (token-agnostic)."""

    def __init__(self, port: OpenClawModelsPort) -> None:
        self._port = port

    async def list_models(
        self, auth: AuthContext | None = None,
    ) -> list[Model]:
        """Return the flat model catalogue; auth is ignored (token-agnostic)."""
        raw = await self._port.models_list()
        out: list[Model] = []
        for entry in raw:
            try:
                model = _model_from_entry(entry)
                if model is not None:
                    out.append(model)
            except Exception as e:  # noqa: BLE001
                log.warning(f"[list_models] convert failed: {e}")
        return out

    async def list_providers(
        self, auth: AuthContext | None = None,
    ) -> list[Provider]:
        """Return providers with nested models; auth is ignored (token-agnostic)."""
        raw = await self._port.providers_list()
        out: list[Provider] = []
        for entry in raw:
            try:
                out.append(_provider_from_payload(entry))
            except Exception as e:  # noqa: BLE001
                log.warning(f"[list_providers] convert failed: {e}")
        return out


__all__ = ["OpenClawModelsAdapter"]
