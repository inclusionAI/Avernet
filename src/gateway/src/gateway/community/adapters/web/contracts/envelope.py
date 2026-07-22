"""Standard response envelope and shared payloads for the public API.

Every JSON response on `/openapi/v1` is wrapped in :class:`Envelope`:
``{code, message, data, request_id}``. ``code`` is a 6-digit integer — the
HTTP status (3 digits) followed by a business subcode (3 digits) — and
``message`` is always English.

These are **contract definitions**: they shape the generated OpenAPI so a
client knows the response format. At runtime the gateway forwards the
downstream component's response verbatim; the downstream API is what actually
produces the envelope (see ``routers`` package docs).
"""

from pydantic import BaseModel, Field

# Standard codes = HTTP status (3 digits) + business subcode (3 digits).
CODE_OK = 200000
CODE_CREATED = 201000
CODE_ACCEPTED = 202000
CODE_NO_CONTENT = 204000


class Envelope[T](BaseModel):
    """Uniform response wrapper for every public endpoint."""

    # All four keys are always present in every response; `data` is nullable
    # (present but null) rather than absent. Kept required (no defaults) so the
    # generated OpenAPI marks them required and SDKs don't treat them as omittable.
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
