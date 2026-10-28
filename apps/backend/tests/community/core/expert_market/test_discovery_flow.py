"""Run the registered discovery flow against actual scanner/DB/HTTP assembly."""

from tests.community._flows.expert_market.api_lifecycle import EXPERT_MARKET_FLOWS
from tests.community.endpoints.test_openapi_experts import (
    seed_expert,
    _principal,
    PRINCIPAL_HEADER,
)
from tests.community.framework.flow_runner import run_flow


def test_discovery_flow(app_with_testing_modules, world):
    seed_expert(world)
    from dataclasses import replace

    flow = EXPERT_MARKET_FLOWS[0]
    authenticated = replace(
        flow,
        steps=[
            replace(step, headers={PRINCIPAL_HEADER: _principal()})
            for step in flow.steps
        ],
    )
    result = run_flow(authenticated, app=app_with_testing_modules, world=world)
    assert result["expert_id"] == "writer"


from tests.community.framework.fixtures import app_with_testing_modules, world  # noqa: F401
