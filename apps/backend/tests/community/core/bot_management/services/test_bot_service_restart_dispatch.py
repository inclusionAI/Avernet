"""Desktop restart dispatch must be based on stored owner identity."""

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_management.services.bot_service import BotService

@pytest.mark.parametrize(
    "caller_expected, source_id, expected_source_bot_id",
    [
        (None, "user-owner", "bot-1"),
        ("desktop", "other-user", None),
    ],
)
def test_desktop_restart_dispatch_uses_valid_owner_patient(
    caller_expected, source_id, expected_source_bot_id
):
    bot_id = "bot-1"
    repository = MagicMock()
    repository.get_by_id_and_owner.return_value = {
        "bot_id": bot_id,
        "owner_id": "user-owner",
        "bot_type": "desktop",
    }
    desktop_service = MagicMock()
    desktop_service.restart.return_value = {"status": "PENDING"}
    provider = MagicMock(return_value=desktop_service)

    service = MagicMock()
    service._repository = repository
    service._desktop_bot_service_provider = provider

    result = BotService.restart_bot_async(
        bot_service=service,
        repo=repository,
        desktop_service=desktop_service,
        caller_expected=caller_expected,
        source_id=source_id,
    )

    assert result == {"status": "PENDING"}
    provider.assert_called_once()
    desktop_service.restart.assert_called_once_with(
        bot_id=bot_id, user_id=source_id
    )
