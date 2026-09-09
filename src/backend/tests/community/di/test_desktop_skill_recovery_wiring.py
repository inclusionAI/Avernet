"""Composition-root coverage for Desktop Skill recovery."""

from __future__ import annotations

import asyncio

import pytest

from agentclaw.community.core.skill_center.desktop_skill_recovery_protocol import (
    DesktopSkillRecoveryServiceProtocol,
)
from agentclaw.community.core.skill_center.services.desktop_skill_recovery import (
    DESKTOP_SKILL_RECOVERY_TASK,
    DesktopSkillRecoveryService,
    DesktopSkillRecoverySweeper,
    DesktopSkillRecoveryTaskHandler,
)
from agentclaw.community.core.skill_center.services.group4_task_registrar import (
    SkillCenterGroup4TaskRegistrar,
)
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.di.modules import config_module
from agentclaw.community.di.modules.config_module import ConfigModule


def test_desktop_skill_recovery_service_handler_sweeper_and_registry_resolve(
    test_injector,
) -> None:
    service = test_injector.get(DesktopSkillRecoveryServiceProtocol)
    handler = test_injector.get(DesktopSkillRecoveryTaskHandler)
    sweeper = test_injector.get(DesktopSkillRecoverySweeper)
    registrar = test_injector.get(SkillCenterGroup4TaskRegistrar)
    registry = test_injector.get(HandlerRegistry)

    assert isinstance(service, DesktopSkillRecoveryService)
    assert isinstance(handler, DesktopSkillRecoveryTaskHandler)
    assert isinstance(sweeper, DesktopSkillRecoverySweeper)

    asyncio.run(registrar.bootstrap())
    assert registry.get(DESKTOP_SKILL_RECOVERY_TASK) is handler
    assert registry.wakes_on_enqueue(DESKTOP_SKILL_RECOVERY_TASK) is True


def test_desktop_skill_recovery_config_reads_valid_sweep_values(monkeypatch) -> None:
    monkeypatch.setattr(
        config_module,
        "_block",
        lambda name: {
            "enabled": False,
            "sweep_interval_seconds": 601,
            "sweep_page_size": 23,
        }
        if name == "desktop_skill_recovery"
        else {},
    )

    value = ConfigModule().desktop_skill_recovery()

    assert value.enabled is False
    assert value.sweep_interval_seconds == 601
    assert value.sweep_page_size == 23


@pytest.mark.parametrize(
    "field,value",
    [("sweep_interval_seconds", 0), ("sweep_page_size", 0)],
)
def test_desktop_skill_recovery_config_rejects_nonpositive_values(
    monkeypatch, field, value
) -> None:
    monkeypatch.setattr(
        config_module,
        "_block",
        lambda _name: {field: value},
    )

    with pytest.raises(ValueError, match="must be positive"):
        ConfigModule().desktop_skill_recovery()


@pytest.mark.parametrize(
    "block",
    [
        {"enabled": "not-bool"},
        {"sweep_interval_seconds": float("nan")},
        {"sweep_page_size": True},
        {"sweep_page_szie": 10},
    ],
)
def test_desktop_skill_recovery_config_rejects_invalid_schema(
    monkeypatch, block
) -> None:
    monkeypatch.setattr(config_module, "_block", lambda _name: block)

    with pytest.raises((TypeError, ValueError)):
        ConfigModule().desktop_skill_recovery()
