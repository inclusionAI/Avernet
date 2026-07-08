"""Internals tests for the runner — exercised against a stub ``FastAPI``
app, independent of the real backend's router graph.

The point is to pin the runner's behavior (path-param substitution,
expectation branches, ``extra_assertions`` ordering, subset semantics)
without dragging in the full app boot. The smoke test in
``tests/endpoints/`` (Task 11) covers the integration angle.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from injector import Injector

from tests.community.framework.case import (
    CaseInput,
    EndpointCase,
    ExpectError,
    ExpectSuccess,
)
from tests.community.framework.runner import _build_url, _is_subset, _run_case
from tests.community.framework.world import World


# ---------------------------------------------------------------------------
# Stub app fixture — independent of the real backend.
# ---------------------------------------------------------------------------
@pytest.fixture
def stub_app() -> FastAPI:
    app = FastAPI()

    @app.get("/echo/{name}")
    async def echo(name: str, suffix: str = ""):
        return {"name": name + suffix}

    @app.get("/raise404")
    async def raise404():
        # Surfaces as HTTP 200 with an error envelope — the project's
        # ApiResponse convention. The runner must handle ExpectError
        # against this shape.
        return JSONResponse(
            status_code=200,
            content={"success": False, "error_code": 404, "message": "not found"},
        )

    @app.get("/raise_500")
    async def raise_500():
        raise HTTPException(status_code=500, detail="boom")

    @app.post("/echo_body")
    async def echo_body(payload: dict):
        return {"received": payload}

    return app


@pytest.fixture
def stub_world() -> World:
    """A bare ``World`` — no real dependency graph, just a placeholder
    for the seed/assertion callable contract.
    """
    return World(Injector())


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
class TestBuildUrl:
    def test_no_params(self):
        assert _build_url("/x", {}) == "/x"

    def test_single_param(self):
        assert _build_url("/x/{id}", {"id": "abc"}) == "/x/abc"

    def test_multiple_params(self):
        assert _build_url(
            "/x/{a}/{b}", {"a": "1", "b": "2"}
        ) == "/x/1/2"

    def test_missing_param_raises_with_path_in_message(self):
        with pytest.raises(KeyError) as excinfo:
            _build_url("/x/{id}", {})
        assert "/x/{id}" in str(excinfo.value)


class TestIsSubset:
    def test_dict_subset(self):
        assert _is_subset({"a": 1}, {"a": 1, "b": 2})

    def test_dict_missing_key(self):
        assert not _is_subset({"a": 1}, {"b": 2})

    def test_dict_wrong_value(self):
        assert not _is_subset({"a": 1}, {"a": 2})

    def test_nested_dict(self):
        assert _is_subset({"a": {"b": 1}}, {"a": {"b": 1, "c": 2}, "d": 3})

    def test_list_member_match(self):
        assert _is_subset([{"id": 1}], [{"id": 1, "v": "x"}, {"id": 2}])

    def test_list_member_missing(self):
        assert not _is_subset([{"id": 9}], [{"id": 1}, {"id": 2}])

    def test_scalar_equality(self):
        assert _is_subset(5, 5)
        assert not _is_subset(5, 6)


# ---------------------------------------------------------------------------
# Runner sequence — happy path
# ---------------------------------------------------------------------------
def test_runner_happy_path_with_path_param(stub_app, stub_world):
    case = EndpointCase(
        method="GET",
        path="/echo/{name}",
        scenario="ok",
        input=CaseInput(path_params={"name": "world"}),
        expect=ExpectSuccess(status=200, json_equals={"name": "world"}),
    )
    _run_case(case, stub_app, stub_world)


def test_runner_uses_query_params(stub_app, stub_world):
    case = EndpointCase(
        method="GET",
        path="/echo/{name}",
        scenario="with_suffix",
        input=CaseInput(path_params={"name": "hi"}, query_params={"suffix": "!"}),
        expect=ExpectSuccess(status=200, json_equals={"name": "hi!"}),
    )
    _run_case(case, stub_app, stub_world)


def test_runner_sends_json_body(stub_app, stub_world):
    case = EndpointCase(
        method="POST",
        path="/echo_body",
        scenario="ok",
        input=CaseInput(json_body={"k": "v"}),
        expect=ExpectSuccess(status=200, json_contains={"received": {"k": "v"}}),
    )
    _run_case(case, stub_app, stub_world)


# ---------------------------------------------------------------------------
# Runner sequence — error branch
# ---------------------------------------------------------------------------
def test_runner_expect_error_envelope_on_200(stub_app, stub_world):
    """The project's error convention: HTTP 200 with envelope-shaped
    failure. ExpectError pins this shape via json_contains.
    """
    case = EndpointCase(
        method="GET",
        path="/raise404",
        scenario="not_found",
        expect=ExpectError(
            status=200,
            json_contains={"success": False, "error_code": 404},
        ),
    )
    _run_case(case, stub_app, stub_world)


def test_runner_expect_error_at_http_5xx(stub_app, stub_world):
    case = EndpointCase(
        method="GET",
        path="/raise_500",
        scenario="boom",
        expect=ExpectError(status=500),
    )
    _run_case(case, stub_app, stub_world)


# ---------------------------------------------------------------------------
# Hooks: seed, extra_assertions
# ---------------------------------------------------------------------------
def test_runner_invokes_seed_before_request(stub_app, stub_world):
    seen: list[str] = []

    def seed(_world):
        seen.append("seed")

    def assertion(_resp, _world):
        seen.append("assert")

    case = EndpointCase(
        method="GET",
        path="/echo/{name}",
        scenario="ok",
        input=CaseInput(path_params={"name": "x"}),
        expect=ExpectSuccess(status=200),
        seed=seed,
        extra_assertions=(assertion,),
    )
    _run_case(case, stub_app, stub_world)
    # seed must run before the assertion (which runs after the request).
    assert seen == ["seed", "assert"]


def test_runner_invokes_extra_assertions_in_order(stub_app, stub_world):
    order: list[int] = []

    case = EndpointCase(
        method="GET",
        path="/echo/{name}",
        scenario="ok",
        input=CaseInput(path_params={"name": "x"}),
        expect=ExpectSuccess(status=200),
        extra_assertions=(
            lambda r, w: order.append(1),
            lambda r, w: order.append(2),
            lambda r, w: order.append(3),
        ),
    )
    _run_case(case, stub_app, stub_world)
    assert order == [1, 2, 3]


def test_runner_propagates_extra_assertion_failure(stub_app, stub_world):
    """A failing extra_assertion must surface as the test failure, not
    be swallowed.
    """
    def fails(_resp, _world):
        raise AssertionError("intentional")

    case = EndpointCase(
        method="GET",
        path="/echo/{name}",
        scenario="ok",
        input=CaseInput(path_params={"name": "x"}),
        expect=ExpectSuccess(status=200),
        extra_assertions=(fails,),
    )
    with pytest.raises(AssertionError, match="intentional"):
        _run_case(case, stub_app, stub_world)


# ---------------------------------------------------------------------------
# Expectation mismatch surfaces clearly
# ---------------------------------------------------------------------------
def test_runner_wrong_status_fails_with_message(stub_app, stub_world):
    case = EndpointCase(
        method="GET",
        path="/echo/{name}",
        scenario="ok",
        input=CaseInput(path_params={"name": "x"}),
        expect=ExpectSuccess(status=201),  # endpoint returns 200
    )
    with pytest.raises(AssertionError, match="expected status 201"):
        _run_case(case, stub_app, stub_world)


def test_runner_json_equals_mismatch_fails(stub_app, stub_world):
    case = EndpointCase(
        method="GET",
        path="/echo/{name}",
        scenario="ok",
        input=CaseInput(path_params={"name": "x"}),
        expect=ExpectSuccess(status=200, json_equals={"name": "different"}),
    )
    with pytest.raises(AssertionError, match="json_equals mismatch"):
        _run_case(case, stub_app, stub_world)


def test_runner_json_contains_mismatch_fails(stub_app, stub_world):
    case = EndpointCase(
        method="GET",
        path="/echo/{name}",
        scenario="ok",
        input=CaseInput(path_params={"name": "x"}),
        expect=ExpectSuccess(status=200, json_contains={"name": "different"}),
    )
    with pytest.raises(AssertionError, match="json_contains not satisfied"):
        _run_case(case, stub_app, stub_world)
