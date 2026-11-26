"""Stateless inference on the addressed engine; no config/credential export."""

from __future__ import annotations

from engine.community.api.caps import check_capability
from engine.community.core.engine.capability import Capability
from engine.community.core.models.completion import (
    CompletionRequest,
    ModelCompletion,
    ModelCompletionService,
)
from engine.community.manager import EngineManager
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/engine", tags=["engine"])


@router.post("/models:complete")
async def complete_model(body: CompletionRequest):
    warning = check_capability(Capability.MODEL_COMPLETE)
    plugin = EngineManager.get_instance().models
    if not isinstance(plugin, ModelCompletionService):
        raise HTTPException(501, "stateless model completion is not supported")
    try:
        result = ModelCompletion.model_validate(await plugin.complete_model(body))
    except NotImplementedError:
        raise HTTPException(
            501, "model routing or authentication is not supported"
        ) from None
    except Exception:  # noqa: BLE001 — config, prompts, credentials stay in engine
        raise HTTPException(502, "engine model completion unavailable") from None
    return {"success": True, "data": result.model_dump(), "warning": warning}
