"""Shared HTTP contract primitives for the public ``/openapi/v1/bots`` API.

The response envelope, pagination controls, and small shared payloads that every
public route reuses, plus the ``x-avernet-security`` marker. These are contract
definitions consumed by OpenAPI generation; handlers are stubs (a later pass
wires them to services).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Query
from pydantic import BaseModel, Field

# Standard codes = HTTP status (3 digits) + business subcode (3 digits).
CODE_OK = 200000
CODE_CREATED = 201000
CODE_ACCEPTED = 202000
CODE_NO_CONTENT = 204000


class Envelope[T](BaseModel):
    """Uniform response wrapper for every public endpoint."""

    code: int = Field(
        description="6-digit code: HTTP status (3) + business subcode (3)."
    )
    message: str = Field(
        description='Human-readable status; always English (e.g. "OK").'
    )
    data: T | None = Field(
        description="Response payload; present but null on errors or empty results."
    )
    request_id: str = Field(
        description="Trace id; mirrors the X-Trace-Id response header."
    )


class Page[T](BaseModel):
    """A page of items returned by list endpoints."""

    total: int = Field(description="Total number of items matching the query.")
    items: list[T] = Field(
        description="Items on the current page (present, possibly empty)."
    )


class Deleted(BaseModel):
    """Payload returned by delete operations."""

    deleted: bool = True


class NameCheck(BaseModel):
    """Payload returned by name-availability checks."""

    name: str
    exists: bool


class PageParams:
    """Standard 1-based pagination controls shared by all list endpoints."""

    def __init__(
        self,
        page: Annotated[int, Query(ge=1, description="1-based page number.")] = 1,
        page_size: Annotated[
            int, Query(ge=1, le=100, description="Items per page (max 100).")
        ] = 20,
    ) -> None:
        self.page = page
        self.page_size = page_size


PageParamsDep = Annotated[PageParams, Depends()]


def requires_user_principal() -> dict[str, Any]:
    """OpenAPI extra marking a route as requiring an authenticated user principal."""
    return {"x-avernet-security": [{"first_party_user": {}}]}
