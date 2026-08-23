"""Singlebox BCS task-mode roster query integration test.

This test validates the real Backend BCS client path, including the lazy
Singlebox Provider bootstrap added to ``SingleboxBcsAdapter``::

    await bcs.list_bots_by_task_modes(claim=True, dream=True, match="all")

Run after starting the local Singlebox stack::

    SINGLEBOX_TASK_E2E=1 DEPLOY_PROFILE=singlebox \
      .venv/bin/python -m pytest \
      tests/community/core/task/singlebox_e2e/bbs_bid/test_bcs_task_modes_query_e2e.py -s

An empty roster is a valid result when no Provider Bot has been registered or
no Provider Bot has both task modes enabled. The test fails on transport,
authentication, Provider bootstrap, or response-shape errors.
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
def test_singlebox_bcs_lists_claim_and_dream_bots() -> None:
    os.environ.setdefault("DEPLOY_PROFILE", "singlebox")
    bot, bcs = TaskModule._resolve_ports()
    assert bot is not None
    assert bcs is not None

    async def run_query() -> list[BotTaskModeRoster]:
        return await bcs.list_bots_by_task_modes(
            claim=True,
            dream=True,
            match="all",
        )

    try:
        roster = asyncio.run(run_query())
    finally:
        asyncio.run(bot._aclose())

    assert isinstance(roster, list)
    assert all(isinstance(item, BotTaskModeRoster) for item in roster)
    assert all(item.task_claim_mode and item.task_dream_mode for item in roster)
    print(f"[task-modes] provider_id={bcs.provider_id} roster_size={len(roster)}")
