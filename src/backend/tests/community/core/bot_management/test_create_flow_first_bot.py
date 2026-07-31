"""Which Passport apply the create flow calls is a data question, not a string one.

``create_bot_with_authorization`` picks between ``apply_first_agent_passport``
and ``apply_agent_passport``. It used to decide with ``bot_id == "default"``, a
proxy that held only while every owner's first bot was ``"default"``.

``generate_bot_id`` now confines that shortcut to the default tenant (issue
#556), so in any other tenant a genuinely first bot carries a generated id. Under
the old proxy that owner would be sent to ``apply_agent_passport`` — the
non-first-bot branch — despite never having had a Passport.

The flow therefore asks the repository. These tests pin both directions, since
the two branches hit different tcauthmng facade methods.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.core.bot_management.create_flow import (
    BotCreateSpec,
    create_bot_with_authorization,
)
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

pytestmark = pytest.mark.unit

_SPEC = BotCreateSpec(
    entity_id="85020",
    engine_type="openclaw",
    bot_type="personal",
    bot_name="Bot",
)


def _run(*, bot_id: str, is_first_bot: bool):
    passport = MagicMock()
    passport.apply_first_agent_passport.return_value = {"token": "tok"}
    passport.apply_agent_passport.return_value = {"token": "tok"}

    # Spec'd to the *protocol*, not the concrete class: both routers inject
    # BotServiceProtocol, so anything create_flow calls on it has to be declared
    # there. A bare MagicMock would happily answer an undeclared method and hide
    # the AttributeError a conforming implementation would raise at runtime.
    bot_service = MagicMock(spec=BotServiceProtocol)
    bot_service.is_first_bot.return_value = is_first_bot

    skill_set_factory = MagicMock()
    skill_set_factory.create.return_value.get_bot_mcp_codes.return_value = []

    create_bot_with_authorization(
        user_id="85020",
        nick_name="Alice",
        bot_id=bot_id,
        spec=_SPEC,
        bot_service=bot_service,
        passport_plugin=passport,
        auth_rel_plugin=MagicMock(),
        skill_set_factory=skill_set_factory,
    )
    return passport, bot_service


def test_generated_id_still_takes_the_first_bot_branch():
    """The case the id proxy got wrong: first bot, non-"default" id."""
    passport, bot_service = _run(bot_id="20260730_ab12cd34", is_first_bot=True)

    passport.apply_first_agent_passport.assert_called_once()
    passport.apply_agent_passport.assert_not_called()
    bot_service.is_first_bot.assert_called_once_with("85020")


def test_default_id_takes_the_first_bot_branch():
    """Unchanged for the default tenant, where a first bot is still "default"."""
    passport, _ = _run(bot_id="default", is_first_bot=True)

    passport.apply_first_agent_passport.assert_called_once()
    passport.apply_agent_passport.assert_not_called()


def test_owner_with_existing_bots_takes_the_non_first_branch():
    passport, _ = _run(bot_id="20260730_ab12cd34", is_first_bot=False)

    passport.apply_agent_passport.assert_called_once()
    passport.apply_first_agent_passport.assert_not_called()


def test_branch_is_not_inferred_from_the_id_in_another_tenant():
    """A non-default tenant never mints "default", so the id must not decide."""
    with avernet_tenant_scope("acme"):
        passport, _ = _run(bot_id="20260730_ab12cd34", is_first_bot=True)

    passport.apply_first_agent_passport.assert_called_once()
    passport.apply_agent_passport.assert_not_called()
