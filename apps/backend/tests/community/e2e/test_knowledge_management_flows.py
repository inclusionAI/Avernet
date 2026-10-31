"""Execute management admission through the real community composition."""

import pytest

from tests.community._flows.knowledge_management.api_lifecycle import (
    KNOWLEDGE_MANAGEMENT_FLOWS,
)
from tests.community.framework.flow_runner import run_flow


@pytest.mark.e2e
@pytest.mark.parametrize("case", KNOWLEDGE_MANAGEMENT_FLOWS, ids=lambda c: c.name)
def test_management_closed_default(case, app_with_testing_modules, world):
    assert run_flow(case, app_with_testing_modules, world) is not None
