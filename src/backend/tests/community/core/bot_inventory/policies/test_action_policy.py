"""Unit tests for Bot inventory action policy tables."""
from __future__ import annotations

import pytest

from agentclaw.community.core.bot_inventory.policies.action_policy import actions_for
from agentclaw.community.core.bot_inventory.types import BotAction, BotInventoryKind, DisplayState


def _values(actions: tuple[BotAction, ...]) -> tuple[str, ...]:
    return tuple(action.value for action in actions)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("kind", "state", "expected", "disabled"),
    [
        (
            BotInventoryKind.PERSONAL_CLOUD,
            DisplayState.RUNNING,
            (
                "view", "chat", "edit", "restart", "engine_restart", "delete",
                "passport", "engine_config", "data_init",
            ),
            {},
        ),
        (
            BotInventoryKind.PERSONAL_CLOUD,
            DisplayState.DORMANT,
            ("view", "activate", "delete"),
            {
                "chat": "activate first",
                "edit": "activate first",
                "restart": "activate first",
                "engine_restart": "activate first",
            },
        ),
        (
            BotInventoryKind.PERSONAL_CLOUD,
            DisplayState.FAILED,
            ("view", "delete"),
            {"restart": "bot provisioning failed", "engine_restart": "bot provisioning failed"},
        ),
        (
            BotInventoryKind.PERSONAL_CLOUD,
            DisplayState.PENDING,
            ("view",),
            {
                "chat": "bot not ready",
                "edit": "bot not ready",
                "restart": "bot not ready",
                "engine_restart": "bot not ready",
            },
        ),
        (
            BotInventoryKind.LOCAL,
            DisplayState.LOCAL_RUNNING,
            ("view", "chat", "edit", "restart", "delete", "open_folder"),
            {
                "runtime_logs": "not supported in this phase",
                "engine_restart": "not supported in this phase",
            },
        ),
        (
            BotInventoryKind.LOCAL,
            DisplayState.LOCAL_OFFLINE,
            ("view", "restart", "delete", "open_folder"),
            {"chat": "device offline", "edit": "device offline"},
        ),
        (
            BotInventoryKind.LOCAL,
            DisplayState.LOCAL_PENDING,
            ("view", "delete"),
            {"chat": "local bot not ready", "edit": "local bot not ready", "restart": "local bot not ready"},
        ),
        (
            BotInventoryKind.LOCAL,
            DisplayState.LOCAL_FAILED,
            ("view", "delete"),
            {"chat": "local bot not ready", "edit": "local bot not ready", "restart": "local bot not ready"},
        ),
        (BotInventoryKind.SERVICE, DisplayState.SERVICE_DRAFT, ("view",), {}),
    ],
)
def test_actions_for_matrix(kind, state, expected, disabled) -> None:
    actions, disabled_actions = actions_for(kind=kind, display_state=state)

    assert _values(actions) == expected
    assert disabled_actions == disabled
