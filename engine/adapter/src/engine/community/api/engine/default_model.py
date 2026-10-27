"""Read the addressed engine's default without exposing its configuration."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, StrictStr

from engine.community.api.caps import check_capability
from engine.community.api.response import ApiResponse
from engine.community.core.engine.capability import Capability
from engine.community.core.engine.exceptions import CapabilityNotSupportedError
from engine.community.core.models.default_model import DefaultModelService
from engine.community.manager import EngineManager

router = APIRouter(prefix="/api/engine", tags=["engine"])


class DefaultModelData(BaseModel):
    model_id: StrictStr | None


class DefaultModelResponse(ApiResponse):
    data: DefaultModelData


@router.get("/default-model", response_model=DefaultModelResponse)
async def get_default_model() -> DefaultModelResponse:
    """Return the engine-owned default; null means no configured default."""
    warning = check_capability(Capability.MODEL_DEFAULT_GET)
    plugin = EngineManager.get_instance().models
    if not isinstance(plugin, DefaultModelService):
        raise HTTPException(500, "default model capability is not wired")
    try:
        model_id = await plugin.get_default_model()
        if model_id is not None and (not isinstance(model_id, str) or not model_id.strip()):
            raise ValueError("invalid default model identifier")
    except CapabilityNotSupportedError as exc:
        raise HTTPException(501, "default model is not supported") from exc
    except Exception as exc:  # noqa: BLE001 — never expose config/provider details
        raise HTTPException(502, "failed to read engine default model") from exc
    return DefaultModelResponse(
        success=True, data=DefaultModelData(model_id=model_id), warning=warning,
    )
