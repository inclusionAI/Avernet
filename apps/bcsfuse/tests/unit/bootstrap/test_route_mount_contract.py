from __future__ import annotations

from fastapi import APIRouter, FastAPI

from src.bootstrap.route_mount_contract import _router_is_mounted


def test_router_mount_validation_reads_effective_included_routes() -> None:
    router = APIRouter()

    @router.get("/workers")
    def list_workers() -> list[object]:
        return []

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    assert _router_is_mounted(app, router, prefix="/api/v1") is True
