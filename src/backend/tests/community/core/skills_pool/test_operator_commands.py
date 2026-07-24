"""Skills Pool durable operator command tests."""

from __future__ import annotations

from dataclasses import replace

from agentclaw.community.core.skills_pool.operator_commands import (
    OperatorCommandOutcome,
    SkillsPoolOperatorCommands,
)
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    BotSkillLayoutState,
)


SCOPE = BotSkillLayoutScope("pre", "owner-1", "bot-1")


class FakeLayouts:
    def __init__(self) -> None:
        self.state = BotSkillLayoutState.legacy_default(SCOPE)

    def get(self, scope: BotSkillLayoutScope) -> BotSkillLayoutState:
        assert scope == SCOPE
        return self.state


class FakeQueue:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def enqueue(self, *args: object, **kwargs: object) -> None:
        self.calls.append((*args, kwargs))


def test_manual_wakeup_is_durable_and_auditable() -> None:
    layouts = FakeLayouts()
    queue = FakeQueue()
    commands = SkillsPoolOperatorCommands(
        layout_repository=layouts,
        task_queue_service=queue,
    )

    result = commands.wake(scope=SCOPE, operator="freddie")

    assert result.outcome is OperatorCommandOutcome.ENQUEUED
    assert queue.calls[0][0] == "skills_pool.reconcile"
    payload = queue.calls[0][1]
    assert payload["source"] == "operator_wakeup"
    assert payload["signal_identity"] == {"operator": "freddie"}


def test_retry_only_accepts_structured_retryable_failure() -> None:
    layouts = FakeLayouts()
    queue = FakeQueue()
    commands = SkillsPoolOperatorCommands(
        layout_repository=layouts,
        task_queue_service=queue,
    )

    unclaimed = commands.wake(
        scope=SCOPE,
        operator="freddie",
        retry_only=True,
    )
    layouts.state = replace(
        layouts.state,
        persisted=True,
        last_failure_retryable=False,
    )
    blocked = commands.wake(
        scope=SCOPE,
        operator="freddie",
        retry_only=True,
    )
    layouts.state = replace(layouts.state, last_failure_retryable=True)
    enqueued = commands.wake(
        scope=SCOPE,
        operator="freddie",
        retry_only=True,
    )

    assert unclaimed.outcome is OperatorCommandOutcome.NOT_CLAIMED
    assert blocked.outcome is OperatorCommandOutcome.NOT_RETRYABLE
    assert enqueued.outcome is OperatorCommandOutcome.ENQUEUED
    assert len(queue.calls) == 1
