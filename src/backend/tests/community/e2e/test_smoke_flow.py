"""Smoke flow: proves the FlowRunner basework end-to-end (Plan B).

Flow: ``GET /api/health`` (liveness) → ``POST /api/v1/user`` (create a real
user, extract its ``userId`` from the response) → ``GET /api/v1/user/staff/{uid}``
(fetch that same user by the extracted id, interpolated into the path). The
final step's ``expect`` asserts the response echoes back the id we passed in,
so a broken extract/interpolation chain cannot pass silently.

FALLBACK NOTE: the plan's first-choice flow was health → create bot → fetch
bot. Empirically (scratch probe), a REAL ``POST /api/bots`` in LOCAL+SQLITE
returns ``401 {"detail":"Authentication required"}`` — it requires auth/Passport
that is unavailable without remote services — so create-bot cannot drive a
mock-free e2e flow here. Per the plan ("若确实无法在 local 跑通建 bot，缩减为
health→一个能在 local 真建实体并回 id 的端点→查该实体的等价流"), we use the
``/api/v1/user`` create→fetch pair instead: ``POST /api/v1/user`` performs a
real ``UserService.upsert_user`` DB write in LOCAL+SQLITE and returns the new
user's id, and ``GET /api/v1/user/{user_type}/{user_id}`` reads it back — an
equivalent create-entity-then-fetch-by-extracted-id flow that still proves
step-value passing.

THROWAWAY flow (covers=[]) — validates the basework, NOT a real business-flow
E1 (those are Plan C). Paths/body are REAL routes confirmed by grep + run; no
mocks or patches drive this flow.
"""
from __future__ import annotations

import pytest

from tests.community.framework.flow import FlowCase, FlowStep
from tests.community.framework.flow_runner import run_flow


def test_smoke_flow_user_create_then_fetch(app_with_testing_modules, world):
    case = FlowCase(
        name="smoke-user-create-then-fetch",
        covers=[],
        steps=[
            # 1) Liveness — no auth, no DB.
            FlowStep(
                method="GET",
                path="/api/health",
                expect_status=200,
                expect={"status": "ok"},
            ),
            # 2) Create a real user; pull its id out of the response.
            FlowStep(
                method="POST",
                path="/api/v1/user",
                body={"user_id": "u_smoke_flow", "user_type": "staff", "status": "active"},
                expect_status=200,
                expect={"success": True, "data": {"userId": "u_smoke_flow"}},
                extract={"user_id": "data.userId"},
            ),
            # 3) Fetch that user by the extracted id, interpolated into the path.
            #    ``expect`` re-asserts the id round-tripped, so a broken
            #    extract → {user_id} chain can't pass unnoticed.
            FlowStep(
                method="GET",
                path="/api/v1/user/staff/{user_id}",
                expect_status=200,
                expect={"success": True, "data": {"userId": "u_smoke_flow"}},
            ),
        ],
    )

    ctx = run_flow(case, app_with_testing_modules, world)

    # Proves the value was passed between steps: step 2 extracted it, step 3
    # consumed it via {user_id} interpolation (and step 3's expect confirmed
    # the fetched entity is the one we created).
    assert "user_id" in ctx
    assert ctx["user_id"] == "u_smoke_flow"
