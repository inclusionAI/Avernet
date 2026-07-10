"""Singlebox coverage hooks for the HTTP adapter layer."""
from __future__ import annotations

from fastapi import FastAPI, Request

from agentclaw.community.utils.singlebox_coverage_recorder import record_router_hit


def _full_route_path(route, request_path: str) -> str:
    """Restore prefixes that FastAPI drops from nested router scope metadata."""
    route_path = getattr(route, "path", None)
    path_regex = getattr(route, "path_regex", None)
    if not route_path:
        return request_path
    if path_regex is None or path_regex.fullmatch(request_path):
        return route_path

    for index, char in enumerate(request_path):
        if char != "/":
            continue
        if path_regex.fullmatch(request_path[index:]):
            return f"{request_path[:index]}{route_path}"
    return route_path


def install_singlebox_coverage_middleware(app: FastAPI) -> None:
    """Record concrete FastAPI route hits while singlebox coverage is enabled."""

    @app.middleware("http")
    async def _singlebox_coverage_router_hit_middleware(request: Request, call_next):
        response = await call_next(request)
        route = request.scope.get("route")
        route_path = _full_route_path(route, request.url.path)
        record_router_hit(
            method=request.method,
            route_path=route_path,
            path=request.url.path,
            status_code=response.status_code,
        )
        return response
