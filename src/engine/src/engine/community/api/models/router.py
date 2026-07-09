"""
Model management router — 模型列表 API.

Dispatches through the active engine's :class:`ModelsService` (no legacy
``ModelAPI`` ABC anymore). When the engine doesn't declare ``MODEL_LIST``
the handler short-circuits to 501 via :func:`check_capability` before
touching the plugin.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from engine.community.api.caps import check_capability
from engine.community.api.models.schemas import ModelsListResponse
from engine.community.api.response import ApiResponse
from engine.community.core.engine.capability import Capability
from engine.community.core.engine.exceptions import CapabilityNotSupportedError
from engine.community.core.models.models import Model

router = APIRouter(prefix="/api/models", tags=["models"])


# ==================== Helper Functions ====================

def _model_to_dict(model: Model) -> dict:
    """Convert :class:`Model` to the legacy frontend-facing shape."""
    result: dict = {
        "id": model.id,
        "provider_id": model.provider_id,
        "provider": model.provider,
        "name": model.name,
        "display_name": model.display_name,
        "description": model.description,
        "enterprise_enabled": model.enterprise_enabled,
        "enterprise_default": model.enterprise_default,
    }

    if model.provider_category is not None:
        result["provider_category"] = model.provider_category

    # Merge engine-specific extra fields (e.g. runtime, quota info, …)
    # into the top-level response dict so the frontend receives them
    # without needing a DTO field for every relay key.  Filtering on
    # existing result keys protects against relay-provided keys that
    # collide with explicitly validated fields (id, provider, pricing,
    # enterprise_enabled, …) — they must not be overwritten.
    if model.extra:
        for k, v in model.extra.items():
            if k not in result:
                result[k] = v

    if model.capabilities:
        result["capabilities"] = {
            "context_window": model.capabilities.context_window,
            "max_output_tokens": model.capabilities.max_output_tokens,
            "vision": model.capabilities.vision,
            "function_calling": model.capabilities.function_calling,
            "reasoning": model.capabilities.reasoning,
            "streaming": model.capabilities.streaming,
            "json_mode": model.capabilities.json_mode,
        }

    if model.pricing:
        result["pricing"] = {
            "input_price": model.pricing.input_price,
            "output_price": model.pricing.output_price,
            "cache_read_price": model.pricing.cache_read_price,
            "cache_write_price": model.pricing.cache_write_price,
        }

    return result


def _models_plugin():
    """Resolve the active engine's ModelsService via EngineManager."""
    from engine.community.manager import EngineManager
    return EngineManager.get_instance().models


# ==================== API Endpoints ====================

@router.get("", response_model=ModelsListResponse)
async def list_models() -> ModelsListResponse:
    """List every model the active engine can route to."""
    warning = check_capability(Capability.MODEL_LIST)
    try:
        plugin = _models_plugin()
        models = await plugin.list_models()
    except CapabilityNotSupportedError as e:
        raise HTTPException(status_code=501, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001 — surface as failure response
        return ModelsListResponse(
            success=False,
            message=f"获取模型列表失败: {e}",
            warning=warning,
        )

    return ModelsListResponse(
        success=True,
        data={
            "models": [_model_to_dict(m) for m in models],
            "total": len(models),
        },
        warning=warning,
    )


@router.get("/{model_id:path}", response_model=ApiResponse)
async def get_model(model_id: str) -> ApiResponse:
    """Look up a single model by its normalised id (e.g. ``openai/gpt-5.3``).

    The plugin Protocol exposes only ``list_models``; we filter the result
    here rather than adding a separate ``get_model`` Protocol method — the
    catalogues are small (≤ low hundreds of entries).
    """
    warning = check_capability(Capability.MODEL_LIST)
    try:
        plugin = _models_plugin()
        models = await plugin.list_models()
    except CapabilityNotSupportedError as e:
        raise HTTPException(status_code=501, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        return ApiResponse(
            success=False,
            message=f"获取模型详情失败: {e}",
            warning=warning,
        )

    match = next(
        (
            m
            for m in models
            if m.id == model_id
        ),
        None,
    )
    if match is None:
        raise HTTPException(status_code=404, detail=f"Model not found: {model_id}")
    return ApiResponse(
        success=True,
        data=_model_to_dict(match),
        warning=warning,
    )
