"""Shared HTTP contract primitives for the public ``/openapi/v1/bots`` API.

The response envelope, pagination controls, and small shared payloads that every
public route reuses. These are contract definitions consumed by OpenAPI
generation; handlers are stubs (a later pass wires them to services).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Query
from pydantic import BaseModel, ConfigDict, Field

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

    Shape-identical to the success envelope with `data` pinned to null, which is
    what the error paths actually emit.
    """

    # Declared as its own model, rather than reusing Envelope, so generated
    # clients get a named error type instead of a synthesized `Envelope[None]`.

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

# The extra failure a **user-scoped** route can produce: its ``user_id`` named
# someone other than the verified caller. Kept out of ``ERROR_RESPONSES`` for the
# same reason the engine-runtime statuses below are — that dict is applied
# surface-wide, and the routes that take no ``user_id`` (the Bot Logs group, plus
# the four catalogue reads with no user dimension) can never answer 403.
USER_SCOPED_403: dict[int | str, dict[str, object]] = {
    403: {
        "model": ErrorEnvelope,
        "description": "The user_id names a user the authenticated caller may "
        "not act for",
    },
}

# For the nine groups whose every route is user-scoped, applied at assembly.
USER_SCOPED_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    **ERROR_RESPONSES,
    **USER_SCOPED_403,
}

# Extra failures only the engine-runtime groups can produce. Attached to those
# routers, NOT merged into ``ERROR_RESPONSES``: that dict is applied surface-wide
# in ``build_public_router``, and ``test_openapi_error_schema`` asserts every
# operation documents every status in it — so adding these there would make the
# six already-shipped categories advertise a 501 they cannot return, pointing at
# an endpoint unrelated to them, and generate dead branches in clients.
#
# Built on the user-scoped set, not on ``ERROR_RESPONSES``: every engine-runtime
# route is user-scoped too, so it documents the 403 as well.
ENGINE_RUNTIME_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    **USER_SCOPED_ERROR_RESPONSES,
    501: {
        "model": ErrorEnvelope,
        "description": "Not supported for this bot — either its engine does not "
        "declare the capability (see the engine-capabilities endpoint) or the "
        "operation is not offered for this bot type",
    },
    504: {"model": ErrorEnvelope, "description": "Upstream service timed out"},
}


class Page[T](BaseModel):
    """A page of items returned by list endpoints."""

    total: int = Field(description="Total number of items matching the query.")
    items: list[T] = Field(
        description="Items on the current page (present, possibly empty)."
    )


class Deleted(BaseModel):
    """Payload returned by delete operations."""

    model_config = ConfigDict(json_schema_extra={"example": {"deleted": True}})

    deleted: bool = Field(
        default=True, description="Always true; the delete is complete once this "
        "response is returned."
    )


class NameCheck(BaseModel):
    """Payload returned by name-availability checks."""

    model_config = ConfigDict(
        json_schema_extra={"example": {"name": "Quarterly reporter", "exists": False}}
    )

    name: str = Field(
        description="The name that was checked, in the trimmed form actually "
        "compared."
    )
    exists: bool = Field(
        description="True when the name is already taken, so creating with it "
        "would be refused."
    )


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
