"""Passport selection is based on the owner's first live personal Bot."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.core.bot_management.create_flow import (
    BotCreateSpec,
    Created,
    create_bot_with_authorization,
)
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

pytestmark = pytest.mark.unit


def _spec(bot_type: str = "personal") -> BotCreateSpec:
    return BotCreateSpec(
        entity_id="85020",
        engine_type="openclaw",
        bot_type=bot_type,
        bot_name="Bot",
    )


def _run(
    *,
    bot_type: str = "personal",
    is_first_bot: bool,
    is_first_personal_bot: bool,
):
    passport = MagicMock()
    passport.apply_first_agent_passport.return_value = {"token": "tok"}
    passport.apply_agent_passport.return_value = {"token": "tok"}

    bot_service = MagicMock(spec=BotServiceProtocol)
    bot_service.is_first_bot.return_value = is_first_bot
    bot_service.is_first_personal_bot.return_value = is_first_personal_bot
    bot_service.create_bot.return_value = {"bot_id": "20260805_ab12cd34"}

    skill_set_factory = MagicMock()
    skill_set_factory.create.return_value.get_bot_mcp_codes.return_value = []

    outcome = create_bot_with_authorization(
        user_id="85020",
        nick_name="Alice",
        bot_id="20260805_ab12cd34",
        spec=_spec(bot_type),
        bot_service=bot_service,
        passport_plugin=passport,
        auth_rel_plugin=MagicMock(),
        skill_set_factory=skill_set_factory,
    )
    return passport, bot_service, outcome


def test_first_bot_and_first_personal_bot_uses_first_passport():
    passport, bot_service, outcome = _run(
        is_first_bot=True, is_first_personal_bot=True
    )

    passport.apply_first_agent_passport.assert_called_once()
    passport.apply_agent_passport.assert_not_called()
    bot_service.is_first_personal_bot.assert_not_called()
    assert isinstance(outcome, Created)
    assert outcome.is_first_bot is True


def test_service_bot_exists_but_first_personal_bot_uses_first_passport():
    passport, _, outcome = _run(
        is_first_bot=False, is_first_personal_bot=True
    )

    passport.apply_first_agent_passport.assert_called_once()
    passport.apply_agent_passport.assert_not_called()
    assert isinstance(outcome, Created)
    assert outcome.is_first_bot is False


def test_existing_personal_bot_uses_regular_passport():
    passport, _, _ = _run(
        is_first_bot=False, is_first_personal_bot=False
    )

    passport.apply_agent_passport.assert_called_once()
    passport.apply_first_agent_passport.assert_not_called()


def test_first_service_bot_preserves_first_passport_behavior():
    passport, bot_service, _ = _run(
        bot_type="service", is_first_bot=True, is_first_personal_bot=True
    )

    passport.apply_first_agent_passport.assert_called_once()
    passport.apply_agent_passport.assert_not_called()
    bot_service.is_first_personal_bot.assert_not_called()


def test_non_first_service_bot_uses_regular_passport():
    passport, bot_service, _ = _run(
        bot_type="service", is_first_bot=False, is_first_personal_bot=True
    )

    passport.apply_agent_passport.assert_called_once()
    passport.apply_first_agent_passport.assert_not_called()
    bot_service.is_first_personal_bot.assert_not_called()


def test_non_default_tenant_keeps_apply_first_behavior():
    with avernet_tenant_scope("acme"):
        passport, _, _ = _run(
            is_first_bot=False, is_first_personal_bot=False
        )

    passport.apply_first_agent_passport.assert_called_once()
    passport.apply_agent_passport.assert_not_called()
