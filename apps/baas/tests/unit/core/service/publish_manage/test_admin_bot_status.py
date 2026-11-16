"""Tests for DefaultPublishAdminService.update_bot_status.

Covers the direct bot status override, its result dataclass, and the
cross-env lookup (no get_current_env). Mock builders shared with the other
admin-service tests live in ``_admin_test_fixtures``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.api.publish_manage import (
    PublishNotFoundError,
    UpdateBotStatusResult,
)
from tests.unit.core.service.publish_manage._admin_test_fixtures import (
    _make_mock_bot,
    _make_service,
)


class TestUpdateBotStatus:
    """DefaultPublishAdminService.update_bot_status."""

    async def test_update_bot_status_happy_path(self):
        mock_bot = _make_mock_bot(
            id=42,
            status="ACTIVE",
            tenant="acme",
            env="prod",
            bot_uuid="bot-abc-123",
        )
        bot_repo = MagicMock()
        bot_repo.get_by_id_only.return_value = mock_bot

        service = _make_service(bot_repo=bot_repo)

        result = await service.update_bot_status(
            bot_id=42,
            status="STOPPED",
            operator="ops.alice",
        )

        assert isinstance(result, UpdateBotStatusResult)
        assert result.bot_id == 42
        assert result.bot_uuid == "bot-abc-123"
        assert result.previous_status == "ACTIVE"
        assert result.new_status == "STOPPED"

        bot_repo.get_by_id_only.assert_called_once_with(42)
        bot_repo.update_status.assert_called_once_with(
            bot_id=42,
            tenant="acme",
            env="prod",
            status="STOPPED",
            modifier="ops.alice",
        )

    async def test_update_bot_status_not_found_raises(self):
        bot_repo = MagicMock()
        bot_repo.get_by_id_only.return_value = None

        service = _make_service(bot_repo=bot_repo)

        with pytest.raises(PublishNotFoundError) as exc_info:
            await service.update_bot_status(
                bot_id=999,
                status="STOPPED",
                operator="ops.alice",
            )

        assert "999" in str(exc_info.value)
        bot_repo.update_status.assert_not_called()

    async def test_update_bot_status_does_not_call_get_current_env(self):
        mock_bot = _make_mock_bot(id=42, status="ACTIVE", bot_uuid="bot-abc-123")
        bot_repo = MagicMock()
        bot_repo.get_by_id_only.return_value = mock_bot

        service = _make_service(bot_repo=bot_repo)

        with patch(
            "secbaas.community.core.service.publish_manage._admin_service.get_current_env",
        ) as mock_get_env:
            await service.update_bot_status(
                bot_id=42,
                status="STOPPED",
                operator="ops.alice",
            )

        mock_get_env.assert_not_called()

    async def test_update_bot_status_emits_warning_log(self):
        mock_bot = _make_mock_bot(
            id=42,
            status="ACTIVE",
            tenant="acme",
            env="prod",
            bot_uuid="bot-abc-123",
        )
        bot_repo = MagicMock()
        bot_repo.get_by_id_only.return_value = mock_bot

        service = _make_service(bot_repo=bot_repo)

        with patch(
            "secbaas.community.core.service.publish_manage._admin_service.logger"
        ) as mock_logger:
            await service.update_bot_status(
                bot_id=42,
                status="STOPPED",
                operator="ops.alice",
            )

        mock_logger.warning.assert_called_once()
        log_msg = mock_logger.warning.call_args.args[0]
        assert "ADMIN_UPDATE_BOT_STATUS:" in log_msg
        assert "bot_id=42" in log_msg
        assert "bot_uuid=bot-abc-123" in log_msg
        assert "previous_status=ACTIVE" in log_msg
        assert "new_status=STOPPED" in log_msg
        assert "operator=ops.alice" in log_msg
        assert "tenant=acme" in log_msg
        assert "env=prod" in log_msg

    async def test_update_bot_status_captures_different_previous_status(self):
        mock_bot = _make_mock_bot(
            id=42, status="STOPPED", tenant="acme", env="prod", bot_uuid="bot-abc-123"
        )
        bot_repo = MagicMock()
        bot_repo.get_by_id_only.return_value = mock_bot

        service = _make_service(bot_repo=bot_repo)

        result = await service.update_bot_status(
            bot_id=42,
            status="ACTIVE",
            operator="ops.alice",
        )

        assert result.previous_status == "STOPPED"
        assert result.new_status == "ACTIVE"

        bot_repo.update_status.assert_called_once_with(
            bot_id=42,
            tenant="acme",
            env="prod",
            status="ACTIVE",
            modifier="ops.alice",
        )

    async def test_update_bot_status_uses_record_env_not_server_env(self):
        mock_bot = _make_mock_bot(
            id=42, status="ACTIVE", tenant="acme", env="gray", bot_uuid="bot-abc-123"
        )
        bot_repo = MagicMock()
        bot_repo.get_by_id_only.return_value = mock_bot

        service = _make_service(bot_repo=bot_repo)

        await service.update_bot_status(
            bot_id=42,
            status="STOPPED",
            operator="ops.alice",
        )

        kwargs = bot_repo.update_status.call_args.kwargs
        assert kwargs["env"] == "gray"
        assert kwargs["tenant"] == "acme"
        assert kwargs["bot_id"] == 42


class TestUpdateBotStatusResult:
    """UpdateBotStatusResult dataclass contract."""

    def test_construction(self):
        result = UpdateBotStatusResult(
            bot_id=42,
            bot_uuid="bot-abc-123",
            previous_status="ACTIVE",
            new_status="STOPPED",
        )
        assert result.bot_id == 42
        assert result.bot_uuid == "bot-abc-123"
        assert result.previous_status == "ACTIVE"
        assert result.new_status == "STOPPED"

    def test_construction_with_other_values(self):
        result = UpdateBotStatusResult(
            bot_id=999,
            bot_uuid="abc-def-456",
            previous_status="PENDING",
            new_status="ACTIVE",
        )
        assert result.bot_id == 999
        assert result.bot_uuid == "abc-def-456"
        assert result.previous_status == "PENDING"
        assert result.new_status == "ACTIVE"
