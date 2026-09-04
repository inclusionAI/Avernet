"""/api/engine/* — engine management HTTP endpoints (M4, doc §18.2).

Thin wrapper over `EngineManager` that lets the frontend introspect
capabilities, list registered engines, switch at runtime, and restart the
current engine. Business logic lives on the manager; this module only maps
HTTP concerns (status codes, response envelopes) onto its surface.

Error shape mirrors the rest of the web layer:
  200  — success (`{"success": True, ...}`)
  400  — bad request (unknown engine name)
  409  — conflict (active connections; retry with `force=true`)
  500  — internal error
"""
from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from engine.community.api.engine.schemas import (
    ActiveSessionQueryResponse,
    EngineRestartRequest,
    EngineSwitchRequest,
)
from engine.community.manager import EngineManager

log = logging.getLogger("engine-web")

router = APIRouter(prefix="/api/engine", tags=["engine"])


@router.get("/status")
async def engine_status() -> dict:
    """Active engine's runtime state: process, transition phase, connection count."""
    manager = EngineManager.get_instance()
    return await manager.status()


@router.get("/capabilities")
async def engine_capabilities(engine: str | None = None):
    """Declared capabilities for the active engine (default) or a named one.

    Response payload is `EngineCapabilities.to_dict()` — the frontend uses
    `supported` / `limited` / `fallback` to gate UI and relay warnings.
    """
    manager = EngineManager.get_instance()
    try:
        caps = manager.get_capabilities(engine)
    except Exception as e:
        log.warning(f"Engine capabilities lookup failed for {engine!r}: {e}")
        return JSONResponse(
            status_code=404,
            content={"success": False, "error": str(e)},
        )
    return {
        "success": True,
        "engine": engine or manager.engine,
        "data": caps.to_dict(),
    }


@router.get("/list")
async def list_registered_engines() -> dict:
    """Every engine registered with DEFAULT_REGISTRY, with active flag + version."""
    manager = EngineManager.get_instance()
    return {
        "success": True,
        "data": {
            "engines": manager.get_registered_engines(),
        },
    }


@router.get("/active-sessions")
async def engine_active_sessions(
    session_id: str | None = None,
    agent_id: str | None = None,
    timeout_seconds: float | None = None,
) -> ActiveSessionQueryResponse:
    """Read-only Active Session query for the active engine (M4, doc §18.2).

    Returns the dual-axis model (`query_status` / `verdict`):
      - `query_status=ok`    → `verdict=clear` (no in-flight runs) or
                                  `active` (>= 1 in-flight run)
      - `query_status=unsupported|timeout|error` → `verdict=unknown`

    OpenClaw backs this from its own process-local active-run registry; engines
    without that surface return `unsupported` rather than failing. This endpoint
    is read-only and never mutates engine or run state, and it must not block
    the engine hot path — query failures degrade to `unknown`.
    """
    manager = EngineManager.get_instance()
    result = await manager.active_sessions_query(
        session_id=session_id,
        agent_id=agent_id,
        timeout=timeout_seconds,
    )
    return ActiveSessionQueryResponse(**result)


@router.post("/switch")
async def engine_switch(request: EngineSwitchRequest):
    """Swap the active engine at runtime. 409 if active connections block it."""
    manager = EngineManager.get_instance()
    try:
        result = await manager.switch(request.engine, force=request.force)
        return {"success": True, **result}
    except ValueError as e:
        return JSONResponse(status_code=400, content={"success": False, "error": str(e)})
    except RuntimeError as e:
        return JSONResponse(status_code=409, content={"success": False, "error": str(e)})
    except Exception as e:
        log.exception(f"Engine switch failed: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.post("/restart")
async def engine_restart(request: EngineRestartRequest):
    """Restart the active engine's process. 409 if active connections block it."""
    manager = EngineManager.get_instance()
    try:
        result = await manager.restart(force=request.force)
        return {"success": True, **result}
    except ValueError as e:
        return JSONResponse(status_code=400, content={"success": False, "error": str(e)})
    except RuntimeError as e:
        return JSONResponse(status_code=409, content={"success": False, "error": str(e)})
    except Exception as e:
        log.exception(f"Engine restart failed: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


__all__ = ["router"]
