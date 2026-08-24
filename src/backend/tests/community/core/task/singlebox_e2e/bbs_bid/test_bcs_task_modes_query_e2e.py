"""Live Singlebox query of the global ``bcs_bots`` task-mode candidate pool.

Run after starting Singlebox::

    SINGLEBOX_TASK_E2E=1 SINGLEBOX_USER_ID=146836 \
      .venv/bin/python -m pytest \
      tests/community/core/task/singlebox_e2e/bbs_bid/test_bcs_task_modes_query_e2e.py -s
"""

from __future__ import annotations

import asyncio
import os

import pytest

from agentclaw.community.core.task.task_runner.integration.bcs_http_adapter import (
    BotTaskModeRoster,
)
from agentclaw.community.di.modules.task_module import TaskModule


_LIVE = os.environ.get("SINGLEBOX_TASK_E2E", "").strip().lower() in {"1", "true"}


@pytest.mark.skipif(not _LIVE, reason="requires a running Singlebox stack")
def test_singlebox_queries_global_bcs_bots_by_task_modes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEPLOY_PROFILE", "singlebox")
    bot, bcs = TaskModule._resolve_ports()
    assert bot is not None
    assert bcs is not None

    async def query() -> tuple[list[BotTaskModeRoster], list[BotTaskModeRoster]]:
        try:
            return (
                await bcs.list_bots_by_task_modes(),
                await bcs.list_bots_by_task_modes(
                    claim=False,
                    dream=False,
                    match="all",
                ),
            )
        finally:
            await bot._aclose()

    all_bots, disabled_bots = asyncio.run(query())

    assert all_bots, "BCS task-mode API returned no physical Bots"
    assert all(isinstance(item, BotTaskModeRoster) for item in all_bots)
    assert all(item.env == "local" for item in all_bots)
    assert all(
        not item.task_claim_mode and not item.task_dream_mode
        for item in disabled_bots
    )
    print(
        f"[task-modes] all={len(all_bots)} "
        f"claim_false_dream_false={len(disabled_bots)}"
    )
