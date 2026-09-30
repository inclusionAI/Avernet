"""A legacy address and its replacement must answer identically.

This is the compatibility promise the whole re-addressing rests on, and it is
the one thing no amount of care in the routers proves on its own. A legacy
operation is **not** an alias: it publishes the old parameter names in the old
locations and translates. That translation is code, and code drifts.

So each pair is driven twice — once at the address a client uses today, once at
the address it will use tomorrow — through the *same* application, and the two
responses are compared. Status, envelope ``code`` and ``message``, and ``data``.

Some legacy routes are re-registrations of the *same endpoint function* under
the old path, because ``bot_id`` is a path parameter in both shapes. It is
tempting to call those trivially equal and skip them. They are not: the two
registrations differ in the router they hang off, and therefore in the
mount-level dependencies and the ``admission.py`` entry that governs them —
exactly the things that decide whether an application caller is admitted. So
they are checked like any other pair.

Rather than a table of hand-written request pairs, the rows are **generated
from the registrations**: every legacy address knows the address that replaced
it, so the pair is already recorded and a hand-written table would only be a
second, driftable copy of it.

What each row asserts is that both addresses reach the same decision. The
requests here are unauthenticated, so both answer the surface's masked failure
— which is exactly the comparison worth making automatically, because it covers
every one of the forty-one addresses and catches the failure that actually
happens: a legacy route mounted with different dependencies, or missing from
``admission.py``, answering a *different* refusal than its replacement. A
difference there is a hole or a wall, and either is a broken promise.

Behavioural parity on the success path is asserted where the services are
stubbed and the answers are meaningful — the per-group suites — not here, where
a shared fixture for all forty-one would have to stub every service on the
surface at once and would prove less than it appeared to.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.community.adapters.http.openapi_v1.conftest import public_router
from agentclaw.community.adapters.http.openapi_v1.deprecated import LEGACY_ROUTES

from .conftest import mount_public_error_handlers


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(public_router())
    mount_public_error_handlers(app)
    return TestClient(app)


def _concrete(template: str) -> str:
    """A callable path for a template, every parameter filled with the same id.

    The same value in both halves of a pair, so a difference in the response is
    a difference in the *route*, never in what was addressed.
    """
    return "/".join(
        "x" if segment.startswith("{") else segment
        for segment in template.replace(":path", "").split("/")
    )


PAIRS = sorted(
    (method, legacy, replacement)
    for (method, legacy), replacement in LEGACY_ROUTES.items()
)


@pytest.mark.parametrize(
    ("method", "legacy", "replacement"),
    PAIRS,
    ids=[f"{m} {legacy}" for m, legacy, _ in PAIRS],
)
def test_a_legacy_address_refuses_exactly_as_its_replacement_does(
    method: str, legacy: str, replacement: str
) -> None:
    client = _client()
    old = client.request(method, _concrete(legacy))
    new = client.request(method, _concrete(replacement))

    assert old.status_code == new.status_code, (
        f"{method} {legacy} answered {old.status_code} where its replacement "
        f"{replacement} answered {new.status_code} — the two are mounted "
        "differently, or one is missing from admission.py"
    )
    assert old.json().get("code") == new.json().get("code")
    assert old.json().get("message") == new.json().get("message")


def test_every_legacy_route_names_a_replacement_that_exists() -> None:
    """A legacy address pointing at nothing would be a dead end in the document."""
    published = set(_client().app.openapi()["paths"])
    missing = sorted(
        replacement
        for replacement in LEGACY_ROUTES.values()
        if replacement.replace(":path", "") not in published
    )
    assert not missing, f"legacy addresses naming a replacement that is not served: {missing}"


def test_the_expected_number_of_addresses_are_retiring() -> None:
    """Thirty-nine re-addressed operations, the two engine-config ones, and
    the retiring GET spelling of the auth-status poll (now a POST).

    Pinned so that adding a legacy address, or losing one, is a number somebody
    has to look at rather than a silent change to what this API still answers.
    A newly introduced operation must not acquire a made-up retiring address.
    """
    assert len(LEGACY_ROUTES) == 42


def test_a_retiring_body_keeps_the_component_name_it_published() -> None:
    """A schema name is part of what a generated client is written against.

    Two request bodies changed shape in this feature — routines' create lost
    ``bot_id`` and the approvals write lost ``session_key``. Whichever model
    keeps the original *name* is the one an unmigrated client's regenerated SDK
    resolves to, so the retiring shape must keep it: otherwise the caller finds
    the type they still construct stripped of the field they still send, which
    is a break inside the window that exists to prevent exactly that.

    Asserted structurally rather than by naming the two, so the next body to
    change shape is covered without anyone remembering: a retiring operation
    whose body schema is named ``Legacy…`` has given the original name away.

    What this deliberately does *not* try to assert is that a retiring body and
    its replacement never share one component. Sharing is correct whenever the
    body did not change — most of them — and a component that is shared has one
    shape by definition, so the document alone cannot say whether the two ought
    to have differed. Comparing against the previously published document is
    what would answer that, and that lives in the release compat gate rather
    than here.
    """
    document = _client().app.openapi()

    def body_schema(method: str, path: str) -> str | None:
        operation = document["paths"].get(path, {}).get(method.lower())
        if not isinstance(operation, dict):
            return None
        content = (operation.get("requestBody") or {}).get("content") or {}
        for spec in content.values():
            ref = (spec.get("schema") or {}).get("$ref")
            if ref:
                return ref.rsplit("/", 1)[-1]
        return None

    prefixed = []
    for (method, legacy), _replacement in LEGACY_ROUTES.items():
        old = body_schema(method, legacy.replace(":path", ""))
        if old is not None and old.startswith("Legacy"):
            prefixed.append(f"{method} {legacy} -> {old}")

    assert not prefixed, (
        "these retiring bodies publish a renamed component, so a client that "
        "regenerates without migrating loses the type it constructs: "
        f"{prefixed}"
    )
