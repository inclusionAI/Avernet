"""A live creation apply is provisioning, never a terminal failure."""

import types

import pytest

from agentclaw.community.core.agentswarm.domain.models import BotState
from agentclaw.community.core.apply_engine.outcomes import ApplyStatus
from agentclaw.community.core.apply_engine.triggers import (
    CREATE_ON_CONTAINER_TRIGGER,
    CREATE_PRE_CONTAINER_TRIGGER,
)
from agentclaw.community.core.task_queue.types import TaskStatus
from tests.community.plugins.community.agentswarm.test_agentswarm_platform import (
    _Apply,
    _Repo,
    _Seam,
    _job,
    _mk,
)


@pytest.mark.parametrize("trigger", [
    CREATE_PRE_CONTAINER_TRIGGER,
    CREATE_ON_CONTAINER_TRIGGER,
])
@pytest.mark.parametrize("bot_status", ["PENDING", "ACTIVE"])
def test_running_creation_apply_remains_provisioning_until_it_settles(
    trigger, bot_status,
):
    seam = _Seam()
    seam.jobs["b1"] = _job(TaskStatus.RUNNING)
    repo = _Repo(bot={"status": bot_status})
    report = types.SimpleNamespace(trigger=trigger, status=ApplyStatus.RUNNING)
    platform = _mk(seam=seam, repo=repo, apply_=_Apply(report))

    assert platform.bot_status("b1", owner_id="owner-1") == BotState.PROVISIONING.value

    report.status = ApplyStatus.SUCCEEDED
    expected = BotState.READY if bot_status == "ACTIVE" else BotState.PROVISIONING
    assert platform.bot_status("b1", owner_id="owner-1") == expected.value

    report.status = ApplyStatus.FAILED
    assert platform.bot_status("b1", owner_id="owner-1") == BotState.APPLY_FAILED.value
