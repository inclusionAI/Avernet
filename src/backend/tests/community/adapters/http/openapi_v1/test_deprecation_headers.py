"""Every retiring address says so, and no current one does.

Two channels carry the same fact and both matter. ``deprecated: true`` in the
published document is what a person reads and what a generated client can
annotate; the ``Deprecation`` and ``Sunset`` response headers reach the client
that is already running, which is the one that has to change.

The asymmetry is the part worth guarding. A legacy address that forgets to say
it is legacy is a caller who never migrates; a *current* address that wrongly
says it is legacy is a caller who migrates away from the address they should be
using. Both are asserted.
"""

from __future__ import annotations

import re
from email.utils import parsedate_to_datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentclaw.community.adapters.http.openapi_v1 import build_public_router

from .conftest import mount_public_error_handlers
from agentclaw.community.adapters.http.openapi_v1.deprecated import LEGACY_ROUTES
from agentclaw.community.adapters.http.openapi_v1.middleware import (
    DEPRECATION,
    SUNSET,
    DeprecationHeaderMiddleware,
    sf_date,
)


def _document() -> dict:
    app = FastAPI()
    app.include_router(build_public_router())
    return app.openapi()


#: ``LEGACY_ROUTES`` holds route paths, which keep Starlette's ``:path``
#: converter; the published document strips it. Compared without it, so
#: ``/models/{bot_id}/{model_id:path}`` and ``/models/{bot_id}/{model_id}`` are
#: recognised as the one address they are.
_LEGACY = {(method, path.replace(":path", "")) for method, path in LEGACY_ROUTES}


def _published_operations(document: dict):
    for path, item in document["paths"].items():
        for method, operation in item.items():
            if isinstance(operation, dict) and "responses" in operation:
                yield method.upper(), path, operation


def test_every_legacy_operation_is_marked_deprecated_in_the_document() -> None:
    document = _document()
    undeclared = [
        f"{method} {path}"
        for method, path, operation in _published_operations(document)
        if (method, path) in _LEGACY and not operation.get("deprecated", False)
    ]
    assert not undeclared, (
        "these retiring addresses do not publish deprecated: true, so a client "
        f"generated from this document has no way to know: {undeclared}"
    )


def test_no_current_operation_is_marked_deprecated() -> None:
    """The addresses this API wants callers on must not tell them to leave."""
    document = _document()
    wrongly_marked = [
        f"{method} {path}"
        for method, path, operation in _published_operations(document)
        if (method, path) not in _LEGACY and operation.get("deprecated", False)
    ]
    assert not wrongly_marked, wrongly_marked


def test_every_legacy_address_answers_with_both_headers() -> None:
    """Asserted on the wire, not on the registration.

    The middleware reads the *matched route* off the scope, so this only holds
    if the route actually matched — which is the thing worth checking, and the
    thing a test of the registration alone would miss.
    """
    app = FastAPI()
    app.include_router(build_public_router())
    app.add_middleware(DeprecationHeaderMiddleware)
    mount_public_error_handlers(app)
    client = TestClient(app)

    missing: list[str] = []
    for method, path in sorted(LEGACY_ROUTES):
        # Every request here is unauthenticated and answers 401. That is the
        # point: the headers must ride on whatever the address returns, not
        # only on a success, or a client that is failing for another reason
        # never learns the address is going away.
        response = client.request(method, _concrete(path))
        if "deprecation" not in response.headers or "sunset" not in response.headers:
            missing.append(f"{method} {path} -> {response.status_code}")
    assert not missing, f"legacy addresses answering without the headers: {missing}"


def test_the_two_headers_use_their_own_spellings() -> None:
    """They are not the same format, and assuming they are is the trap here.

    ``Sunset`` (RFC 8594) is an IMF-fixdate HTTP-date. ``Deprecation`` (RFC 9745)
    is a Structured Fields ``sf-date`` — ``@`` and whole seconds since the epoch.
    The superseded ``draft-dalal-deprecation-header`` *did* use an HTTP-date, so
    an implementation written from memory of the draft emits a value RFC 9745
    parsers reject. This asserts the distinction on the wire, in both directions,
    so neither can drift into the other's format.
    """
    app = FastAPI()
    app.include_router(build_public_router())
    app.add_middleware(DeprecationHeaderMiddleware)
    mount_public_error_handlers(app)
    method, path = sorted(LEGACY_ROUTES)[0]
    response = TestClient(app).request(method, _concrete(path))

    deprecation = response.headers["deprecation"]
    assert deprecation == sf_date(DEPRECATION)
    assert deprecation.startswith("@"), deprecation
    assert int(deprecation[1:]) == int(DEPRECATION.timestamp())

    assert parsedate_to_datetime(response.headers["sunset"]) == SUNSET
    # …and specifically *not* the other one's format.
    assert not response.headers["sunset"].startswith("@")


def test_the_sunset_is_after_the_deprecation() -> None:
    """A window, not a date that has already passed."""
    assert SUNSET > DEPRECATION


def _concrete(template: str) -> str:
    """A callable path for a template, with every parameter filled in."""
    parts = [
        "x" if segment.startswith("{") else segment for segment in template.split("/")
    ]
    return "/".join(parts)


def test_no_two_operations_share_an_operation_id() -> None:
    """A duplicate id makes a generated client pick one of them at random.

    Real risk here rather than a formality: the retiring addresses keep the ids
    they published, and FastAPI's default rule replaces every non-word character
    with an underscore — under which ``…/engine-config`` and ``…/engine/config``
    collapse to the *same* string. That collision was introduced and caught by
    this assertion.
    """
    ids = [
        operation["operationId"]
        for _method, _path, operation in _published_operations(_document())
        if "operationId" in operation
    ]
    duplicated = sorted({name for name in ids if ids.count(name) > 1})
    assert not duplicated, f"operation ids serving more than one address: {duplicated}"


def test_a_retiring_address_keeps_its_own_operation_id() -> None:
    """The id a client's SDK was generated against must survive the window.

    Generators turn ``operationId`` into a method name, so changing it renames
    methods in every SDK built from this document — breaking a caller who has
    *not* migrated and regenerates for an unrelated reason. That is the one
    place a compatibility promise is least expected to fail.

    Asserted as "derived from its own address, not its replacement's": each
    retiring operation's id must match what FastAPI's own rule produces for the
    legacy path, which is what that address published before it was retired.
    """
    document = _document()
    wrong = []
    for method, path, operation in _published_operations(document):
        if (method, path) not in _LEGACY:
            continue
        # What FastAPI's rule contributes for *this* address. The endpoint name
        # is the prefix and varies, so the suffix is what identifies which path
        # the id was built from — and that is the whole question.
        suffix = re.sub(r"\W", "_", path) + f"_{method.lower()}"
        published = operation.get("operationId", "")
        if not published.endswith(suffix):
            wrong.append(f"{method} {path} -> {published} (expected to end {suffix})")
    assert not wrong, (
        "these retiring addresses publish an id that is not the one their own "
        "address produces, so an SDK generated before the migration finds its "
        f"method names changed: {wrong}"
    )
