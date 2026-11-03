"""Shared, opt-in v2 transport. It never selects a business implementation."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from search_core.api.errors import (
    SearchOverloaded,
    SearchUnavailable,
    UnsupportedSearchOption,
)
from search_core.api.models import (
    EffectiveSearch,
    ScoreDetails,
    SearchCommand,
    SearchResult,
    VerifiedIdentity,
)
from search_core.api.services import SearchApplication
from fastapi.routing import APIRoute

from search_core.core.observation import events

Authenticator = Callable[[Request], Awaitable[VerifiedIdentity]]

MAX_SEARCH_BODY_BYTES = 64 * 1024
MAX_FILTER_FIELDS = 16
MAX_FILTER_VALUES_PER_FIELD = 32


class SearchBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    query: str = Field(min_length=1, max_length=4096)
    limit: int = Field(default=5, ge=1)
    filters: dict[str, list[str]] = Field(default_factory=dict)
    scene: str = Field(default="default", min_length=1, max_length=128)
    extensions: dict[str, Any] = Field(default_factory=dict)

    def command(self, operation: str) -> SearchCommand:
        return SearchCommand(
            self.query,
            self.limit,
            operation,
            {key: tuple(values) for key, values in self.filters.items()},
            self.scene,
            self.extensions,
        )

    @field_validator("query")
    @classmethod
    def non_blank_query(cls, value):
        if not value.strip():
            raise ValueError("query must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def bounded_filters(self):
        if len(self.filters) > MAX_FILTER_FIELDS:
            raise ValueError("filters must contain at most 16 fields")
        if any(
            len(values) > MAX_FILTER_VALUES_PER_FIELD
            for values in self.filters.values()
        ):
            raise ValueError("each filter must contain at most 32 values")
        return self


class BoundedSearchBody(SearchBody):
    """Keep the original HTTP contract unless a host owns limit normalization."""

    limit: int = Field(default=5, ge=1, le=20)


class ItemBody(BaseModel):
    id: str
    name: str
    summary: str
    score: float
    score_source: str
    extensions: dict[str, str]
    scoreDetails: ScoreDetails | None = None


class ResultBody(BaseModel):
    items: list[ItemBody]
    status: Literal["ok", "degraded"]
    reasons: list[str]
    effective: EffectiveSearch | None = None


class BoundedSearchAPIRoute(APIRoute):
    """Reject a declared oversized body before FastAPI parses the JSON body."""

    def get_route_handler(self):
        original_handler = super().get_route_handler()

        async def bounded_handler(request: Request):
            content_length = request.headers.get("content-length")
            if content_length is not None:
                try:
                    declared_size = int(content_length)
                except ValueError as error:
                    raise HTTPException(400, "invalid content length") from error
                if declared_size > MAX_SEARCH_BODY_BYTES:
                    raise HTTPException(413, "request body too large")
            return await original_handler(request)

        return bounded_handler


def create_search_router(
    application: SearchApplication,
    authenticate: Authenticator,
    *,
    timeout_seconds: float = 2.0,
    request_model: type[SearchBody] = BoundedSearchBody,
    result_mapper: Callable[[SearchResult], BaseModel] | None = None,
    response_model: type[BaseModel] = ResultBody,
) -> APIRouter:
    """The host owns provider lifecycle; no permissive auth default exists.

    The deadline cancels asynchronous work. Sync adapters must retain execution
    permits until underlying work finishes; this router is not a concurrency cap.
    """
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    router = APIRouter(prefix="/api/v2", tags=["search"], route_class=BoundedSearchAPIRoute)

    def endpoint(operation):
        async def execute(
            body: request_model,
            identity: Annotated[VerifiedIdentity, Depends(authenticate)],
        ):
            command = body.command(operation)
            try:
                result = await asyncio.wait_for(
                    application.execute(command, identity),
                    timeout_seconds,
                )
            except UnsupportedSearchOption as error:
                raise HTTPException(400, "unsupported search option") from error
            except SearchOverloaded as error:
                raise HTTPException(429, "search capacity exceeded") from error
            except SearchUnavailable as error:
                raise HTTPException(503, "search unavailable") from error
            except asyncio.TimeoutError as error:
                events.record(
                    "search_request",
                    "http",
                    "deadline_exceeded",
                    timeout_ms=timeout_seconds * 1000,
                )
                raise HTTPException(504, "search deadline exceeded") from error
            if result_mapper is not None:
                return result_mapper(result)
            return ResultBody(
                items=[
                    ItemBody(
                        id=item.item_id,
                        name=item.name,
                        summary=item.summary,
                        score=item.score,
                        score_source=item.score_source,
                        extensions=item.extensions,
                        scoreDetails=item.score_details,
                    )
                    for item in result.items
                ],
                status=result.status,
                reasons=list(result.reasons),
                effective=result.effective,
            )

        return execute

    for operation in ("search", "recommend"):
        router.add_api_route(
            f"/{operation}",
            endpoint(operation),
            methods=["POST"],
            response_model=response_model,
            response_model_exclude_none=True,
            name=f"{operation}_v2",
        )
    return router
