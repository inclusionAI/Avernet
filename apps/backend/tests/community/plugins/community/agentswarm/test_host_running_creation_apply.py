"""Polling a live creation Apply must wait for its terminal report."""

from types import SimpleNamespace

import pytest

from agentclaw.community.core.agentswarm.domain.models import BotState
from agentclaw.community.core.apply_engine.outcomes import ApplyStatus
from agentclaw.community.core.bot_config_manifest.creation import (
    CREATE_ON_CONTAINER_TRIGGER,
    CREATE_PRE_CONTAINER_TRIGGER,
)
from agentclaw.community.core.task_queue.types import TaskStatus
from .test_agentswarm_platform import _Apply, _Repo, _Seam, _job, _mk


@pytest.mark.parametrize("trigger", [CREATE_PRE_CONTAINER_TRIGGER, CREATE_ON_CONTAINER_TRIGGER])
@pytest.mark.parametrize("bot_status", ["PENDING", "ACTIVE"])
@pytest.mark.parametrize("terminal", [ApplyStatus.SUCCEEDED, ApplyStatus.PARTIAL, ApplyStatus.FAILED])
def test_running_creation_apply_waits_then_serves_its_terminal_verdict(
    trigger, bot_status, terminal
):
    seam = _Seam()
    seam.jobs["b1"] = _job(TaskStatus.RUNNING)
    report = SimpleNamespace(trigger=trigger, status=ApplyStatus.RUNNING)
    platform = _mk(
        seam=seam, repo=_Repo(bot={"status": bot_status}), apply_=_Apply(report)
    )
    assert platform.bot_status("b1", owner_id="owner-1") == BotState.PROVISIONING.value
    report.status = terminal
    expected = (
        BotState.READY if bot_status == "ACTIVE" else BotState.PROVISIONING
    ) if terminal is ApplyStatus.SUCCEEDED else BotState.APPLY_FAILED
    assert platform.bot_status("b1", owner_id="owner-1") == expected.value
