"""Read the active engine's effective new-session default model."""

from fastapi import HTTPException

from engine.community.api.caps import check_capability
from engine.community.api.response import ApiResponse
from engine.community.core.engine.capability import Capability
from engine.community.core.engine.exceptions import CapabilityNotSupportedError
from engine.community.core.models.models import DefaultModel
from engine.community.manager import EngineManager


async def default_model() -> ApiResponse:
    warning = check_capability(Capability.MODEL_DEFAULT_GET)
    try:
        model = await EngineManager.get_instance().models.get_default_model()
        model = DefaultModel.model_validate(model)
    except CapabilityNotSupportedError as error:
        raise HTTPException(501, "Default model is not supported") from error
    except TimeoutError as error:
        raise HTTPException(504, "Default model query timed out") from error
    except Exception as error:
        raise HTTPException(502, "Default model query failed") from error
    return ApiResponse(success=True, data=model.model_dump(), warning=warning)
