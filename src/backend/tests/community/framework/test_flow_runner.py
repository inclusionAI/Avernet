# src/backend/tests/framework/test_flow_runner.py
"""FlowRunner self-tests: real-endpoint chaining + precise failures.

Uses GET /api/health (no auth) to prove the runner drives real endpoints,
status/expect assertions, and that a failing step reports precisely.
Step-value passing is proven via _dig + interpolate unit checks here and
end-to-end in tests/e2e/test_smoke_flow.py (Task 6).
"""
from __future__ import annotations

from urllib.parse import parse_qs

import pytest
from fastapi import FastAPI, Request

from tests.community.framework.flow import FlowCase, FlowContext, FlowStep
from tests.community.framework.flow_runner import _dig, run_flow


def test_dig_follows_dotted_path():
    assert _dig({"data": {"bot_id": "x"}}, "data.bot_id") == "x"


def test_dig_missing_raises():
    with pytest.raises(KeyError):
        _dig({"data": {}}, "data.bot_id")


def test_run_flow_health_ok(app_with_testing_modules, world):
    case = FlowCase(name="health-only", covers=[], steps=[
        FlowStep(method="GET", path="/api/health", expect_status=200),
    ])
    ctx = run_flow(case, app_with_testing_modules, world)
    assert isinstance(ctx, FlowContext)


def test_run_flow_reports_failing_step(app_with_testing_modules, world):
    case = FlowCase(name="bad-status", covers=[], steps=[
        FlowStep(method="GET", path="/api/health", expect_status=599),  # wrong on purpose
    ])
    with pytest.raises(AssertionError, match=r"step 1 \(GET /api/health\)"):
        run_flow(case, app_with_testing_modules, world)


def test_run_flow_interpolates_query_params(world):
    app = FastAPI()

    @app.get("/echo")
    async def echo(request: Request):
        return {"probe": request.query_params["probe"]}

    ctx = FlowContext()
    ctx["value"] = "ok"
    case = FlowCase(
        name="query-params",
        covers=[],
        steps=[
            FlowStep(
                method="GET",
                path="/echo",
                query={"probe": "{value}"},
                expect={"probe": "ok"},
            ),
        ],
    )

    run_flow(case, app, world, initial_context=ctx)


def test_run_flow_interpolates_form_body(world):
    app = FastAPI()

    @app.post("/echo-form")
    async def echo_form(request: Request):
        parsed = parse_qs((await request.body()).decode())
        return {"decision": parsed["decision"][0]}

    ctx = FlowContext()
    ctx["decision"] = "approve"
    case = FlowCase(
        name="form-body",
        covers=[],
        steps=[
            FlowStep(
                method="POST",
                path="/echo-form",
                form={"decision": "{decision}"},
                expect={"decision": "approve"},
            ),
        ],
    )

    run_flow(case, app, world, initial_context=ctx)
