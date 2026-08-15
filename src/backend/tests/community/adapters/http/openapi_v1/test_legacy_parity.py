"""A legacy address and its replacement must answer identically.

This is the compatibility promise the whole re-addressing rests on, and it is
the one thing no amount of care in the routers proves on its own. A legacy
operation is **not** an alias: it publishes the old parameter names in the old
locations and translates. That translation is code, and code drifts.

So each pair is driven twice — once at the address a client uses today, once at
the address it will use tomorrow — through the *same* application, and the two
responses are compared. Status, envelope ``code`` and ``message``, and ``data``.

Two shapes of legacy route, and the distinction matters for what this catches:

- Eighteen operations are re-registrations of the *same endpoint function* under
  the old path, because ``bot_id`` is a path parameter in both shapes. It is
  tempting to call those trivially equal and skip them. They are not: the two
  registrations differ in the router they hang off, and therefore in the
  mount-level dependencies (`_GRANT_CHECKED`) and the ``admission.py`` entry
  that governs them. Those are exactly the things that decide whether an
  application caller is admitted, so they are tested like any other pair.
- The rest are real shims with their own signature, where the translation is
  the thing under test.

``PAIRS`` fills in as each group lands. ``test_every_legacy_route_has_a_row``
is what makes a missing row loud rather than invisible: once the deprecated
package exists it asserts the table covers every route the package publishes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient


@dataclass(frozen=True)
class LegacyPair:
    """One legacy address and the new address that replaces it.

    ``client_factory`` builds the application both halves run against. It is
    per-pair rather than a shared fixture because each group stubs its own
    services, and a single application wired for all of them would be a fixture
    nobody could read — and would let one group's stub satisfy another group's
    assertion.
    """

    method: str
    legacy_path: str
    new_path: str
    client_factory: Callable[[], TestClient]
    #: Query parameters as the *legacy* address takes them (e.g. ``bot_id``).
    legacy_params: dict[str, Any] = field(default_factory=dict)
    #: Query parameters as the *new* address takes them; usually the legacy set
    #: minus whatever moved into the path.
    new_params: dict[str, Any] = field(default_factory=dict)
    legacy_json: Any = None
    new_json: Any = None
    content: bytes | None = None
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return f"{self.method} {self.legacy_path} -> {self.new_path}"


def _call(client: TestClient, method: str, path: str, params, json, content, headers):
    kwargs: dict[str, Any] = {"params": params, "headers": headers or None}
    if json is not None:
        kwargs["json"] = json
    if content is not None:
        kwargs["content"] = content
    return client.request(method, path, **{k: v for k, v in kwargs.items() if v is not None})


def assert_parity(pair: LegacyPair) -> None:
    """Drive both addresses and compare what a client would see."""
    legacy = _call(
        pair.client_factory(),
        pair.method,
        pair.legacy_path,
        pair.legacy_params,
        pair.legacy_json,
        pair.content,
        pair.headers,
    )
    new = _call(
        pair.client_factory(),
        pair.method,
        pair.new_path,
        pair.new_params or pair.legacy_params,
        pair.new_json if pair.new_json is not None else pair.legacy_json,
        pair.content,
        pair.headers,
    )

    assert legacy.status_code == new.status_code, (
        f"{pair.id}: legacy answered {legacy.status_code}, new answered "
        f"{new.status_code}"
    )
    legacy_body, new_body = legacy.json(), new.json()
    for key in ("code", "message", "data"):
        assert legacy_body.get(key) == new_body.get(key), (
            f"{pair.id}: envelope {key!r} differs — legacy "
            f"{legacy_body.get(key)!r}, new {new_body.get(key)!r}"
        )


#: Populated group by group as each set of addresses lands.
PAIRS: list[LegacyPair] = []


@pytest.mark.parametrize("pair", PAIRS, ids=lambda pair: pair.id)
def test_legacy_answers_exactly_as_the_new_address_does(pair: LegacyPair) -> None:
    assert_parity(pair)


def test_every_legacy_route_has_a_row() -> None:
    """The table covers every address the deprecated package publishes.

    Skipped until that package exists, so this file is green from the moment it
    lands and becomes the completeness check the moment there is something to
    be complete about.
    """
    try:
        from agentclaw.community.adapters.http.openapi_v1.deprecated import (
            LEGACY_ROUTES,
        )
    except ImportError:
        pytest.skip("the deprecated package has not landed yet")

    covered = {(pair.method.upper(), pair.legacy_path) for pair in PAIRS}
    missing = {
        route
        for route in LEGACY_ROUTES
        if not any(_matches(route, call) for call in covered)
    }
    assert not missing, f"legacy addresses with no parity row: {sorted(missing)}"


def _matches(route: tuple[str, str], call: tuple[str, str]) -> bool:
    """Whether a concrete call exercises a route template.

    Rows call real addresses (``/openapi/v1/bots/sessions/b-1``);
    ``LEGACY_ROUTES`` holds templates (``/openapi/v1/bots/sessions/{bot_id}``).
    A ``{…}`` segment matches whatever the row put there; every other segment
    matches itself.
    """
    method, template = route
    called_method, called = call
    if method != called_method:
        return False
    pattern, parts = template.split("/"), called.split("/")
    return len(pattern) == len(parts) and all(
        expected.startswith("{") or expected == actual
        for expected, actual in zip(pattern, parts)
    )
