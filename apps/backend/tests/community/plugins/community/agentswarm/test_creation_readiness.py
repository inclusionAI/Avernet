from types import SimpleNamespace

import pytest

from agentclaw.community.core.agentswarm.domain.models import BotState
from agentclaw.community.core.apply_engine.outcomes import ApplyStatus
from agentclaw.community.core.bot_config_manifest.creation import (
    CREATE_ON_CONTAINER_TRIGGER,
    CREATE_PRE_CONTAINER_TRIGGER,
)
from agentclaw.community.core.task_queue.types import TaskStatus
from agentclaw.community.plugins.community.agentswarm.host import AvernetBotPlatform


def make_platform(
    *,
    trigger: str,
    apply_status: ApplyStatus,
    job_status: TaskStatus,
    bot_status: str = "ACTIVE",
) -> AvernetBotPlatform:
    job = SimpleNamespace(status=job_status, last_error=None)
    report = SimpleNamespace(trigger=trigger, status=apply_status)
    return AvernetBotPlatform(
        manifest_seam=SimpleNamespace(find_job=lambda **kwargs: job),
        bot_service=SimpleNamespace(),
        passport_plugin=SimpleNamespace(),
        skill_set_factory=SimpleNamespace(),
        bot_repo=SimpleNamespace(
            get_by_id_and_entity=lambda *args: {"status": bot_status},
        ),
        apply_service=SimpleNamespace(last_apply=lambda **kwargs: report),
        space_context=SimpleNamespace(),
        baas_service=SimpleNamespace(),
    )


@pytest.mark.parametrize("job_status", [TaskStatus.PENDING, TaskStatus.RUNNING])
def test_pre_container_success_does_not_finish_a_live_creation(job_status):
    platform = make_platform(
        trigger=CREATE_PRE_CONTAINER_TRIGGER,
        apply_status=ApplyStatus.SUCCEEDED,
        job_status=job_status,
    )

    assert platform.bot_status("bot-1", owner_id="owner-1") == BotState.PROVISIONING.value


@pytest.mark.parametrize(
    "job_status,expected",
    [
        (TaskStatus.SUCCEEDED, BotState.READY),
        (TaskStatus.FAILED, BotState.APPLY_FAILED),
        (TaskStatus.TIMED_OUT, BotState.APPLY_FAILED),
    ],
)
def test_pre_container_success_preserves_the_terminal_job_verdict(job_status, expected):
    platform = make_platform(
        trigger=CREATE_PRE_CONTAINER_TRIGGER,
        apply_status=ApplyStatus.SUCCEEDED,
        job_status=job_status,
    )

    assert platform.bot_status("bot-1", owner_id="owner-1") == expected.value


@pytest.mark.parametrize("job_status", [TaskStatus.FAILED, TaskStatus.TIMED_OUT])
def test_pre_container_success_cannot_hide_a_failed_container_creation(job_status):
    platform = make_platform(
        trigger=CREATE_PRE_CONTAINER_TRIGGER,
        apply_status=ApplyStatus.SUCCEEDED,
        job_status=job_status,
        bot_status="PENDING",
    )

    assert platform.bot_status("bot-1", owner_id="owner-1") == BotState.CREATE_FAILED.value


@pytest.mark.parametrize("job_status", [TaskStatus.PENDING, TaskStatus.RUNNING])
def test_on_container_success_can_finish_before_the_job_is_settled(job_status):
    platform = make_platform(
        trigger=CREATE_ON_CONTAINER_TRIGGER,
        apply_status=ApplyStatus.SUCCEEDED,
        job_status=job_status,
    )

    assert platform.bot_status("bot-1", owner_id="owner-1") == BotState.READY.value


@pytest.mark.parametrize("trigger", [CREATE_PRE_CONTAINER_TRIGGER, CREATE_ON_CONTAINER_TRIGGER])
def test_a_running_creation_apply_keeps_polling(trigger):
    platform = make_platform(
        trigger=trigger,
        apply_status=ApplyStatus.RUNNING,
        job_status=TaskStatus.RUNNING,
    )

    assert platform.bot_status("bot-1", owner_id="owner-1") == BotState.PROVISIONING.value


@pytest.mark.parametrize("trigger", [CREATE_PRE_CONTAINER_TRIGGER, CREATE_ON_CONTAINER_TRIGGER])
@pytest.mark.parametrize("apply_status", [ApplyStatus.FAILED, ApplyStatus.PARTIAL])
def test_unsuccessful_creation_applies_keep_their_phase_verdict(trigger, apply_status):
    platform = make_platform(
        trigger=trigger,
        apply_status=apply_status,
        job_status=TaskStatus.RUNNING,
    )

    expected = (
        BotState.PROVISIONING
        if trigger == CREATE_PRE_CONTAINER_TRIGGER
        else BotState.APPLY_FAILED
    )
    assert platform.bot_status("bot-1", owner_id="owner-1") == expected.value


def test_on_container_success_still_requires_an_up_container():
    platform = make_platform(
        trigger=CREATE_ON_CONTAINER_TRIGGER,
        apply_status=ApplyStatus.SUCCEEDED,
        job_status=TaskStatus.RUNNING,
        bot_status="PENDING",
    )

    assert platform.bot_status("bot-1", owner_id="owner-1") == BotState.PROVISIONING.value
