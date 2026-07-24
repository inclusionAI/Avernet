"""Re-home existing routes onto the public ``/openapi/v1/bots`` surface.

This module performs a **path move only** — it re-mounts the *same* handler
functions (and their dependencies) under new paths. No business logic is written
or changed here; the legacy ``/api/...`` routes stay exactly as they were.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.routing import APIRoute

TARGET_BASE = "/openapi/v1/bots"


def rehome_into(public: APIRouter, source: APIRouter, *, strip_prefix: str) -> None:
    """Add every route in *source* to *public*, moved under ``TARGET_BASE``.

    ``strip_prefix`` is removed from each route's path before ``TARGET_BASE`` is
    prepended, so e.g. ``/api/channels`` (strip ``/api``) → ``/openapi/v1/bots/
    channels`` and ``/api/bots/{id}`` (strip ``/api/bots``) → ``/openapi/v1/bots/
    {id}``.
    """
    for route in source.routes:
        if not isinstance(route, APIRoute):
            continue
        tail = (
            route.path[len(strip_prefix) :]
            if route.path.startswith(strip_prefix)
            else route.path
        )
        public.add_api_route(
            TARGET_BASE + tail,
            route.endpoint,
            methods=sorted(route.methods or set()),
            response_model=route.response_model,
            status_code=route.status_code,
            tags=route.tags,
            dependencies=route.dependencies,
            summary=route.summary,
            description=route.description,
            response_description=route.response_description,
            responses=route.responses,
            deprecated=route.deprecated,
            name=route.name,
            response_model_include=route.response_model_include,
            response_model_exclude=route.response_model_exclude,
            response_model_by_alias=route.response_model_by_alias,
            response_model_exclude_unset=route.response_model_exclude_unset,
            response_model_exclude_defaults=route.response_model_exclude_defaults,
            response_model_exclude_none=route.response_model_exclude_none,
            include_in_schema=route.include_in_schema,
            response_class=route.response_class,
            openapi_extra=route.openapi_extra,
            callbacks=route.callbacks,
        )
