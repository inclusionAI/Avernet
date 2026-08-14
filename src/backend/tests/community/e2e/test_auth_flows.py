"""Auth flows through the in-process singlebox application."""
from __future__ import annotations

import pytest

from tests.community._flows.auth.api_lifecycle import AUTH_LIFECYCLE_FLOWS
from tests.community.framework.flow_runner import run_flow


@pytest.mark.e2e
@pytest.mark.parametrize("case", AUTH_LIFECYCLE_FLOWS, ids=lambda case: case.name)
def test_auth_flow(case, app_with_testing_modules, world):
    context = run_flow(case, app_with_testing_modules, world)
    assert context is not None
