"""Unit tests for qpm_config_router.

Covers all 5 endpoints:
- GET /api/v1/bot-qpm — list_qpm_configs
- GET /api/v1/bot-qpm/{bot_id} — get_qpm_config
- POST /api/v1/bot-qpm — upsert_qpm_config
- PUT /api/v1/bot-qpm/{bot_id} — update_qpm_config
- DELETE /api/v1/bot-qpm/{bot_id} — delete_qpm_config
"""

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from secbaas.community.adapters.web.routers.config_management.qpm_config_router import (
    BotQpmListResponse,
    BotQpmResponse,
    BotQpmUpdateRequest,
    BotQpmUpsertRequest,
    delete_qpm_config,
    get_qpm_config,
    list_qpm_configs,
    update_qpm_config,
    upsert_qpm_config,
)
from secbaas.community.api import OperationContext

_OP_CTX = OperationContext(operator="test_user", env="dev")


def _make_item(id: int = 1, bot_id: str = "bot-001", qpm: int = 100) -> MagicMock:
    """Build a mock QPM item with required attributes."""
    m = MagicMock()
    m.id = id
    m.bot_id = bot_id
    m.qpm = qpm
    m.env = "dev"
    m.gmt_create = None
    m.gmt_modified = None
    return m


class TestListQpmConfigs:
    """Tests for list_qpm_configs endpoint."""

    @pytest.mark.asyncio
    async def test_lists_configs(self):
        mock_svc = MagicMock()
        item = _make_item()
        mock_svc.list_configs.return_value = MagicMock(items=[item], total=1)

        result = await list_qpm_configs(op_ctx=_OP_CTX, service=mock_svc)

        assert result.code == 0
        assert result.data.total == 1
        assert len(result.data.items) == 1
        assert result.data.items[0].bot_id == "bot-001"

    @pytest.mark.asyncio
    async def test_lists_empty(self):
        mock_svc = MagicMock()
        mock_svc.list_configs.return_value = MagicMock(items=[], total=0)

        result = await list_qpm_configs(op_ctx=_OP_CTX, service=mock_svc)

        assert result.code == 0
        assert result.data.total == 0
        assert len(result.data.items) == 0


class TestGetQpmConfig:
    """Tests for get_qpm_config endpoint."""

    @pytest.mark.asyncio
    async def test_gets_config(self):
        mock_svc = MagicMock()
        item = _make_item()
        mock_svc.get_config.return_value = item

        result = await get_qpm_config(
            bot_id="bot-001", op_ctx=_OP_CTX, service=mock_svc
        )

        assert result.code == 0
        assert result.data.bot_id == "bot-001"
        mock_svc.get_config.assert_called_once_with("bot-001")

    @pytest.mark.asyncio
    async def test_raises_404_when_not_found(self):
        mock_svc = MagicMock()
        mock_svc.get_config.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await get_qpm_config(bot_id="missing", op_ctx=_OP_CTX, service=mock_svc)

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail["error_code"] == "QPM_CONFIG_NOT_FOUND"


class TestUpsertQpmConfig:
    """Tests for upsert_qpm_config endpoint."""

    @pytest.mark.asyncio
    async def test_upserts_config(self):
        mock_svc = MagicMock()
        item = _make_item(bot_id="new-bot", qpm=200)
        mock_svc.upsert_config.return_value = item

        request = BotQpmUpsertRequest(bot_id="new-bot", qpm=200)

        result = await upsert_qpm_config(
            request=request, op_ctx=_OP_CTX, service=mock_svc
        )

        assert result.code == 0
        assert result.data.bot_id == "new-bot"
        assert result.data.qpm == 200
        mock_svc.upsert_config.assert_called_once_with(bot_id="new-bot", qpm=200)


class TestUpdateQpmConfig:
    """Tests for update_qpm_config endpoint."""

    @pytest.mark.asyncio
    async def test_updates_config(self):
        mock_svc = MagicMock()
        item = _make_item(bot_id="bot-001", qpm=500)
        mock_svc.update_config.return_value = item

        request = BotQpmUpdateRequest(qpm=500)

        result = await update_qpm_config(
            bot_id="bot-001", request=request, op_ctx=_OP_CTX, service=mock_svc
        )

        assert result.code == 0
        assert result.data.qpm == 500
        mock_svc.update_config.assert_called_once_with(bot_id="bot-001", qpm=500)

    @pytest.mark.asyncio
    async def test_raises_404_when_not_found(self):
        mock_svc = MagicMock()
        mock_svc.update_config.return_value = None

        request = BotQpmUpdateRequest(qpm=500)

        with pytest.raises(HTTPException) as exc_info:
            await update_qpm_config(
                bot_id="missing", request=request, op_ctx=_OP_CTX, service=mock_svc
            )

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail["error_code"] == "QPM_CONFIG_NOT_FOUND"


class TestDeleteQpmConfig:
    """Tests for delete_qpm_config endpoint."""

    @pytest.mark.asyncio
    async def test_deletes_config(self):
        mock_svc = MagicMock()
        mock_svc.delete_config.return_value = True

        result = await delete_qpm_config(
            bot_id="bot-001", op_ctx=_OP_CTX, service=mock_svc
        )

        assert result.code == 0
        assert result.data.success is True
        assert result.data.message == "QPM config deleted"
        mock_svc.delete_config.assert_called_once_with("bot-001")

    @pytest.mark.asyncio
    async def test_raises_404_when_not_found(self):
        mock_svc = MagicMock()
        mock_svc.delete_config.return_value = False

        with pytest.raises(HTTPException) as exc_info:
            await delete_qpm_config(bot_id="missing", op_ctx=_OP_CTX, service=mock_svc)

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail["error_code"] == "QPM_CONFIG_NOT_FOUND"
