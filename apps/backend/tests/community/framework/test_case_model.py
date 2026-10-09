"""Unit tests for the declarative case dataclasses.

Pins the contract authors will rely on: defaults, frozen-ness, the
auto-computed ``id`` format, and the input/expectation shapes.
"""
from __future__ import annotations

import pytest

from tests.community.framework.case import (
    UNSET,
    CaseInput,
    EndpointCase,
    ExpectError,
    ExpectSuccess,
)


def test_case_input_defaults_are_empty() -> None:
    c = CaseInput()
    assert c.path_params == {}
    assert c.query_params == {}
    assert c.headers == {}
    assert c.json_body is None


def test_case_input_is_frozen() -> None:
    c = CaseInput()
    with pytest.raises((AttributeError, Exception)):
        # ``dataclasses`` raises FrozenInstanceError (subclass of AttributeError).
        c.path_params = {"x": 1}  # type: ignore[misc]


def test_expect_success_defaults() -> None:
    e = ExpectSuccess()
    assert e.status == 200
    assert e.json_equals is UNSET
    assert e.json_contains == {}


def test_expect_error_requires_status() -> None:
    e = ExpectError(status=404)
    assert e.status == 404
    assert e.json_contains == {}
    assert e.exception_type is None


def test_endpoint_case_auto_id() -> None:
    case = EndpointCase(
        method="GET",
        path="/api/v1/user/{user_type}/{user_id}",
        scenario="ok",
        expect=ExpectSuccess(),
    )
    assert case.id == "GET /api/v1/user/{user_type}/{user_id} :: ok"


def test_endpoint_case_is_frozen() -> None:
    case = EndpointCase(
        method="GET", path="/x", scenario="ok", expect=ExpectSuccess()
    )
    with pytest.raises(Exception):
        case.scenario = "nope"  # type: ignore[misc]


def test_endpoint_case_equality_ignores_id_field() -> None:
    """``id`` is derived from the other fields, so two cases with equal
    inputs must be ``==`` even though ``id`` is filled in
    post-construction. ``compare=False`` on the ``id`` field guarantees
    this; equality is structural over the declared inputs.
    """
    a = EndpointCase(method="GET", path="/x", scenario="ok", expect=ExpectSuccess())
    b = EndpointCase(method="GET", path="/x", scenario="ok", expect=ExpectSuccess())
    assert a == b


def test_endpoint_case_accepts_seed_and_extra_assertions() -> None:
    """The seed callable and the extra_assertions tuple are the two
    author-supplied hooks; verify they round-trip into the dataclass.
    """
    seen: list[str] = []

    def seed(_world) -> None:  # noqa: D401 — test helper
        seen.append("seed")

    def asserter(_response, _world) -> None:
        seen.append("assert")

    case = EndpointCase(
        method="POST",
        path="/x",
        scenario="ok",
        expect=ExpectSuccess(status=201),
        seed=seed,
        extra_assertions=(asserter,),
    )
    assert case.seed is seed
    assert case.extra_assertions == (asserter,)
    # Calling them directly is just to prove they remain invokable
    # references after dataclass construction — no behaviour tested here.
    case.seed(None)  # type: ignore[misc]
    case.extra_assertions[0](None, None)  # type: ignore[misc]
    assert seen == ["seed", "assert"]


def test_unset_sentinel_is_singleton_and_falsy() -> None:
    """Two references to ``UNSET`` must compare identical; ``UNSET`` is
    also intentionally falsy so a future ``if expect.json_equals:`` won't
    silently treat "unset" as "asserted empty dict/string". This pins the
    sentinel contract.
    """
    from tests.community.framework.case import _Unset

    assert UNSET is _Unset()
    assert not bool(UNSET)
