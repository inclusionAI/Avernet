"""Tests for the removable temporary duty fallback candidate augmentation wiring."""

from __future__ import annotations

import pytest
from injector import Injector

from agentclaw.community.core.task.task_dispatch.candidate_augmentation import (
    NullSearchCandidateAugmentor,
    SearchCandidateAugmentor,
    TemporaryDutySearchCandidateAugmentor,
)
from agentclaw.community.di.modules import config_module
from agentclaw.community.di.modules.task_module import TaskModule
from agentclaw.community.di.modules.temporary_duty_module import TemporaryDutyModule


@pytest.mark.asyncio
async def test_task_module_defaults_to_null_search_augmentor() -> None:
    injector = Injector([TaskModule()])

    assert isinstance(
        injector.get(SearchCandidateAugmentor), NullSearchCandidateAugmentor
    )


@pytest.mark.asyncio
async def test_temporary_duty_module_overrides_augmentor_from_config(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        config_module,
        "read_user_config",
        lambda: {
            "task_dispatch": {
                "temporary_duty_bot_candidates": [
                    {
                        "bot_id": "20260407_025zmn4f:378611",
                        "name": "RiskProfilePlus BOT",
                        "description": "RP+ platform diagnostics",
                    }
                ]
            }
        },
    )

    injector = Injector([TaskModule(), TemporaryDutyModule()])
    augmentor = injector.get(SearchCandidateAugmentor)

    assert isinstance(augmentor, TemporaryDutySearchCandidateAugmentor)


@pytest.mark.asyncio
async def test_temporary_duty_module_uses_null_when_not_configured(
    monkeypatch,
) -> None:
    monkeypatch.setattr(config_module, "read_user_config", lambda: {})

    assert isinstance(
        TemporaryDutyModule().search_candidate_augmentor(),
        NullSearchCandidateAugmentor,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    [
        "invalid",
        [{"bot_id": "bot", "name": "Bot", "extra": "invalid"}],
        [{"name": "Bot"}],
        [{"bot_id": "bot"}, {"bot_id": "bot", "name": "Bot"}],
    ],
)
async def test_temporary_duty_module_rejects_invalid_config(monkeypatch, raw) -> None:
    monkeypatch.setattr(
        config_module,
        "read_user_config",
        lambda: {"task_dispatch": {"temporary_duty_bot_candidates": raw}},
    )

    with pytest.raises(ValueError):
        TemporaryDutyModule().search_candidate_augmentor()
