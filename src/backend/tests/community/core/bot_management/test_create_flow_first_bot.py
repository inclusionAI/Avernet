"""Passport selection is based on the owner's first live personal Bot."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.core.bot_management.create_flow import (
    BotCreateContext,
    BotCreateDeploymentMode,
    BotCreateSpec,
    Created,
    create_bot_with_authorization,
)
from agentclaw.community.plugin_api.auth_relationship import (
    AuthRelationshipError,
)
from agentclaw.community.plugin_api.passport import PassportError
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

pytestmark = pytest.mark.unit

_CONTEXT = BotCreateContext(
    deployment_mode=BotCreateDeploymentMode.CLOUD,
    space_kind="personal",
)


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
    passport.apply_first_agent_passport.return_value = {
        "token": "tok", "agent_code": "ac-1"
    }
    passport.apply_agent_passport.return_value = {
        "token": "tok", "agent_code": "ac-1"
    }

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
        context=_CONTEXT,
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


def test_a_non_default_tenant_no_longer_forces_apply_first():
    """The tenant does not get a vote any more — only eligibility does.

    This used to assert the opposite: any non-default tenant took applyFirst
    unconditionally (#556, "approval does not apply to external tenants").
    That made ``use_first_passport=False`` a request ``_apply_passport`` could
    silently decline, which W13 cannot survive — submission passes ``False``
    to get an authorization URL, and on a non-default tenant got a bare token
    and two empty URL fields instead.

    Kept rather than deleted, and inverted, because the old rule is exactly the
    thing a future change could restore without noticing: an owner who is not
    on their first Bot now goes through approval on **every** tenant.
    """
    with avernet_tenant_scope("acme"):
        passport, _, _ = _run(
            is_first_bot=False, is_first_personal_bot=False
        )

    passport.apply_agent_passport.assert_called_once()
    passport.apply_first_agent_passport.assert_not_called()


def test_a_non_default_tenants_first_bot_still_skips_approval():
    """The other half: eligibility still decides, and it decides the same way.

    Removing the tenant branch must not have turned into "everyone goes
    through approval" — a first Bot skips it, on a non-default tenant exactly
    as on the default one.
    """
    with avernet_tenant_scope("acme"):
        passport, _, _ = _run(is_first_bot=True, is_first_personal_bot=True)

    passport.apply_first_agent_passport.assert_called_once()
    passport.apply_agent_passport.assert_not_called()


def test_owner_relationship_failure_is_not_acknowledged_as_created():
    passport = MagicMock()
    passport.apply_first_agent_passport.return_value = {
        "token": "tok",
        "agent_code": "ac-1",
    }
    bot_service = MagicMock(spec=BotServiceProtocol)
    bot_service.is_first_bot.return_value = True
    bot_service.create_bot.return_value = {"bot_id": "20260805_ab12cd34"}
    skill_set_factory = MagicMock()
    skill_set_factory.create.return_value.get_bot_mcp_codes.return_value = []
    auth_relationship = MagicMock()
    auth_relationship.create_relationship.return_value = None

    with pytest.raises(AuthRelationshipError):
        create_bot_with_authorization(
            user_id="85020",
            nick_name="Alice",
            bot_id="20260805_ab12cd34",
            spec=_spec(),
            context=_CONTEXT,
            bot_service=bot_service,
            passport_plugin=passport,
            auth_rel_plugin=auth_relationship,
            skill_set_factory=skill_set_factory,
        )


def test_issued_passport_without_agent_code_does_not_create_bot():
    passport = MagicMock()
    passport.apply_first_agent_passport.return_value = {"token": "tok"}
    bot_service = MagicMock(spec=BotServiceProtocol)
    bot_service.is_first_bot.return_value = True
    skill_set_factory = MagicMock()
    skill_set_factory.create.return_value.get_bot_mcp_codes.return_value = []

    with pytest.raises(PassportError, match="no agent_code"):
        create_bot_with_authorization(
            user_id="85020",
            nick_name="Alice",
            bot_id="20260805_ab12cd34",
            spec=_spec(),
            context=_CONTEXT,
            bot_service=bot_service,
            passport_plugin=passport,
            auth_rel_plugin=MagicMock(),
            skill_set_factory=skill_set_factory,
        )

    bot_service.create_bot.assert_not_called()
