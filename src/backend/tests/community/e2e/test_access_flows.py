"""access e2e business flows (real endpoints, LOCAL+SQLITE, no mocks).

ACCESS_LIFECYCLE_FLOWS (tests/_flows/access/api_lifecycle.py) is the single
source of truth for both this runner and the E3 coverage guard
(test_e2e_module_coverage.py), and for the route-B live executor.
"""
from __future__ import annotations

import pytest

from tests.community._flows.access.api_lifecycle import ACCESS_LIFECYCLE_FLOWS
from tests.community.framework.flow_runner import run_flow


@pytest.mark.e2e
@pytest.mark.parametrize("case", ACCESS_LIFECYCLE_FLOWS, ids=lambda c: c.name)
def test_access_flow(case, app_with_testing_modules, world):
    ctx = run_flow(case, app_with_testing_modules, world)
    assert ctx is not None
