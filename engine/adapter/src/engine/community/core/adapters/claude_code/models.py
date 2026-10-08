"""ClaudeCode models ACL adapter.

Implements the core `ModelsService` by delegating to an injected
`ClaudeCodeModelsPort` and translating the port's raw model/provider dicts →
`Model` / `Provider` DTOs.

The dict→DTO helper functions (`_capabilities_from_payload`,
`_model_from_payload`, `_provider_from_payload`) are relocated intact from
`engines/claude_code/models.py`. The relay already emits snake_case field
names (``display_name``, ``context_window``) that match the core DTOs, so no
camel↔snake translation is needed (unlike cron / session).

Models is token-aware on the claude_code port (``models_list`` /
``models_list_providers`` accept ``token``), so this adapter DOES extract
``auth.token`` — differing from the OpenClaw models adapter which is
token-agnostic.
"""
from __future__ import annotations

import logging
from typing import Any

from engine.community.core.engine.context import AuthContext
from engine.community.core.models.models import Model, ModelCapabilities, Provider
from engine.community.core.models.protocol import ModelsService
from engine.community.plugin_api.claude_code.models_port import ClaudeCodeModelsPort

log = logging.getLogger("claude-code-models-adapter")


def _capabilities_from_payload(raw: Any) -> ModelCapabilities:
    """Build ``ModelCapabilities`` from the relay's capabilities dict.

    Unknown keys are ignored (forward-compat); missing keys stay ``None``.
    Accepts non-dict input defensively. Relocated from corp impl.
    """
    if not isinstance(raw, dict):
        return ModelCapabilities()
    return ModelCapabilities(
        context_window=raw.get("context_window"),
        max_output_tokens=raw.get("max_output_tokens"),
        vision=raw.get("vision"),
        function_calling=raw.get("function_calling"),
        reasoning=raw.get("reasoning"),
        streaming=raw.get("streaming"),
        json_mode=raw.get("json_mode"),
    )


def _model_from_payload(raw: dict[str, Any], *, provider_id: str | None = None) -> Model:
    """Convert one ``models.list`` entry into a `Model`.

    ``provider_id`` is a fallback used when the entry came from a nested
    ``providers.list`` ``models`` array and does not carry its own
    ``provider`` field.
    """
    return Model(
        id=str(raw.get("id") or ""),
        provider=str(raw.get("provider") or provider_id or ""),
        name=str(raw.get("name") or raw.get("id") or ""),
        display_name=raw.get("display_name"),
        enabled=bool(raw.get("enabled", True)),
        default=bool(raw.get("default", False)),
        capabilities=_capabilities_from_payload(raw.get("capabilities")),
    )


def _provider_from_payload(raw: dict[str, Any]) -> Provider:
    pid = str(raw.get("id") or "")
    nested = raw.get("models")
    models = (
        [_model_from_payload(m, provider_id=pid) for m in nested if isinstance(m, dict)]
        if isinstance(nested, list)
        else []
    )
    return Provider(
        id=pid,
        name=str(raw.get("name") or pid),
        enabled=bool(raw.get("enabled", True)),
        models=models,
    )


class ClaudeCodeModelsAdapter(ModelsService):
    """`ModelsService` over the claude_code native models port."""

    def __init__(self, port: ClaudeCodeModelsPort) -> None:
        self._port = port

    async def list_models(
        self, auth: AuthContext | None = None,
    ) -> list[Model]:
        """Return the flat model catalogue."""
        token = auth.token if auth is not None else None
        raw = await self._port.models_list(token=token)
        out: list[Model] = []
        for entry in raw:
            try:
                if not isinstance(entry, dict):
                    continue
                out.append(_model_from_payload(entry))
            except Exception as e:  # noqa: BLE001
                log.warning("[list_models] convert failed: %s", e)
        return out

    async def list_providers(
        self, auth: AuthContext | None = None,
    ) -> list[Provider]:
        """Return providers with nested models."""
        token = auth.token if auth is not None else None
        raw = await self._port.models_list_providers(token=token)
        out: list[Provider] = []
        for entry in raw:
            try:
                if not isinstance(entry, dict):
                    continue
                out.append(_provider_from_payload(entry))
            except Exception as e:  # noqa: BLE001
                log.warning("[list_providers] convert failed: %s", e)
        return out


__all__ = ["ClaudeCodeModelsAdapter"]
