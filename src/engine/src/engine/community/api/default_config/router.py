"""Default-config router — dispatches through ``EngineManager.default_config``.

Engine-specific behaviour (which file to read, where it lives) is owned
by the engine's ``default_config`` plugin. The router applies the
capability guard and translates plugin exceptions to HTTP codes.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from engine.community.api.caps import check_capability
from engine.community.api.response import ApiResponse
from engine.community.core.engine.capability import Capability
from engine.community.core.engine.exceptions import CapabilityNotSupportedError

router = APIRouter(prefix="/api/openclaw", tags=["openclaw"])


def _default_config_plugin():
    from engine.community.manager import EngineManager
    return EngineManager.get_instance().default_config


@router.get("/default-config", response_model=ApiResponse)
async def get_default_config() -> ApiResponse:
    warning = check_capability(Capability.DEFAULT_CONFIG_GET)
    try:
        plugin = _default_config_plugin()
        result = await plugin.get_default_config()
    except CapabilityNotSupportedError as e:
        raise HTTPException(status_code=501, detail=str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except IsADirectoryError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    return ApiResponse(
        success=True,
        data={"path": result.path, "config": result.config},
        message="查询成功",
        warning=warning,
    )
