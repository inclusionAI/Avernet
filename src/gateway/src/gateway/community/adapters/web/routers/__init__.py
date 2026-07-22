"""Public API router registry and aggregation.

Each `/openapi/v1` resource group defines an ``APIRouter`` and registers it in
``GROUP_ROUTERS``. ``include_all(app)`` mounts every registered router onto the
FastAPI app; it is called once from ``adapters/web/app.py::create_app``.

The routers are **definition-only**: their handlers are stubs whose sole
purpose is to make FastAPI generate the OpenAPI contract. No group routers are
registered yet — they land in the per-group tasks.
"""

from fastapi import APIRouter, FastAPI

# Group routers register here (appended in each group's module on import).
GROUP_ROUTERS: list[APIRouter] = []


def include_all(app: FastAPI) -> None:
    """Mount every registered public-API group router onto ``app``."""
    for router in GROUP_ROUTERS:
        app.include_router(router)
