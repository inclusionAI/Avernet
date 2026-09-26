"""Disabled integration compatibility against the live public singlebox."""
import pytest
from tests.community._flows.digital_employee.api_lifecycle import DIGITAL_EMPLOYEE_FLOWS
from tests.community.framework.flow_runner_live import run_flow_live

@pytest.mark.acceptance
@pytest.mark.parametrize("case", DIGITAL_EMPLOYEE_FLOWS, ids=lambda case: case.name)
def test_disabled_employee_flow(case, live_backend):
    run_flow_live(case, base_url=live_backend)
