"""Desktop restart dispatch must be based on stored owner identity."""

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
    BotService,
)


@pytest.mark.parametrize("caller_expected", [None, "desktop"])
@pytest.mark.asyncio
async def test_desktop_restart_dispatch_uses_valid_owner(caller_expected):
    bot_id = "bot-1"
    user_id = "user-owner"
    repository = MagicMock()
    repository.get_by_id_and_owner.return_value = {
        "bot_id": bot_id,
        "owner_id": user_id,
        "bot_type": "desktop",
    }
    desktop_service = MagicMock()
    desktop_service.restart.return_value = {"status": "PENDING"}
    provider = MagicMock(return_value=desktop_service)

    service = MagicMock()
    service._repository = repository
    service._desktop_bot_service_provider = provider

    result = await BotService.restart_bot_async(
        service,
        bot_id=bot_id,
        user_id=user_id,
        caller_expected=caller_expected,
    )

    assert result == {"status": "PENDING"}
    repository.get_by_id_and_owner.assert_called_once_with(bot_id, user_id)
    provider.assert_called_once()
    desktop_service.restart.assert_called_once_with(
        bot_id=bot_id,
        user_id=user_id,
    )


@pytest.mark.asyncio
async def test_desktop_restart_dispatch_rejects_cross_owner_patiently():
    bot_id = "bot-1"
    owner_id = "user-owner"
    repository = MagicMock()
    desktop_record = {
        "bot_id": bot_id,
        "owner_id": owner_id,
        "bot_type": "desktop",
    }
    repository.get_by_id_and_owner.side_effect = (
        lambda requested_bot_id, requested_user_id: (
            desktop_record if requested_user_id == owner_id else None
        )
    )
    desktop_service = MagicMock()
    provider = MagicMock(return_value=desktop_service)

    service = MagicMock()
    service._repository = repository
    service._desktop_bot_service_provider = provider
    # get_bot is the existing ownership guard and raises for an unknown owner.
    service.get_bot.side_effect = BotNotFoundError(f"Bot not found: {bot_id}")

    with pytest.raises(BotNotFoundError):
        await BotService.restart_bot_async(
            service,
            bot_id=bot_id,
            user_id="other-user",
        )

    repository.get_by_id_and_owner.assert_called_once_with(bot_id, "other-user")
    provider.assert_not_called()
    desktop_service.restart.assert_not_called()
