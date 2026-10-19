from __future__ import annotations

import asyncio
import pytest

from agentclaw.community.core.task.domain.errors import TaskNotFoundError, TaskStateError
from agentclaw.community.core.task.domain.intake import (
    IntakeTarget, IntakeTargetType, TaskIntakeAction, TaskIntakeCommand,
    TaskIntakeContext, TaskIntakeStatus,
)
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria, Context, Goal, TaskSourceType, TaskSpec,
)
from agentclaw.community.core.task.task_center.task_intake import TaskIntakeHandler


class _Repository:
    def __init__(self):
        self.saved = {}

    def create(self, result):
        self.saved[result.session_id] = result
        return result

    def get(self, session_id):
        return self.saved.get(session_id)

    def save(self, previous, updated):
        assert self.saved[previous.session_id] == previous
        self.saved[previous.session_id] = updated
        return updated


class _Resolver:
    async def resolve(self, requested, message, context, *, allow_external=False):
        return requested or IntakeTarget(IntakeTargetType.BOT, "bot-1")

    def resolve_a2a_proxy(self, requested_ref, context):
        return f"{requested_ref.partition(':')[0]}:{context.owner_user_id}"


class _Skill:
    def __init__(self):
        self.calls = 0

    async def recognize(
        self, message, target, owner_user_id, *, a2a_proxy_bot_id=None,
        intake_session_id=None,
    ):
        self.calls += 1
        return TaskSpec(
            context=Context(title="上线模型", extend_props={}),
            goal=Goal(objective=message, acceptances=[]),
        ), []

    async def clarify(
        self, existing_spec, message, target, owner_user_id, *,
        a2a_proxy_bot_id=None, intake_session_id=None,
    ):
        self.calls += 1
        return TaskSpec(
            context=Context(title=existing_spec.context.title, extend_props={
                "deliverables": ["上线记录"], "constraints": [],
            }),
            goal=Goal(objective=existing_spec.goal.objective, acceptances=[
                AcceptanceCriteria(id="", description="灰度达到 10%"),
            ]),
        ), []

    async def resume_pending(self, result):
        return None


def _context(session_id=None, owner="owner-1"):
    return TaskIntakeContext(session_id=session_id, owner_user_id=owner, source_type=TaskSourceType.API)


def _run(coro):
    return asyncio.run(coro)


def test_clarify_confirm_resume_without_creating_task() -> None:
    skill = _Skill()
    handler = TaskIntakeHandler(_Repository(), skill, _Resolver())
    started = _run(handler.intake(TaskIntakeCommand(TaskIntakeAction.START, "上线模型"), _context()))
    assert started.status == TaskIntakeStatus.CLARIFYING
    assert started.missing_fields == ["deliverables", "acceptances", "constraints"]
    assert started.questions

    context = _context(started.session_id)
    clarified = _run(handler.intake(TaskIntakeCommand(TaskIntakeAction.CONTINUE, "补充验收"), context))
    assert clarified.missing_fields == []
    assert clarified.questions == []
    assert clarified.task_spec.goal.acceptances[0].id == "ac1"
    confirmed = _run(handler.intake(TaskIntakeCommand(TaskIntakeAction.CONFIRM), context))
    assert confirmed.status == TaskIntakeStatus.CONFIRMED
    assert _run(handler.intake(TaskIntakeCommand(TaskIntakeAction.CONFIRM), context)) == confirmed
    assert _run(handler.intake(TaskIntakeCommand(TaskIntakeAction.RESUME), context)) == confirmed
    assert skill.calls == 2
    assert not hasattr(confirmed, "task_id")
    with pytest.raises(TaskStateError, match="terminal"):
        _run(handler.intake(TaskIntakeCommand(TaskIntakeAction.CANCEL), context))


def test_intake_session_is_owner_scoped() -> None:
    handler = TaskIntakeHandler(_Repository(), _Skill(), _Resolver())
    started = _run(handler.intake(TaskIntakeCommand(TaskIntakeAction.START, "目标"), _context()))
    with pytest.raises(TaskNotFoundError):
        _run(handler.intake(TaskIntakeCommand(TaskIntakeAction.RESUME), _context(started.session_id, "other")))


def test_incomplete_spec_cannot_be_confirmed() -> None:
    handler = TaskIntakeHandler(_Repository(), _Skill(), _Resolver())
    started = _run(handler.intake(TaskIntakeCommand(TaskIntakeAction.START, "目标"), _context()))
    with pytest.raises(TaskStateError, match="incomplete"):
        _run(handler.intake(TaskIntakeCommand(TaskIntakeAction.CONFIRM), _context(started.session_id)))
