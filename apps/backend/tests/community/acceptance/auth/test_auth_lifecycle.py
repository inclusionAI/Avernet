"""Auth flows against the real singlebox backend process."""
from __future__ import annotations

import pytest

from tests.community._flows.auth.api_lifecycle import AUTH_LIFECYCLE_FLOWS
from tests.community.framework.flow_runner_live import run_flow_live


@pytest.mark.acceptance
@pytest.mark.parametrize("case", AUTH_LIFECYCLE_FLOWS, ids=lambda case: case.name)
def test_auth_lifecycle_live(case, live_backend, acceptance_fs_root):
    context = run_flow_live(
        case,
        base_url=live_backend,
        fs_root=acceptance_fs_root,
        default_headers={"x-user-id": "e2e_user"},
    )
    assert context is not None
