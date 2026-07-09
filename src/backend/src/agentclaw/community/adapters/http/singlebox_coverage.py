"""Singlebox coverage hooks for the HTTP adapter layer."""
from __future__ import annotations

from fastapi import FastAPI, Request

from agentclaw.community.utils.singlebox_coverage_recorder import record_router_hit


def install_singlebox_coverage_middleware(app: FastAPI) -> None:
    """Record concrete FastAPI route hits while singlebox coverage is enabled."""

    @app.middleware("http")
    async def _singlebox_coverage_router_hit_middleware(request: Request, call_next):
        response = await call_next(request)
        route = request.scope.get("route")
        route_path = getattr(route, "path", None) or request.url.path
        record_router_hit(
            method=request.method,
            route_path=route_path,
            path=request.url.path,
            status_code=response.status_code,
        )
        return response
