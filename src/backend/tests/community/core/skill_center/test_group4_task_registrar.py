"""G4 queue handlers are registered before the worker starts."""

from __future__ import annotations

import asyncio

from agentclaw.community.core.skill_center.services.group4_task_registrar import (
    SkillCenterGroup4TaskRegistrar,
)
from agentclaw.community.core.skill_center.services.skill_center_reference_service import (
    SKILL_CENTER_REFERENCE_TASK,
)
from agentclaw.community.core.skill_center.services.track_latest import (
    BOT_TRACK_LATEST_RECONCILE_TASK,
    TRACK_LATEST_FANOUT_TASK,
)
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.types import Complete


class _Handler:
    def __init__(self, task_type: str) -> None:
        self._task_type = task_type

    @property
    def task_type(self) -> str:
        return self._task_type

    def handle(self, _payload):
        return Complete()


def test_bootstrap_registers_reference_and_track_latest_handlers() -> None:
    registry = HandlerRegistry()
    reference = _Handler(SKILL_CENTER_REFERENCE_TASK)
    fanout = _Handler(TRACK_LATEST_FANOUT_TASK)
    reconcile = _Handler(BOT_TRACK_LATEST_RECONCILE_TASK)
    registrar = SkillCenterGroup4TaskRegistrar(
        registry=registry,
        reference=reference,  # type: ignore[arg-type]
        fanout=fanout,  # type: ignore[arg-type]
        reconcile=reconcile,  # type: ignore[arg-type]
    )

    asyncio.run(registrar.bootstrap())

    assert registry.get(SKILL_CENTER_REFERENCE_TASK) is reference
    assert registry.wakes_on_enqueue(SKILL_CENTER_REFERENCE_TASK) is True
    assert registry.get(TRACK_LATEST_FANOUT_TASK) is fanout
    assert registry.get(BOT_TRACK_LATEST_RECONCILE_TASK) is reconcile
