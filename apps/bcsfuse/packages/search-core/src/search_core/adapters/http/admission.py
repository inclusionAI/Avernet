"""Immediate HTTP admission before request parsing and retrieval work."""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from search_core.api.errors import SearchOverloaded
from search_core.application.admission import SearchAdmission


class SearchAdmissionMiddleware:
    def __init__(self, app: ASGIApp, *, admission: SearchAdmission):
        self.app = app
        self.admission = admission

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or scope["path"].rstrip("/")
            not in {
                "/api/v2/search",
                "/api/v2/recommend",
                "/api/v2/agent-cards/preview",
            }
        ):
            await self.app(scope, receive, send)
            return
        try:
            with self.admission.admit("http"):
                await self.app(scope, receive, send)
        except SearchOverloaded:
            response = JSONResponse(
                {"detail": "search capacity exceeded"},
                status_code=429,
                headers={"Retry-After": "1", "Cache-Control": "no-store"},
            )
            await response(scope, receive, send)
