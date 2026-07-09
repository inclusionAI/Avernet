"""skill_center e2e business flows (real endpoints, LOCAL+SQLITE, no mocks).

API_LIFECYCLE_FLOWS (tests/community/_flows/skill_center/api_lifecycle.py) is the single
source of truth for both this runner and the E3 coverage guard
(test_e2e_module_coverage.py), and for the route-B live executor.
"""
from __future__ import annotations

import pytest

from tests.community._flows.skill_center.api_lifecycle import API_LIFECYCLE_FLOWS as SKILL_CENTER_FLOWS
from tests.community.framework.flow_runner import run_flow

IN_PROCESS_SKILL_CENTER_FLOWS = [case for case in SKILL_CENTER_FLOWS if not case.live_only]


@pytest.mark.parametrize("case", IN_PROCESS_SKILL_CENTER_FLOWS, ids=lambda c: c.name)
def test_skill_center_flow(case, app_with_testing_modules, world):
    ctx = run_flow(case, app_with_testing_modules, world)
    assert ctx is not None
    # When a flow both creates a skill set and fetches it back, assert the id
    # round-trips explicitly (create's data.id == GET's data.id) rather than
    # leaning only on the 404-if-missing routing. Keyed on what the flow chose
    # to extract, so flows that don't do this pair are untouched.
    if "skill_set_id" in ctx and "fetched_id" in ctx:
        assert ctx["fetched_id"] == ctx["skill_set_id"]
