"""Route-B acceptance: skill_center API lifecycle flows.

These flows run against a live singlebox backend. They intentionally use the
shared FlowCase definitions from tests/community/_flows/skill_center so each new user
story contributes to both in-process e2e and live singlebox coverage.
"""
import pytest

from tests.community._flows.skill_center.api_lifecycle import API_LIFECYCLE_FLOWS
from tests.community.framework.flow_runner_live import run_flow_live


@pytest.mark.acceptance
@pytest.mark.parametrize("case", API_LIFECYCLE_FLOWS, ids=lambda case: case.name)
def test_skill_center_api_lifecycle(case, live_backend):
    run_flow_live(case, base_url=live_backend)
