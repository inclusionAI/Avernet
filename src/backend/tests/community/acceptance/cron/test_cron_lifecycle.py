"""Route-B acceptance: cron CRUD reaches the live singlebox engine adapter."""
from __future__ import annotations

import httpx
import pytest

from tests.community._flows.cron.api_lifecycle import CRON_FLOWS
from tests.community.acceptance._fixtures.live_personal_bot import (
    create_live_personal_bot,
    fresh_id,
)
from tests.community.framework.flow import FlowContext
from tests.community.framework.flow_runner_live import run_flow_live


@pytest.mark.acceptance
def test_cron_crud_reaches_live_singlebox_engine(live_backend, acceptance_fs_root):
    user_id = fresh_id("e2e_cron_user")
    headers = {"x-user-id": user_id}

    with httpx.Client(base_url=live_backend, headers=headers, timeout=60.0) as client:
        bot = create_live_personal_bot(
            client,
            user_id=user_id,
            bot_name_prefix="Cron Acceptance",
            bot_desc="cron live adapter acceptance bot",
        )

    ctx = FlowContext()
    ctx["bot_id"] = bot["bot_id"]
    result_ctx = run_flow_live(
        CRON_FLOWS[0],
        base_url=live_backend,
        fs_root=acceptance_fs_root,
        default_headers=headers,
        initial_context=ctx,
    )

    assert result_ctx["cron_task_id"]

