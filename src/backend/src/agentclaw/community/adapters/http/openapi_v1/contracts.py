"""Shared HTTP contract primitives for the public ``/openapi/v1/bots`` API.

The response envelope, pagination controls, and small shared payloads that every
public route reuses. These are contract definitions consumed by OpenAPI
generation; handlers are stubs (a later pass wires them to services).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Path, Query
from pydantic import BaseModel, ConfigDict, Field

# The published startup-script size limit, imported rather than retyped so the
# example in STARTUP_SCRIPT_WRITE_RESPONSES cannot drift from the enforced value.
# From ``api/`` — the Service API seam — not the core service module it lives in.
from agentclaw.community.api.bot_startup_script_service import MAX_SCRIPT_BYTES

# The published manifest document limit, imported for the same reason.
from agentclaw.community.api.bot_config_manifest_service import MAX_DOCUMENT_BYTES

#: The prefix every operation on this surface is mounted under.
#:
#: Defined here rather than in the package's ``__init__`` because ``access_log``
#: needs it at *module* scope, and ``__init__`` imports the routers before it
#: would reach the assignment. That ordering was harmless while no row was
#: ``Check``: nothing pulled ``bot_access`` — and through it ``access_log`` —
#: in during router import. The first adjudicated route makes
#: ``PublicAPIRoute.__init__`` import the seam at decoration time, which closes
#: the loop back into a half-initialised ``__init__``. A leaf module cannot be
#: half-initialised by anything downstream of it.
PUBLIC_API_PREFIX = "/openapi/v1"


# Standard codes = HTTP status (3 digits) + business subcode (3 digits).
CODE_OK = 200000
CODE_CREATED = 201000
CODE_ACCEPTED = 202000
CODE_NO_CONTENT = 204000

#: Illustrative trace id used by every example, so rendered samples show a
#: realistic value instead of the literal placeholder "string".
EXAMPLE_TRACE_ID = "b0a6d2f4e8c94b1a9f3d5e7c60218a4d"


# Injected into every parametrisation's schema (Envelope[Bot], Page[Skill], …):
# a parametrised generic does not inherit the generic's docstring, so without
# this the concrete wrapper components in the published document carry no
# description at all. `setdefault` so a named subclass that states its own
# docstring keeps it.
#
# The per-property examples exist because doc UIs synthesize a response sample
# from the schema: without them every envelope rendered `"code": 0` and
# `"message": "string"` around a fully-worked payload example. Property-level
# (never a whole-schema example): a top-level example would replace the
# synthesized `data`, losing the payload model's own example.
def _describe_envelope(schema: dict[str, Any]) -> None:
    schema.setdefault(
        "description",
        "Uniform response wrapper for every endpoint: `code`/`message` say how "
        "the call went, `data` carries the payload named in the wrapper's "
        "title, and `request_id` identifies the request for support.",
    )
    properties = schema.get("properties") or {}
    for name, value in (
        ("code", CODE_OK),
        ("message", "OK"),
        ("request_id", EXAMPLE_TRACE_ID),
    ):
        prop = properties.get(name)
        if isinstance(prop, dict):
            prop.setdefault("example", value)


def _describe_page(schema: dict[str, Any]) -> None:
    schema.setdefault(
        "description",
        "One page of a list result: `total` counts every match, `items` holds "
        "the current page.",
    )
    # Matches the synthesized `items` sample, which holds one element.
    total = (schema.get("properties") or {}).get("total")
    if isinstance(total, dict):
        total.setdefault("example", 1)


class Envelope[T](BaseModel):
    """Uniform response wrapper for every public endpoint."""

    model_config = ConfigDict(json_schema_extra=_describe_envelope)

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
    """The envelope returned on every documented failure — the same shape as
    the success envelope, with `data` pinned to null."""

    # Shape-identical to Envelope with data pinned to null, which is what the
    # error paths actually emit. Declared as its own model so generated clients
    # get a named error type instead of a synthesized Envelope[None].
    #
    # Field examples are the schema-view fallback; each documented status also
    # carries its own response example (see error_example below) with that
    # status's real code and message, which is what response samples render.

    code: int = Field(
        description="6-digit code: HTTP status (3) + business subcode (3), "
        "e.g. 404000 for a not-found failure.",
        json_schema_extra={"example": 404000},
    )
    message: str = Field(
        description="Human-readable failure reason; always English.",
        json_schema_extra={"example": "Not found"},
    )
    data: None = Field(default=None, description="Always null on an error response.")
    request_id: str = Field(
        description="Trace id; mirrors the X-Trace-Id response header.",
        json_schema_extra={"example": EXAMPLE_TRACE_ID},
    )


def error_example(status: int, message: str) -> dict[str, object]:
    """A worked response sample for one documented failure status.

    The code follows the status*1000 rule and the message is one the server
    really emits for that status, so rendered samples show actual values
    instead of type placeholders.
    """
    return {
        "content": {
            "application/json": {
                "example": {
                    "code": status * 1000,
                    "message": message,
                    "data": None,
                    "request_id": EXAMPLE_TRACE_ID,
                }
            }
        }
    }


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
#
# Example messages: where a status has one fixed public message on this surface
# (401/403/404/422) the example carries it verbatim; where messages vary by
# cause (400/409/500/502) it carries the reason-phrase fallback the unmapped
# path emits, since any specific domain message would be wrong on most routes.
ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {
        "model": ErrorEnvelope,
        "description": "Invalid request",
        **error_example(400, "Bad Request"),
    },
    401: {
        "model": ErrorEnvelope,
        "description": "Missing or invalid credentials",
        **error_example(401, "Unauthorized"),
    },
    404: {
        "model": ErrorEnvelope,
        "description": "Not found — also returned when the resource exists but "
        "does not belong to the caller",
        **error_example(404, "Not found"),
    },
    409: {
        "model": ErrorEnvelope,
        "description": "Conflicts with current state",
        **error_example(409, "Conflict"),
    },
    422: {
        "model": ErrorEnvelope,
        "description": "Request failed validation",
        **error_example(422, "Invalid request"),
    },
    500: {
        "model": ErrorEnvelope,
        "description": "Internal error",
        **error_example(500, "Internal Server Error"),
    },
    502: {
        "model": ErrorEnvelope,
        "description": "Upstream service error",
        **error_example(502, "Bad Gateway"),
    },
    503: {
        "model": ErrorEnvelope,
        "description": "Temporarily unavailable",
        **error_example(503, "Service Unavailable"),
    },
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
        **error_example(403, "Forbidden"),
    },
}

# The extra failure the startup-script **write** can produce. Kept here beside
# the other per-route sets rather than inline in the bots router, which sits
# against the 1000-line module cap.
#
# It is not in ``ERROR_RESPONSES``: that dict is applied surface-wide, and no
# other operation can answer 413. Without this entry the status is reachable but
# invisible to a client generated from the published schema — the 409 for an
# unsupported bot is already carried by the base set.
STARTUP_SCRIPT_WRITE_RESPONSES: dict[int | str, dict[str, object]] = {
    **USER_SCOPED_403,
    413: {
        "model": ErrorEnvelope,
        "description": "Script body exceeds the size limit.",
        **error_example(
            413, f"Startup script exceeds the {MAX_SCRIPT_BYTES}-byte limit"
        ),
    },
}

# The two extra failures the config-manifest **write** can produce. Same
# placement, and the same reason, as the startup-script table above: applying
# either surface-wide would make every operation advertise a status it cannot
# return.
#
# The 422 is the one that matters to a client. It is the all-or-nothing refusal,
# and unlike every other error on this surface it carries a ``data`` block: a
# list of ``{location, code, message}`` naming each offending entry. A fixed
# message alone would tell a caller their document is invalid and leave them to
# bisect it.
CONFIG_MANIFEST_WRITE_RESPONSES: dict[int | str, dict[str, object]] = {
    **USER_SCOPED_403,
    413: {
        "model": ErrorEnvelope,
        "description": "Manifest document exceeds the size limit.",
        **error_example(
            413, f"Config manifest exceeds the {MAX_DOCUMENT_BYTES}-byte limit"
        ),
    },
    422: {
        "model": ErrorEnvelope,
        "description": "The document was refused; `data.violations` names every "
        "reason, each with the entry it applies to.",
        **error_example(422, "Config manifest is invalid"),
    },
}

#: The install operation's failure table (W9). A CLI tool is an executable the
#: platform distributes on a caller's behalf, so the ways it can be refused are
#: worth publishing rather than collapsing into a 400.
CLI_TOOL_WRITE_RESPONSES: dict[int | str, dict[str, object]] = {
    **USER_SCOPED_403,
    409: {
        "model": ErrorEnvelope,
        "description": "The bot already has a CLI tool by that name.",
        **error_example(409, "The bot already has a CLI tool with this name"),
    },
    422: {
        "model": ErrorEnvelope,
        "description": "The declaration, the fetched bytes or the engine "
        "refused the install — an unpinned digest, a source that did not match "
        "it, an archive member that is not there, a binary built for another "
        "architecture, or an engine that would not take it.",
        **error_example(422, "The CLI tool could not be installed"),
    },
}

# For the nine groups whose every route is user-scoped, applied at assembly.
USER_SCOPED_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    **ERROR_RESPONSES,
    **USER_SCOPED_403,
}

# Space/member/favorite routes derive the actor from the verified principal and
# can answer 403 for a valid caller lacking the required space role. This is a
# different contract from USER_SCOPED_403: there is no caller-supplied user_id.
SPACE_SCOPED_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    **ERROR_RESPONSES,
    403: {
        "model": ErrorEnvelope,
        "description": "The authenticated user lacks the required space membership or role",
        **error_example(403, "Forbidden"),
    },
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
        **error_example(
            501,
            "Not supported by this bot's engine; see the engine capabilities "
            "endpoint",
        ),
    },
    504: {
        "model": ErrorEnvelope,
        "description": "Upstream service timed out",
        **error_example(504, "Engine request timed out"),
    },
}


class Page[T](BaseModel):
    """A page of items returned by list endpoints."""

    model_config = ConfigDict(json_schema_extra=_describe_page)

    total: int = Field(description="Total number of items matching the query.")
    items: list[T] = Field(
        description="Items on the current page (present, possibly empty)."
    )


class Deleted(BaseModel):
    """Payload returned by delete operations."""

    model_config = ConfigDict(json_schema_extra={"example": {"deleted": True}})

    deleted: bool = Field(
        default=True,
        description="Always true: the resource is gone. A failed delete "
        "answers an error envelope instead, never `deleted: false`.",
    )


class NameCheck(BaseModel):
    """Payload returned by name-availability checks."""

    model_config = ConfigDict(
        json_schema_extra={"example": {"name": "quarterly-report", "exists": False}}
    )

    name: str = Field(
        description="The name in the form the server actually checked — "
        "normalized (e.g. trimmed) from what was sent, so this is the exact "
        "string the availability answer applies to."
    )
    exists: bool = Field(
        description="True when the name is already taken; false when it is "
        "available to use."
    )


#: The path parameter naming the bot an operation addresses, documented once so
#: every group publishes the same wording. The example is the issued format
#: (date + 8 random characters); legacy deployments may hold other shapes, so
#: the format is illustrative, not a contract.
BotIdPath = Annotated[
    str,
    Path(
        description="The bot this operation addresses — its `bot_id` as "
        "issued at creation and returned by the bots listing, "
        "e.g. `20260813_a7k2m9p1`."
    ),
]


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
