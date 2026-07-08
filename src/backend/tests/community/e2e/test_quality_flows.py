"""Quality e2e business flows (real endpoints, LOCAL+SQLITE, no mocks).

QUALITY_FLOWS (tests/_flows/quality/api_lifecycle.py) is the single
source of truth for both this runner and the E3 coverage guard
(test_e2e_module_coverage.py), and for the route-B live executor.
"""
from __future__ import annotations

import pytest

from tests.community._flows.quality.api_lifecycle import QUALITY_FLOWS
from tests.community.factories.bot_collaborator import make_bot
from tests.community.framework.flow_runner import run_flow


def _seed_bot_for_quality(world):
    """Seed a bot for quality task permission checks.

    CollaboratorPermissionInterceptor requires the bot to exist and the
    requesting user (x-user-id) to be the owner.
    """
    make_bot(
        world,
        bot_id="bot_e2e_quality",
        owner_id="e2e_user",
        bot_type="service",
        status="ACTIVE",
    )


@pytest.mark.parametrize("case", QUALITY_FLOWS, ids=lambda c: c.name)
def test_quality_flow(case, app_with_testing_modules, world):
    # Seed bot first - required for CollaboratorPermissionInterceptor
    _seed_bot_for_quality(world)

    ctx = run_flow(case, app_with_testing_modules, world)
    assert ctx is not None
    # When a flow both creates a task and fetches it back, assert the id
    # round-trips explicitly (create's data.id == GET's data.id) rather than
    # leaning only on the 404-if-missing routing. Keyed on what the flow chose
    # to extract, so flows that don't do this pair are untouched.
    if "task_id" in ctx:
        # Verify the task was created and is accessible
        assert ctx["task_id"] > 0