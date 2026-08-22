"""Serving-readiness compatibility for persisted Bot record shapes."""

from __future__ import annotations

import pytest

from agentclaw.community.core.bot_management.readiness import is_bot_ready


@pytest.mark.parametrize("template_type", ["applicationCoding", "personalCoding"])
def test_active_aicoding_bot_without_start_marker_is_ready(
    template_type: str,
) -> None:
    """BotRepository rows carry binding locators but not the hydrated binding."""
    bot = {
        "status": "ACTIVE",
        "active_engine": "claude_code",
        "template_type": template_type,
        "binding_id": 123,
        "device_id": "BOT-ready",
        "ext": {},
    }

    assert is_bot_ready(bot) is True


def test_explicit_application_initialization_state_remains_authoritative() -> None:
    bot = {
        "status": "ACTIVE",
        "active_engine": "claude_code",
        "template_type": "applicationCoding",
        "binding_id": 123,
        "device_id": "BOT-starting",
        "ext": {"start_status": "PENDING"},
    }

    assert is_bot_ready(bot) is False
