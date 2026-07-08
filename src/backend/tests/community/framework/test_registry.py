"""Unit tests for the registry + ``@endpoint_test`` decorator.

Pins three contracts:
- registration is purely side-effecting at import time (no central list
  to edit);
- duplicate ``(method, path, scenario)`` is a hard error with a useful
  message;
- the decorator returns the original function object so IDE navigation
  and naming continue to work.
"""
from __future__ import annotations

import pytest

from tests.community.framework.case import ExpectError, ExpectSuccess
from tests.community.framework.registry import (
    ENDPOINT_CASES,
    _reset_registry_for_tests,
    endpoint_test,
)


@pytest.fixture(autouse=True)
def _isolated_registry():
    """Snapshot + restore the registry around each test so framework
    self-tests don't leak cases into the suite's real run.
    """
    saved_cases = list(ENDPOINT_CASES)
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()
    ENDPOINT_CASES.extend(saved_cases)


def test_decorator_appends_to_registry() -> None:
    @endpoint_test(method="GET", path="/x", scenario="ok", expect=ExpectSuccess())
    def case_ok():  # body intentionally empty
        pass

    assert len(ENDPOINT_CASES) == 1
    c = ENDPOINT_CASES[0]
    assert c.method == "GET" and c.path == "/x" and c.scenario == "ok"
    assert c.id == "GET /x :: ok"


def test_two_scenarios_for_same_route_both_register() -> None:
    @endpoint_test(method="GET", path="/x", scenario="ok", expect=ExpectSuccess())
    def case_ok():
        pass

    @endpoint_test(method="GET", path="/x", scenario="not_found", expect=ExpectError(status=404))
    def case_404():
        pass

    assert {c.scenario for c in ENDPOINT_CASES} == {"ok", "not_found"}


def test_duplicate_raises_with_both_sites() -> None:
    @endpoint_test(method="GET", path="/x", scenario="ok", expect=ExpectSuccess())
    def case_one():
        pass

    with pytest.raises(ValueError) as excinfo:
        @endpoint_test(method="GET", path="/x", scenario="ok", expect=ExpectSuccess())
        def case_two():
            pass

    msg = str(excinfo.value)
    assert "GET /x :: ok" in msg
    # Both registration locations should appear in the message so the
    # author can find the conflict without grepping.
    assert "first registered at:" in msg
    assert "also registered at:" in msg


def test_decorator_returns_original_function() -> None:
    def fn():
        return "untouched"

    decorated = endpoint_test(
        method="GET", path="/x", scenario="ok", expect=ExpectSuccess()
    )(fn)
    assert decorated is fn
    assert decorated() == "untouched"


def test_function_body_is_not_invoked_by_registration() -> None:
    """The framework's "no lying annotations" guarantee depends on the
    decorator never calling the wrapped function. Verify that at
    registration time the body has not been executed.
    """
    invocations: list[str] = []

    @endpoint_test(method="GET", path="/x", scenario="ok", expect=ExpectSuccess())
    def case_ok():
        invocations.append("called")

    assert invocations == []  # registration alone must not invoke


def test_distinct_routes_register_independently() -> None:
    @endpoint_test(method="GET", path="/a", scenario="ok", expect=ExpectSuccess())
    def case_a():
        pass

    @endpoint_test(method="POST", path="/a", scenario="ok", expect=ExpectSuccess(status=201))
    def case_b():
        pass

    @endpoint_test(method="GET", path="/b", scenario="ok", expect=ExpectSuccess())
    def case_c():
        pass

    assert {(c.method, c.path) for c in ENDPOINT_CASES} == {
        ("GET", "/a"),
        ("POST", "/a"),
        ("GET", "/b"),
    }
