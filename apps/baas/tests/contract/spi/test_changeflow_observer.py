"""Conformance contract for the community ChangeFlow observer stub."""

from __future__ import annotations

import pytest

from secbaas.community.plugins.changeflow.stub import NoopChangeFlowObserver
from secbaas.community.spi.changeflow import (
    ChangeFlowBatchContext,
    ChangeFlowRegisterContext,
    ChangeFlowTerminalContext,
)


@pytest.mark.asyncio
async def test_noop_observer_is_silent_and_returns_none():
    observer = NoopChangeFlowObserver()
    register = ChangeFlowRegisterContext(
        tenant="t",
        publish_id=1,
        publish_type="CREATE",
        bot_id=2,
        bot_name="bot",
        engine="teclaw",
        instance_count=1,
        environment="prod",
        candidate_version="v1",
        initiator="alice",
    )
    batch = ChangeFlowBatchContext(
        tenant="t",
        publish_id=1,
        change_id="chg",
        stage="PROD_FIRST_BATCH",
        batch_index=0,
        candidate_version="v1",
    )
    terminal = ChangeFlowTerminalContext(
        tenant="t", publish_id=1, change_id="chg", outcome="success"
    )

    assert await observer.on_register(register) is None
    assert await observer.on_pre_check(batch) is None
    assert await observer.on_batch_result(batch) is None
    assert await observer.on_publish_terminal(terminal) is None
