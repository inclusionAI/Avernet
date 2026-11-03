"""Released default: transfer stays closed and existing Bot reads still work."""

import pytest
from tests.community._flows.bot_management.api_lifecycle import (
    BOT_MANAGEMENT_LIFECYCLE_FLOWS,
)
from tests.community.framework.flow_runner_live import run_flow_live


@pytest.mark.acceptance
def test_owner_handoff_closed_live(live_backend, acceptance_fs_root):
    flow = next(
        case
        for case in BOT_MANAGEMENT_LIFECYCLE_FLOWS
        if case.name == "owner-handoff-disabled-preserves-existing-reads"
    )
    run_flow_live(
        flow,
        base_url=live_backend,
        fs_root=acceptance_fs_root,
        default_headers={"x-user-id": "e2e_user"},
    )
