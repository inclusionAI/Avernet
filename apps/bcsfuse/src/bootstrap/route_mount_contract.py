"""Validation for required BCSFuse business-route groups."""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute

logger = logging.getLogger(__name__)


RouteSignature = tuple[str, frozenset[str], object]


def _prefixed_path(prefix: str, path: str) -> str:
    return f"{prefix.rstrip('/')}{path}"


def _router_signatures(
    router: APIRouter,
    *,
    prefix: str,
) -> set[RouteSignature]:
    return {
        (
            _prefixed_path(prefix, route.path),
            frozenset(route.methods or ()),
            route.endpoint,
        )
        for route in router.routes
        if isinstance(route, APIRoute)
    }


def _app_signatures(app: FastAPI) -> set[RouteSignature]:
    try:
        from fastapi.routing import iter_route_contexts
    except ImportError:
        routes = app.routes
    else:
        routes = iter_route_contexts(app.routes)

    return {
        (route.path, frozenset(route.methods or ()), route.endpoint)
        for route in routes
        if isinstance(getattr(route, "original_route", route), APIRoute)
    }


def _router_is_mounted(
    app: FastAPI,
    router: APIRouter,
    *,
    prefix: str,
) -> bool:
    expected = _router_signatures(router, prefix=prefix)
    return bool(expected) and expected.issubset(_app_signatures(app))


def _require_router_mounted(
    app: FastAPI,
    router: APIRouter,
    *,
    prefix: str,
    group: str,
) -> None:
    if not _router_is_mounted(app, router, prefix=prefix):
        raise RuntimeError(f"Required route group is not fully mounted: {group}")


def validate_required_oss_business_routes(app: FastAPI) -> None:
    """Fail composition when a required route group was silently skipped."""
    from src.interfaces.api.worker_profile_parity_routes import (
        admin_router,
        api_router,
        compat_router,
        mgmt_router,
    )

    required_routers = [
        (api_router, "/api/v1", "R3 worker/profile API"),
        (mgmt_router, "/v1", "R3 worker/profile management"),
        (compat_router, "/v1", "R3 worker/profile compatibility"),
    ]
    if os.getenv("BCSFUSE_EXPOSE_ADMIN", "false").lower() == "true":
        required_routers.append((admin_router, "/v1/admin", "R3 worker/profile admin"))

    from src.interfaces.api.fusion_parity_routes import router as fusion_router
    from src.interfaces.api.verify_routes import router as verify_router

    required_routers.extend(
        [
            (fusion_router, "/api/v1", "R5 fusion"),
            (verify_router, "/v1", "capability verification"),
        ]
    )

    for router, prefix, group in required_routers:
        _require_router_mounted(app, router, prefix=prefix, group=group)

    real_recommend_mounted = False
    fallback_recommend_mounted = False
    try:
        from src.interfaces.api.recommend_routes import router as recommend_router

        real_recommend_mounted = _router_is_mounted(
            app,
            recommend_router,
            prefix="/api/v1",
        )
    except Exception as error:  # noqa: BLE001 - the fallback may still be valid
        logger.warning(
            "Real recommend route validation unavailable: %s",
            type(error).__name__,
        )

    try:
        from src.interfaces.api.recommend_parity_routes import (
            router as recommend_fallback_router,
        )

        fallback_recommend_mounted = _router_is_mounted(
            app,
            recommend_fallback_router,
            prefix="/api/v1",
        )
    except Exception as error:  # noqa: BLE001 - report the required group below
        logger.warning(
            "Fallback recommend route validation unavailable: %s",
            type(error).__name__,
        )

    if not (real_recommend_mounted or fallback_recommend_mounted):
        raise RuntimeError("Required route group is not fully mounted: Recommend")
