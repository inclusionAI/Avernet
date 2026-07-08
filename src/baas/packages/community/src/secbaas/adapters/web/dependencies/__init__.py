"""Web-layer FastAPI dependencies — shared across routers."""

__all__ = [
    "get_op_ctx",
]

from dependency_injector.wiring import Provide, inject
from fastapi import Depends, Request

from secbaas.api import OperationContext
from secbaas.api.auth import AuthService
from secbaas.bootstrap import ApplicationContainer


@inject
async def get_op_ctx(
    request: Request,
    auth_service: AuthService = Depends(
        Provide[ApplicationContainer.services.auth_service]
    ),
) -> OperationContext:
    """FastAPI dependency: resolve OperationContext from the current request.

    Extracts cookie and referer from the FastAPI Request, then delegates to
    AuthService.build_operation_context() for authentication and environment
    resolution.

    Usage::

        @router.get("/x")
        async def handler(ctx: OperationContext = Depends(get_op_ctx)):
            ...
    """
    cookie = "; ".join([f"{k}={m}" for k, m in request.cookies.items()])
    referer = str(request.base_url)
    return await auth_service.build_operation_context(cookie=cookie, referer=referer)
