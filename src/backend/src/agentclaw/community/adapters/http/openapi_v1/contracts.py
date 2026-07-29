"""Shared HTTP contract primitives for the public ``/openapi/v1/bots`` API.

The response envelope, pagination controls, and small shared payloads that every
public route reuses. These are contract definitions consumed by OpenAPI
generation; handlers are stubs (a later pass wires them to services).
"""

from __future__ import annotations

from typing import Annotated

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


class ErrorEnvelope(BaseModel):
    """The envelope returned on every documented failure.

    Shape-identical to :class:`Envelope` with ``data`` pinned to null, which is
    what the error paths actually emit. Declared as its own model so generated
    clients get a named error type instead of a synthesized ``Envelope[None]``.
    """

    code: int = Field(
        description="6-digit code: HTTP status (3) + business subcode (3), "
        "e.g. 404000 for a not-found failure."
    )
    message: str = Field(description="Human-readable failure reason; always English.")
    data: None = Field(default=None, description="Always null on an error response.")
    request_id: str = Field(
        description="Trace id; mirrors the X-Trace-Id response header."
    )


# Documented failure responses shared by every public route. Applied at router
# assembly (see ``build_public_router``) rather than repeated per handler.
#
# Without these, generated clients see only the success model plus FastAPI's
# default ``HTTPValidationError`` for 422 — a wire contract that disagrees with
# what this surface actually returns, since every failure here is an Envelope.
# The 422 entry deliberately replaces FastAPI's default: public validation
# failures are translated to the envelope by the app-level handler.
#
# Declared surface-wide, not per route: the envelope is uniform by design, the
# app-level backstop can produce 500 on any route, and a per-route list would
# drift out of sync with the mappings in ``responses.ENVELOPE_ERRORS``.
ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": ErrorEnvelope, "description": "Invalid request"},
    401: {"model": ErrorEnvelope, "description": "Missing or invalid credentials"},
    404: {
        "model": ErrorEnvelope,
        "description": "Not found — also returned when the resource exists but "
        "does not belong to the caller",
    },
    409: {"model": ErrorEnvelope, "description": "Conflicts with current state"},
    422: {"model": ErrorEnvelope, "description": "Request failed validation"},
    500: {"model": ErrorEnvelope, "description": "Internal error"},
    502: {"model": ErrorEnvelope, "description": "Upstream service error"},
}


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
