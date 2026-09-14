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
    ActiveSessionEntry,
    ActiveSessionsResponseData,
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


@router.get("/active-sessions")
async def engine_active_sessions(timeout_ms: int = 2000) -> dict:
    """Read-only Active Session query.

    Returns a dual-axis response envelope:
      ``{success, data: {query_status, verdict, engine, checked_at,
      active_session_count, sessions[]}}``

    Routing logic:
      * the active engine is OpenClaw and has been initialized via
        ``EngineManager.initialize()`` → delegate to
        ``OpenClawEngine.query_active_sessions``;
      * any other active engine or an uninitialized manager →
        ``query_status=unsupported, verdict=unknown``;
      * timeouts / exceptions from the OpenClaw engine are encoded into the
        response shape (the engine's query_active_sessions never raises).
    """
    manager = EngineManager.get_instance()
    active_engine = manager.active_engine_instance()
    # OpenClawEngine (and only it today) exposes query_active_sessions().
    supports_active_sessions = (
        active_engine is not None
        and hasattr(active_engine, "query_active_sessions")
    )
    if not supports_active_sessions:
        data = ActiveSessionsResponseData(
            query_status="unsupported",
            verdict="unknown",
            engine=manager.engine,
            checked_at=_iso_now(),
            active_session_count=0,
            sessions=[],
            incomplete=True,
            error_message="active engine does not support active-session queries",
        )
        return {"success": True, "data": data.model_dump()}

    try:
        raw = await active_engine.query_active_sessions(timeout_ms=timeout_ms)
    except Exception as exc:  # pragma: no cover — defensive; engines contract to never raise
        log.exception("active_engine.query_active_sessions raised: %s", exc)
        raw = {
            "query_status": "error",
            "verdict": "unknown",
            "engine": manager.engine,
            "checked_at": _iso_now(),
            "active_session_count": 0,
            "sessions": [],
            "incomplete": True,
            "error_message": str(exc) or "active-sessions query raised an exception",
        }
    sessions = [ActiveSessionEntry(**s) for s in raw.get("sessions", [])]
    data = ActiveSessionsResponseData(
        query_status=raw.get("query_status", "error"),
        verdict=raw.get("verdict", "unknown"),
        engine=raw.get("engine", manager.engine),
        checked_at=raw.get("checked_at", _iso_now()),
        active_session_count=int(raw.get("active_session_count", len(sessions))),
        sessions=sessions,
        incomplete=raw.get("incomplete"),
        error_message=raw.get("error_message"),
    )
    return {"success": True, "data": data.model_dump()}


def _iso_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(tz=UTC).isoformat()


__all__ = ["router"]
