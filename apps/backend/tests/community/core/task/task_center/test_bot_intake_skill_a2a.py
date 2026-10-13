from __future__ import annotations

import asyncio

from agentclaw.community.core.task.domain.intake import (
    IntakeTarget,
    IntakeTargetType,
    TaskIntakeAction,
    TaskIntakeCommand,
    TaskIntakeContext,
)
from agentclaw.community.core.task.domain.models import TaskSourceType
from agentclaw.community.core.task.task_center.bot_intake_skill import BotTaskIntakeSkill
from agentclaw.community.core.task.task_center.task_intake import TaskIntakeHandler
from agentclaw.community.core.task.task_runner.client.ports import BotSendResult


class _Repository:
    def __init__(self) -> None:
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
    def resolve_a2a_proxy(self, requested_ref, context):
        return f"{requested_ref}:{context.owner_user_id}"

    async def resolve(self, requested, message, context, *, allow_external=False):
        assert allow_external is True
        return requested


class _BotService:
    def get_bot(self, bot_id, owner_id):
        return {"bot_id": bot_id, "owner_id": owner_id, "status": "ACTIVE"}


class _UnusedBot:
    async def send_and_wait_async(self, **kwargs):
        raise AssertionError("A2A intake must not use the direct Bot transport")


class _Tokens:
    def get_token(self, bot_id):
        assert bot_id == "proxy:owner-1"
        return "secret-token"


class _Bcs:
    def __init__(self, states=None):
        self.states = list(states or ["completed"])
        self.sent = []

    async def send_message_a2a(self, **kwargs):
        self.sent.append(kwargs)
        return BotSendResult(run_id="run-1", session_id="bcs-session-1")

    async def get_chat_run(self, run_id, **kwargs):
        state = self.states.pop(0) if self.states else "completed"
        payload = {
            "run_id": run_id,
            "state": state,
            "version": 1,
            "response": {"content": ""},
        }
        if state == "completed":
            payload["response"] = {"content": """
            {"task_spec":{"context":{"title":"分析支付失败率","background":"",
            "extend_props":{"deliverables":["分析报告"],"constraints":[]}},
            "goal":{"objective":"分析支付失败率","acceptances":[
            {"id":"ac1","description":"给出失败原因"}]}},"questions":[]}
            """}
        return payload


def _run(coro):
    return asyncio.run(coro)


def test_a2a_intake_uses_proxy_identity_and_target_bot() -> None:
    repo = _Repository()
    bcs = _Bcs()
    skill = BotTaskIntakeSkill(
        _UnusedBot(), _BotService(), bcs=bcs, bot_tokens=_Tokens(),
        intake_repository=repo,
    )
    handler = TaskIntakeHandler(repo, skill, _Resolver())

    result = _run(handler.intake(
        TaskIntakeCommand(
            TaskIntakeAction.START,
            "分析支付失败率",
            requested_a2a_proxy_bot_ref="proxy",
            requested_target=IntakeTarget(IntakeTargetType.BOT, "risk:owner-2"),
        ),
        TaskIntakeContext(None, "owner-1", TaskSourceType.API),
    ))

    assert result.target.target_id == "risk:owner-2"
    assert result.a2a_proxy_bot_id == "proxy:owner-1"
    assert result.a2a_run_id is None
    assert result.task_spec.goal.objective == "分析支付失败率"
    assert bcs.sent[0]["target_bot_id"] == "risk"
    assert bcs.sent[0]["target_user_id"] == "owner-2"
    assert bcs.sent[0]["caller_bearer"] == "secret-token"
    assert bcs.sent[0]["idempotency_key"]


def test_a2a_timeout_persists_run_and_resume_reconciles_it() -> None:
    repo = _Repository()
    bcs = _Bcs(states=["running"])
    skill = BotTaskIntakeSkill(
        _UnusedBot(), _BotService(), bcs=bcs, bot_tokens=_Tokens(),
        intake_repository=repo, a2a_timeout=0.0,
    )
    handler = TaskIntakeHandler(repo, skill, _Resolver())

    pending = _run(handler.intake(
        TaskIntakeCommand(
            TaskIntakeAction.START,
            "分析支付失败率",
            requested_a2a_proxy_bot_ref="proxy",
            requested_target=IntakeTarget(IntakeTargetType.BOT, "risk:owner-2"),
        ),
        TaskIntakeContext(None, "owner-1", TaskSourceType.API),
    ))
    assert pending.a2a_run_id == "run-1"
    skill._a2a_timeout = 1.0
    bcs.states = ["completed"]
    resumed = _run(handler.intake(
        TaskIntakeCommand(TaskIntakeAction.RESUME),
        TaskIntakeContext(pending.session_id, "owner-1", TaskSourceType.API),
    ))
    assert resumed.a2a_run_id is None
    assert resumed.task_spec.goal.objective == "分析支付失败率"
    assert len(bcs.sent) == 1
