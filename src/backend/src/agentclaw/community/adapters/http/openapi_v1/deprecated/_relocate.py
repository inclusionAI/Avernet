"""Re-register a moved route at the address it used to have.

Eighteen of the legacy addresses need no translation at all: their bot was a
path parameter before the move and is one after, so the *same endpoint
function* answers both, and only the path template differs.

The obvious way to write that is eighteen hand-written registrations naming
each response model and response table again. This does it by reading them off
the route that already exists instead, for one reason: a hand-copied
``response_model`` is a second declaration of the contract, and the day someone
changes the real one is the day the legacy address starts publishing a
different shape than it serves. Deriving it cannot drift.

What is *not* derived is the mount — dependencies and the surface-wide response
table are attached where the router is included, and each legacy group is
included exactly as its replacement is. That is deliberate: parity means the
legacy address answers as it always did, and the mount is half of what decides
that.
"""

from __future__ import annotations

from typing import Callable

from fastapi import APIRouter
from fastapi.routing import APIRoute

from ._shim import legacy_route


def relocate(
    source: APIRouter,
    legacy: APIRouter,
    old_path: Callable[[str], str | None],
    skip: Callable[[str, str], bool] | None = None,
    transform: Callable[[Callable[..., object], str, str], Callable[..., object]]
    | None = None,
) -> APIRouter:
    """Register every route of *source* on *legacy* at its former address.

    ``old_path`` maps a current path to the one it replaced, returning ``None``
    for a path this mapper does not own. ``skip`` excludes an individual
    ``(method, path)`` — which is how the approvals *write* is kept out, since
    it shares its address with the read and differs only by method. Its body
    changed as well as its address, so it needs a hand-written shim rather than
    a re-registration.

    ``transform`` replaces the endpoint that gets registered, given
    ``(endpoint, method, new_path)``. It is how the groups whose bot moved out
    of the query string are handled: the response model and response table
    still come off the real route, and only the signature is adjusted.
    """
    for route in source.routes:
        if not isinstance(route, APIRoute):
            continue
        former = old_path(route.path)
        if former is None:
            continue
        for method in sorted(route.methods):
            if skip is not None and skip(method, route.path):
                continue
            endpoint = route.endpoint
            if transform is not None:
                endpoint = transform(endpoint, method, route.path)
            legacy_route(
                legacy,
                method,
                former[len(legacy.prefix) :],
                endpoint,
                replaces=route.path,
                response_model=route.response_model,
                responses=route.responses,
                status_code=route.status_code,
                summary=route.summary,
                # The id this address published before it was retired, rebuilt
                # from the route's own name and its former path. The current
                # address is at a different path, so FastAPI's rule gives it a
                # different id and the two cannot collide — while a client that
                # has not migrated keeps the method names its SDK was generated
                # with. See ``legacy_operation_id``.
                operation_name=route.name,
                name=f"{route.name}_deprecated",
            )
    return legacy


def bot_first_to_component_first(component: str) -> Callable[[str], str | None]:
    """The inverse of this feature's move, for one component.

    ``/openapi/v1/bots/{bot_id}/sessions/{session_id}`` came from
    ``/openapi/v1/bots/sessions/{bot_id}/{session_id}`` — the bot and the
    component swap back, and everything after them is untouched.
    """

    base = "/openapi/v1/bots"
    head = f"{base}/{{bot_id}}/{component}"

    def to_old(path: str) -> str | None:
        if not path.startswith(head):
            return None
        return f"{base}/{component}/{{bot_id}}{path[len(head):]}"

    return to_old


def bot_first_to_query(component: str) -> Callable[[str], str | None]:
    """For a group whose bot used to be a query parameter, not a segment.

    ``/openapi/v1/bots/{bot_id}/resources/stat`` came from
    ``/openapi/v1/bots/resources/stat?bot_id=…`` — the bot segment is simply
    dropped, and the shim puts it back in the query string.
    """

    base = "/openapi/v1/bots"
    head = f"{base}/{{bot_id}}/{component}"

    def to_old(path: str) -> str | None:
        if not path.startswith(head):
            return None
        return f"{base}/{component}{path[len(head):]}"

    return to_old
