"""Unit tests for Bot inventory lifecycle display mapping."""
from __future__ import annotations

import pytest

from agentclaw.community.core.bot_inventory.services.lifecycle_view import BotLifecycleView
from agentclaw.community.core.bot_inventory.types import BotAction, BotInventoryKind, DisplayState


class _ServiceLifecycle:
    def display_state(self, *, bot):
        return DisplayState.SERVICE_ONLINE

    def allowed_actions(self, *, bot):
        return (BotAction.VIEW, BotAction.CHAT)


@pytest.fixture
def view() -> BotLifecycleView:
    return BotLifecycleView(_ServiceLifecycle())


@pytest.mark.unit
@pytest.mark.parametrize(
    ("bot", "expected"),
    [
        ({"status": "ACTIVE"}, DisplayState.RUNNING),
        ({"status": "RUNNING"}, DisplayState.RUNNING),
        ({"status": "READY"}, DisplayState.RUNNING),
        ({"status": "FAILED"}, DisplayState.FAILED),
        ({"status": "ERROR"}, DisplayState.FAILED),
        ({"status": "DORMANT"}, DisplayState.DORMANT),
        ({"status": "SLEEPING"}, DisplayState.DORMANT),
        ({"status": "PENDING"}, DisplayState.PENDING),
        ({"status": "ACTIVE", "ext": {"dormant": True}}, DisplayState.DORMANT),
    ],
)
def test_personal_display_state_matrix(view, bot, expected) -> None:
    assert view.display_state(bot=bot, kind=BotInventoryKind.PERSONAL_CLOUD) is expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("ACTIVE", DisplayState.LOCAL_RUNNING),
        ("RUNNING", DisplayState.LOCAL_RUNNING),
        ("READY", DisplayState.LOCAL_RUNNING),
        ("OFFLINE", DisplayState.LOCAL_OFFLINE),
        ("RELEASED", DisplayState.LOCAL_OFFLINE),
        ("RELEASING", DisplayState.LOCAL_OFFLINE),
        ("FAILED", DisplayState.LOCAL_FAILED),
        ("ERROR", DisplayState.LOCAL_FAILED),
        ("PENDING", DisplayState.LOCAL_PENDING),
    ],
)
def test_local_display_state_matrix(view, status, expected) -> None:
    assert view.display_state(bot={"status": status}, kind=BotInventoryKind.LOCAL) is expected


@pytest.mark.unit
def test_service_lifecycle_delegates_to_port(view) -> None:
    assert view.display_state(bot={}, kind=BotInventoryKind.SERVICE) is DisplayState.SERVICE_ONLINE
    actions, disabled = view.allowed_actions(bot={}, kind=BotInventoryKind.SERVICE)
    assert actions == (BotAction.VIEW, BotAction.CHAT)
    assert disabled == {}
