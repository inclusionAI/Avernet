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
    DesktopSkillRecoveryTaskHandler,
)
from agentclaw.community.core.skill_center.services.bot_runtime_projector import (
    BotRuntimeProjector,
)
from agentclaw.community.core.skill_center.services.recovering_bot_runtime_projector import (
    RecoveringBotRuntimeProjector,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    BotRuntimeProjectorProtocol,
)
from agentclaw.community.core.skill_center.services.group4_task_registrar import (
    SkillCenterGroup4TaskRegistrar,
)
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.di import DeployProfile, build_injector
from agentclaw.community.di.modules import desktop_skill_recovery_config_module
from agentclaw.community.di.modules.desktop_skill_recovery_config_module import (
    DesktopSkillRecoveryConfigModule,
)


def test_desktop_skill_recovery_service_handler_and_registry_resolve(
    test_injector,
) -> None:
    service = test_injector.get(DesktopSkillRecoveryServiceProtocol)
    handler = test_injector.get(DesktopSkillRecoveryTaskHandler)
    registrar = test_injector.get(SkillCenterGroup4TaskRegistrar)
    registry = test_injector.get(HandlerRegistry)
    projector = test_injector.get(BotRuntimeProjectorProtocol)
    raw_projector = test_injector.get(BotRuntimeProjector)

    assert isinstance(service, DesktopSkillRecoveryService)
    assert isinstance(handler, DesktopSkillRecoveryTaskHandler)
    assert isinstance(projector, RecoveringBotRuntimeProjector)
    assert handler._projector is raw_projector

    asyncio.run(registrar.bootstrap())
    assert registry.get(DESKTOP_SKILL_RECOVERY_TASK) is handler
    assert registry.wakes_on_enqueue(DESKTOP_SKILL_RECOVERY_TASK) is True


def test_desktop_skill_recovery_config_reads_task_deadline(monkeypatch) -> None:
    monkeypatch.setattr(
        desktop_skill_recovery_config_module,
        "_block",
        lambda: {"task_deadline_seconds": 600},
    )

    value = DesktopSkillRecoveryConfigModule().desktop_skill_recovery()

    assert value.task_deadline_seconds == 600


def test_desktop_skill_recovery_config_rejects_nonpositive_deadline(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        desktop_skill_recovery_config_module,
        "_block",
        lambda: {"task_deadline_seconds": 0},
    )

    with pytest.raises(ValueError, match="must be positive"):
        DesktopSkillRecoveryConfigModule().desktop_skill_recovery()


@pytest.mark.parametrize(
    "block",
    [
        {"task_deadline_seconds": True},
        {"task_deadline_seconds": 0},
        {"sweep_interval_seconds": 600},
    ],
)
def test_desktop_skill_recovery_config_rejects_invalid_schema(
    monkeypatch, block
) -> None:
    monkeypatch.setattr(desktop_skill_recovery_config_module, "_block", lambda: block)

    with pytest.raises((TypeError, ValueError)):
        DesktopSkillRecoveryConfigModule().desktop_skill_recovery()


def test_invalid_desktop_skill_recovery_config_fails_injector_build(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        desktop_skill_recovery_config_module.config_module,
        "read_user_config",
        lambda: {"desktop_skill_recovery": {"sweep_page_szie": 10}},
    )

    with pytest.raises(ValueError, match="unknown desktop_skill_recovery"):
        build_injector(profile=DeployProfile.TEST)


@pytest.mark.parametrize("raw", [False, 0, [], ""])
def test_desktop_skill_recovery_rejects_falsey_non_mapping_block(
    monkeypatch, raw
) -> None:
    monkeypatch.setattr(
        desktop_skill_recovery_config_module.config_module,
        "read_user_config",
        lambda: {"desktop_skill_recovery": raw},
    )

    with pytest.raises(ValueError, match="must be a mapping"):
        DesktopSkillRecoveryConfigModule().desktop_skill_recovery()
