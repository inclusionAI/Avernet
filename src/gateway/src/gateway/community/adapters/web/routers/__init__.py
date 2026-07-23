"""Public API router aggregation.

``include_all(app)`` mounts every ``/openapi/v1`` group router onto the app; it
is called once from ``create_app``. Add a group by importing its router in
``_group_routers``.

Group routers are definition-only: their handlers are stubs whose purpose is to
make FastAPI generate the OpenAPI contract.
"""

from fastapi import APIRouter, FastAPI


def _group_routers() -> list[APIRouter]:
    from gateway.community.adapters.web.routers.bots import router as bots_router

    return [bots_router]


def include_all(app: FastAPI) -> None:
    """Mount every registered public-API group router onto ``app``."""
    for router in _group_routers():
        app.include_router(router)


__all__ = [
    "include_all",
]
