"""User-list E2E business flows over LOCAL SQLite."""
from __future__ import annotations

import pytest

from tests.community._flows.user_list.api_lifecycle import USER_LIST_FLOWS
from tests.community.framework.flow_runner import run_flow


@pytest.mark.e2e
@pytest.mark.parametrize("case", USER_LIST_FLOWS, ids=lambda case: case.name)
def test_user_list_flow(case, app_with_testing_modules, world):
    """Run the membership write/read lifecycle through the HTTP application."""
    ctx = run_flow(case, app_with_testing_modules, world)
    assert ctx is not None
