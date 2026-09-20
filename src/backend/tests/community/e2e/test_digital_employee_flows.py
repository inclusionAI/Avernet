"""Real application assembly and public-local employee compatibility."""
import pytest
from tests.community._flows.digital_employee.api_lifecycle import DIGITAL_EMPLOYEE_FLOWS
from tests.community.framework.flow_runner import run_flow

@pytest.mark.e2e
@pytest.mark.parametrize("case", DIGITAL_EMPLOYEE_FLOWS, ids=lambda case: case.name)
def test_disabled_employee_flow(case, app_with_testing_modules, world):
    run_flow(case, app_with_testing_modules, world)
