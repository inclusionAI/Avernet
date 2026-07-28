"""A pending create must hand back a usable authorization handle (R5/F25).

``AuthPending`` tells the caller "go authorize, then poll". That is only a real
state if there is somewhere to go. When Passport returns neither a token nor an
iframe/redirect URL — including the ``None`` result the plugin contract
explicitly permits — the apply did not succeed, and reporting it as pending
hands back a dead end indistinguishable from a genuine wait.

Both surfaces already map ``PassportError``: the internal route answers 5400,
the public one 502.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_management.create_flow import (
    AuthPending,
    BotCreateSpec,
    create_bot_with_authorization,
)
from agentclaw.community.plugin_api.passport import PassportError

_SPEC = BotCreateSpec(
    entity_id="u1",
    engine_type="openclaw",
    bot_type="personal",
    bot_name="Bot",
)


def _run(apply_result):
    passport = MagicMock()
    passport.apply_first_agent_passport.return_value = apply_result
    bot_service = MagicMock()
    skill_set_factory = MagicMock()
    skill_set_factory.create.return_value.get_bot_mcp_codes.return_value = []
    return create_bot_with_authorization(
        user_id="u1",
        nick_name="u1",
        bot_id="default",
        spec=_SPEC,
        bot_service=bot_service,
        passport_plugin=passport,
        auth_rel_plugin=MagicMock(),
        skill_set_factory=skill_set_factory,
    )


@pytest.mark.parametrize(
    "apply_result",
    [
        None,                                   # permitted by the plugin contract
        {},                                     # answered, but with nothing usable
        {"iframe_url": "", "redirect_url": ""},  # present but empty
    ],
    ids=["none", "empty-dict", "empty-urls"],
)
def test_pending_without_any_handle_raises(apply_result):
    with pytest.raises(PassportError):
        _run(apply_result)


@pytest.mark.parametrize(
    "apply_result",
    [
        {"iframe_url": "https://passport/iframe"},
        {"redirect_url": "https://passport/redirect"},
    ],
    ids=["iframe-only", "redirect-only"],
)
def test_pending_with_either_handle_is_returned(apply_result):
    """Either handle alone is enough — the caller can still authorize."""
    outcome = _run(apply_result)
    assert isinstance(outcome, AuthPending)
    assert outcome.iframe_url or outcome.redirect_url
