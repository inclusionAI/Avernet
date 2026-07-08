from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from engine.community.api.bash.schemas import BashExecRequest
from engine.community.api.caps import check_capability
from engine.community.api.response import ApiResponse
from engine.community.core.engine.capability import Capability

log = logging.getLogger("api-bash")

router = APIRouter(prefix="/api/bash", tags=["bash"])


def _bash_plugin():
    from engine.community.manager import EngineManager
    return EngineManager.get_instance().bash


@router.post("/exec", response_model=ApiResponse)
async def exec_command(body: BashExecRequest) -> ApiResponse:
    """Execute a bash command in the container workspace."""
    warning = check_capability(Capability.BASH_EXEC)
    try:
        plugin = _bash_plugin()
        result = await plugin.exec(
            cmd=body.cmd,
            cwd=body.cwd,
            timeout=body.timeout,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        log.error(f"bash exec failed: cmd={body.cmd!r} cwd={body.cwd!r} error={e}")
        raise HTTPException(status_code=500, detail=str(e)) from e

    return ApiResponse(
        success=True,
        data={
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.exit_code,
        },
        warning=warning,
    )
