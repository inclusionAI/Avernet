"""Execute the real SQLite/HTTP closed-default lifecycle, without mock providers."""

import pytest

from tests.community._flows.space_knowledge.api_lifecycle import SPACE_KNOWLEDGE_FLOWS
from tests.community.framework.flow_runner import run_flow


@pytest.mark.e2e
@pytest.mark.parametrize("case", SPACE_KNOWLEDGE_FLOWS, ids=lambda c: c.name)
def test_space_knowledge_closed_default_flow(case, app_with_testing_modules, world):
    assert run_flow(case, app_with_testing_modules, world) is not None
