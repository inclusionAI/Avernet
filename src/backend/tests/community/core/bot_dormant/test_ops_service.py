from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_dormant.ops_service import DormantOpsService
from agentclaw.community.core.bot_dormant.service import DormantBotService
from agentclaw.community.plugin_api.passport import PassportPlugin


def test_unfreeze_passport_one_only_calls_passport() -> None:
    dormant_service = MagicMock(spec=DormantBotService)
    passport = MagicMock(spec=PassportPlugin)
    service = DormantOpsService(dormant_service, passport)

    result = service.unfreeze_passport_one(
        bot_id="default",
        owner_id="37565",
        reason="recover license",
    )

    assert result == {
        "bot_id": "default",
        "owner_id": "37565",
        "status": "passport_online",
    }
    passport.unfreeze_agent_passport.assert_called_once_with(
        bot_id="default",
        owner_workno="37565",
        reason="recover license",
    )
    assert dormant_service.mock_calls == []


def test_unfreeze_passport_one_propagates_passport_error() -> None:
    passport = MagicMock(spec=PassportPlugin)
    passport.unfreeze_agent_passport.side_effect = RuntimeError(
        "passport unavailable"
    )
    service = DormantOpsService(MagicMock(spec=DormantBotService), passport)

    with pytest.raises(RuntimeError, match="passport unavailable"):
        service.unfreeze_passport_one(
            bot_id="default",
            owner_id="37565",
            reason="recover license",
        )
